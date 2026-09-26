from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import uuid4

from pydantic import SecretStr

from app.accounts.resolver import ResolvedTarget, TargetResolver
from app.accounts.validator import validate_account_configuration
from app.history.store import HistoryStore
from app.models.domain import (
    AccountConfig,
    AutomationResult,
    ErrorCategory,
    HistoryRecord,
    HistoryStatus,
    LoginExecutionItem,
    LoginRequest,
    LoginStatus,
    utc_now,
)
from app.models.errors import AppError, DomainValidationError
from app.mt4.interface import MT4Automation
from app.security.redaction import redact_text, sensitive_values

logger = logging.getLogger(__name__)
CompletionCallback = Callable[[list[LoginExecutionItem]], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class JobHandle:
    job_id: str
    target: ResolvedTarget


class LoginService:
    def __init__(
        self,
        resolver: TargetResolver,
        automation: MT4Automation,
        history: HistoryStore,
        *,
        max_jobs: int = 16,
        otp_max_age_seconds: int = 300,
    ) -> None:
        self.resolver = resolver
        self.automation = automation
        self.history = history
        self.max_jobs = max(1, max_jobs)
        self.otp_max_age_seconds = min(900, max(30, otp_max_age_seconds))
        self._tasks: set[asyncio.Task[None]] = set()
        self._task_account_ids: dict[asyncio.Task[None], set[str]] = {}
        self._pending_account_ids: set[str] = set()
        self._run_lock = asyncio.Lock()
        self._running = 0
        self._accepting = True

    def start(self) -> None:
        self._accepting = True

    @staticmethod
    def _otp_age_seconds(request: LoginRequest) -> float:
        if request.received_monotonic is None:  # defensive for legacy callers
            return max(0.0, (utc_now() - request.received_at).total_seconds())
        return max(0.0, time.monotonic() - request.received_monotonic)

    def validate_target(self, target: str) -> ResolvedTarget:
        return self.resolver.resolve(target)

    def can_accept(self, target: str | None = None) -> bool:
        if not self._accepting:
            return False
        if len(self._tasks) >= self.max_jobs:
            return False
        if target is not None:
            try:
                resolved = self.validate_target(target)
            except AppError:
                return False
            if {account.id for account in resolved.accounts} & self._pending_account_ids:
                return False
        return True

    def submit(
        self, request: LoginRequest, callback: CompletionCallback | None = None
    ) -> JobHandle:
        if not self._accepting:
            raise DomainValidationError("Login service is shutting down")
        # Resolve before acknowledgement so unknown aliases can be rejected immediately.
        resolved = self.validate_target(request.target)
        if len(self._tasks) >= self.max_jobs:
            raise DomainValidationError("Login queue is full; wait for active jobs to finish")
        account_ids = {account.id for account in resolved.accounts}
        if account_ids & self._pending_account_ids:
            raise DomainValidationError(
                "One or more target accounts already have a login in progress"
            )
        loop = asyncio.get_running_loop()
        job_id = str(uuid4())
        self._pending_account_ids.update(account_ids)
        try:
            task = loop.create_task(self._run(job_id, request, resolved, callback))
        except Exception:
            self._pending_account_ids.difference_update(account_ids)
            raise
        self._tasks.add(task)
        self._task_account_ids[task] = account_ids
        task.add_done_callback(self._task_done)
        return JobHandle(job_id=job_id, target=resolved)

    def _task_done(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        account_ids = self._task_account_ids.pop(task, set())
        self._pending_account_ids.difference_update(account_ids)
        if task.cancelled():
            return
        try:
            task.exception()
        except Exception as exc:  # pragma: no cover - defensive task boundary
            logger.error("Login task ended unexpectedly (%s)", type(exc).__name__)

    async def _run(
        self,
        job_id: str,
        request: LoginRequest,
        target: ResolvedTarget,
        callback: CompletionCallback | None,
    ) -> None:
        otp_value = request.otp.get_secret_value()
        account_ids = {account.id for account in target.accounts}
        items: list[LoginExecutionItem] = []
        cancelled = False
        processed_ids: set[str] = set()
        try:
            with sensitive_values(otp_value):
                async with self._run_lock:
                    self._running += 1
                    try:
                        for snapshot in target.accounts:
                            if self._otp_age_seconds(request) > self.otp_max_age_seconds:
                                items.append(
                                    await self._execute_account(
                                        request,
                                        snapshot,
                                        None,
                                        otp_value,
                                        missing_category=ErrorCategory.OTP_EXPIRED,
                                        forced_result=AutomationResult(
                                            status=LoginStatus.TIMEOUT,
                                            category=ErrorCategory.OTP_EXPIRED,
                                            message="OTP expired while waiting in the login queue",
                                        ),
                                    )
                                )
                                processed_ids.add(snapshot.id)
                                continue
                            current: AccountConfig | None = None
                            try:
                                live_target = self.resolver.resolve(request.target)
                                if (
                                    live_target.kind == target.kind
                                    and live_target.target_id == target.target_id
                                ):
                                    current = next(
                                        (
                                            item
                                            for item in live_target.accounts
                                            if item.id == snapshot.id
                                        ),
                                        None,
                                    )
                            except AppError:
                                current = None
                            items.append(
                                await self._execute_account(
                                    request,
                                    snapshot,
                                    current,
                                    otp_value,
                                    missing_category=(
                                        ErrorCategory.TARGET_CHANGED
                                        if target.kind == "group"
                                        else ErrorCategory.ACCOUNT_NOT_FOUND
                                    ),
                                )
                            )
                            processed_ids.add(snapshot.id)
                    finally:
                        self._running -= 1
        except asyncio.CancelledError:
            cancelled = True
            for snapshot in target.accounts:
                if snapshot.id in processed_ids:
                    continue
                items.append(
                    await self._execute_account(
                        request,
                        snapshot,
                        None,
                        otp_value,
                        missing_category=ErrorCategory.CANCELLED,
                        forced_result=AutomationResult(
                            status=LoginStatus.TIMEOUT,
                            category=ErrorCategory.CANCELLED,
                            message="Login request was cancelled before this account started",
                        ),
                    )
                )
        finally:
            self._pending_account_ids.difference_update(account_ids)
            # Drop the request's reference as soon as the job is complete.
            request.otp = SecretStr("")
            del otp_value
        if callback is not None:
            try:
                if cancelled:
                    callback_task = asyncio.create_task(callback(items))
                    await asyncio.shield(callback_task)
                else:
                    await callback(items)
            except asyncio.CancelledError:
                logger.error("Completion callback was cancelled")
            except Exception as exc:
                logger.error("Completion callback failed (%s)", type(exc).__name__)

    async def _execute_account(
        self,
        request: LoginRequest,
        snapshot: AccountConfig,
        current: AccountConfig | None,
        otp_value: str,
        *,
        missing_category: ErrorCategory,
        forced_result: AutomationResult | None = None,
    ) -> LoginExecutionItem:
        started = time.monotonic()
        result: AutomationResult
        account = current
        if forced_result is not None:
            result = forced_result
        elif account is None:
            result = AutomationResult(
                status=LoginStatus.FAILED,
                category=missing_category,
                message="Account or target changed before execution; request was not started",
            )
        elif not account.enabled:
            result = AutomationResult(
                status=LoginStatus.FAILED,
                category=ErrorCategory.ACCOUNT_DISABLED,
                message="Account is disabled",
            )
        else:
            validation = validate_account_configuration(account)
            if not validation.valid:
                result = AutomationResult(
                    status=LoginStatus.FAILED,
                    category=ErrorCategory.INVALID_ACCOUNT_CONFIG,
                    message="Account configuration validation failed",
                )
            else:
                try:
                    result = await self.automation.login(account, SecretStr(otp_value))
                except Exception as exc:
                    # Do not persist or return arbitrary exception text.
                    logger.error(
                        "Automation raised %s for account %s", type(exc).__name__, account.alias
                    )
                    result = AutomationResult(
                        status=LoginStatus.FAILED,
                        category=ErrorCategory.INTERNAL_ERROR,
                        message="Login automation failed unexpectedly",
                    )
        duration_ms = max(0, int((time.monotonic() - started) * 1000))
        safe_target = redact_text(request.target, (otp_value,))
        safe_sender = redact_text(request.sender_id, (otp_value,))
        safe_alias = redact_text(account.alias if account else snapshot.alias, (otp_value,))
        message = redact_text(result.message, (otp_value,))
        history = HistoryRecord(
            timestamp=utc_now(),
            sender=safe_sender,
            target=safe_target,
            account_alias=safe_alias,
            status=HistoryStatus.SUCCESS
            if result.status == LoginStatus.SUCCESS
            else HistoryStatus.FAILED,
            error_category=result.category,
            duration_ms=duration_ms,
        )
        history_id: str | None = None
        try:
            history_id = (await asyncio.to_thread(self.history.append, history)).id
        except Exception as exc:
            logger.error("History append failed (%s)", type(exc).__name__)
        return LoginExecutionItem(
            target=safe_target,
            account_id=(account or snapshot).id,
            account_alias=safe_alias,
            status=result.status,
            error_category=result.category,
            message=message[:300],
            verification=redact_text(result.verification, (otp_value,)),
            duration_ms=duration_ms,
            history_id=history_id,
        )

    @property
    def active_job_count(self) -> int:
        return len(self._tasks)

    @property
    def running_job_count(self) -> int:
        return self._running

    @property
    def queued_job_count(self) -> int:
        return max(0, self.active_job_count - self.running_job_count)

    async def shutdown(self) -> None:
        self._accepting = False
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        await self.automation.close()
