from __future__ import annotations

import asyncio
import hmac
import logging
import os
import platform
import secrets as secrets_module
import sys
import threading
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from app.accounts.groups import GroupService
from app.accounts.resolver import TargetResolver
from app.accounts.service import AccountService
from app.accounts.validator import validate_account_configuration
from app.adapters.slack.gateway import SlackGateway
from app.config.paths import AppPaths
from app.config.repositories import (
    AccountRepository,
    GroupRepository,
    SecretsRepository,
    SettingsRepository,
)
from app.config.service import SettingsService
from app.history.store import HistoryStore
from app.models.domain import RuntimeSnapshot
from app.models.errors import ConfigurationError
from app.mt4.factory import build_automation
from app.mt4.login_service import LoginService

logger = logging.getLogger(__name__)


class InstanceLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle: Any | None = None

    def acquire(self) -> None:
        if self._handle is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        with suppress(OSError):
            os.chmod(self.path, 0o600)
        try:
            handle.seek(0, 2)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise ConfigurationError(
                "Another Agent instance is already using this data directory"
            ) from exc
        self._handle = handle

    def release(self) -> None:
        handle = self._handle
        self._handle = None
        if handle is None:
            return
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            handle.close()


class AgentRuntime:
    def __init__(self, paths: AppPaths) -> None:
        self.paths = paths
        self._instance_lock = InstanceLock(paths.instance_file)
        self._instance_lock.acquire()
        self.catalog_lock = threading.RLock()
        self.settings_repository = SettingsRepository(paths)
        self.secrets_repository = SecretsRepository(paths)
        self.account_repository = AccountRepository(paths, lock=self.catalog_lock)
        self.group_repository = GroupRepository(paths, lock=self.catalog_lock)
        self.settings = SettingsService(self.settings_repository, self.secrets_repository)
        self.accounts = AccountService(
            self.account_repository, self.group_repository, self.catalog_lock
        )
        self.groups = GroupService(
            self.account_repository, self.group_repository, self.catalog_lock
        )
        self.resolver = TargetResolver(self.account_repository, self.groups, self.catalog_lock)
        self.history = HistoryStore(paths.history_file)
        self.automation = build_automation(self.settings.get())
        self.login_service = LoginService(
            self.resolver,
            self.automation,
            self.history,
            otp_max_age_seconds=self.settings.get().otp_max_age_seconds,
        )
        self.dedup_key = self.ensure_dedup_key()
        self.slack = SlackGateway(
            self.settings,
            self.login_service,
            self.status,
            dedup_key=self.dedup_key,
            dedup_state_path=paths.dedup_file,
        )
        self.started_at = datetime.now(UTC)
        self._started = False
        self._stopped = False
        self._test_runner: Any | None = None

    @property
    def test_runner(self):
        if self._test_runner is None:
            from app.testing.runner import TestRunner

            self._test_runner = TestRunner(self)
        return self._test_runner

    def __del__(self) -> None:
        with suppress(Exception):
            self._instance_lock.release()

    @property
    def settings_value(self):
        return self.settings.get()

    def ensure_web_admin_token(self) -> str:
        current = self.secrets_repository.get()
        if current.web_admin_token_configured:
            return current.web_admin_token.get_secret_value()  # type: ignore[union-attr]
        token = secrets_module.token_urlsafe(32)
        self.secrets_repository.set(
            current.model_copy(update={"web_admin_token": SecretStr(token)})
        )
        return token

    def ensure_dedup_key(self) -> bytes:
        current = self.secrets_repository.get()
        if current.dedup_hmac_key:
            try:
                candidate = bytes.fromhex(current.dedup_hmac_key.get_secret_value())
                if len(candidate) >= 32:
                    return candidate
            except ValueError:
                pass
        key = secrets_module.token_bytes(32)
        self.secrets_repository.set(
            current.model_copy(update={"dedup_hmac_key": SecretStr(key.hex())})
        )
        return key

    def web_admin_token_matches(self, candidate: str | None) -> bool:
        expected = self.secrets_repository.get().web_admin_token
        if expected is None or not candidate:
            return False
        return hmac.compare_digest(
            expected.get_secret_value().encode("utf-8"),
            candidate.encode("utf-8"),
        )

    async def start(self) -> None:
        if self._started:
            return
        self._instance_lock.acquire()
        self._stopped = False
        self.login_service.start()
        self.started_at = datetime.now(UTC)
        await self.slack.start()
        self._started = True
        logger.info("Agent started on %s", self.platform_name)

    async def stop(self) -> None:
        if self._stopped:
            return
        try:
            await self.slack.stop()
        finally:
            try:
                await self.login_service.shutdown()
            finally:
                self._stopped = True
                self._started = False
                self._instance_lock.release()
                logger.info("Agent stopped")

    @property
    def platform_name(self) -> str:
        if sys.platform == "win32":
            return "Windows"
        if sys.platform == "darwin":
            return "macOS"
        if sys.platform.startswith("linux"):
            return "Linux"
        return sys.platform

    def status(self) -> dict[str, Any]:
        slack_state, connected, last_error = self.slack.public_status()
        return {
            "agent_status": "running" if self._started else "starting",
            "version": "1.0.0",
            "platform": self.platform_name,
            "python_version": platform.python_version(),
            "automation_mode": self.automation.mode,
            "slack_connected": connected,
            "slack_state": slack_state,
            "slack_last_error": last_error,
            "started_at": self.started_at.isoformat(),
            "active_login_jobs": self.login_service.active_job_count,
            "queued_login_jobs": self.login_service.queued_job_count,
            "account_count": len(self.account_repository.all()),
            "data_dir": str(self.paths.data_dir),
        }

    def snapshot(self) -> RuntimeSnapshot:
        return RuntimeSnapshot.model_validate(self.status())

    async def dashboard(self) -> dict[str, Any]:
        async def status_for(account):
            validation = validate_account_configuration(account)
            try:
                runtime_status = await self.automation.account_status(account)
                runtime_status.configuration_valid = validation.valid
                runtime_status.issues = [
                    item.detail for item in validation.checks if not item.passed
                ]
                return runtime_status
            except Exception as exc:
                # Keep Web Admin available even if a platform adapter cannot inspect a process.
                logger.error("Account status failed (%s)", type(exc).__name__)
                return {
                    "account_id": account.id,
                    "alias": account.alias,
                    "enabled": account.enabled,
                    "configuration_valid": validation.valid,
                    "process_state": "unavailable",
                    "issues": [item.detail for item in validation.checks if not item.passed],
                }

        statuses = await asyncio.gather(*(status_for(account) for account in self.accounts.list()))
        return {
            "runtime": self.status(),
            "accounts": statuses,
            "history": [item.model_dump(mode="json") for item in self.history.recent(20)],
        }


def build_runtime(data_dir: str | Path | None = None) -> AgentRuntime:
    paths = AppPaths.from_value(data_dir)
    paths.ensure()
    return AgentRuntime(paths)
