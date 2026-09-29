"""Single Test Runner Core shared by the CLI and Web Admin."""

from __future__ import annotations

import asyncio
import json
import os
import platform
import subprocess
import time
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from pydantic import SecretStr

from app.adapters.slack.commands import CommandError
from app.models.domain import (
    _WIN32_FALLBACK_KEYS,  # single source of truth for the control id key space
    AccountPatch,
    LoginRequest,
    LoginStatus,
    Win32FallbackConfig,
)
from app.models.errors import AppError, NotFoundError
from app.runtime import AgentRuntime
from app.security.redaction import redact_text, sensitive_values
from app.testing.environment import run_environment_checks
from app.testing.models import (
    DetectedControl,
    TestCategory,
    TestClass,
    TestPhase,
    TestReport,
    TestResult,
    TestSeverity,
    TestStatus,
    now_utc,
)
from app.testing.mt4_discovery import (
    apply_confirmed_selectors,
    discover_account,
    selector_diff,
)
from app.testing.report import new_report_id, write_report
from app.testing.security import scan_paths, secret_candidates
from app.testing.slack_tests import run_slack_checks


class TestRunner:
    """Orchestrates safe checks and explicit, human-confirmed real actions."""

    __test__ = False

    def __init__(self, runtime: AgentRuntime) -> None:
        self.runtime = runtime
        self.paths = runtime.paths
        self._lock = asyncio.Lock()
        self._results: list[TestResult] = []
        self._detected_controls: list[DetectedControl] = []
        self._detected_account_id: str | None = None
        self._diagnostic: dict[str, Any] = {}
        self._report_id: str | None = None
        self._report_dir: Path | None = None
        self._started_at = now_utc()
        self._completed_phases: set[TestPhase] = set()
        self._phase_status: dict[TestPhase, str] = {}
        self._phase_manual: dict[TestPhase, int] = {}
        self._discovery_route: str = ""
        self._security_scan: dict[str, bool] = {}
        self._slack_sessions: dict[str, tuple[str, Any]] = {}

    @property
    def detected_controls(self) -> list[DetectedControl]:
        return list(self._detected_controls)

    @property
    def report_id(self) -> str | None:
        return self._report_id

    @property
    def report_dir(self) -> Path | None:
        return self._report_dir

    @property
    def windows_available(self) -> bool:
        import sys

        return sys.platform == "win32"

    def status(self) -> dict[str, Any]:
        return {
            "windows_available": self.windows_available,
            "report_id": self._report_id,
            "report_dir": str(self._report_dir) if self._report_dir else None,
            "completed_phases": [phase.value for phase in self._completed_phases],
            "phase_status": {
                phase.value: value for phase, value in self._phase_status.items()
            },
            "phase_manual": {
                phase.value: value for phase, value in self._phase_manual.items()
            },
            "discovery_route": self._discovery_route,
            "results": [item.safe_dict() for item in self._results],
            "detected_controls": [item.model_dump(mode="json") for item in self._detected_controls],
        }

    @staticmethod
    def _verdict(results: list[TestResult]) -> str:
        """Summarise a phase from its real results, not from the fact it ran.

        Only the latest occurrence of each check id counts. Results accumulate for
        the life of the process, so an earlier FAIL for the same check must not keep
        a later PASS looking broken.
        """
        if not results:
            return "not_run"
        latest: dict[str, TestStatus] = {}
        for item in results:
            latest[item.id] = item.status
        statuses = list(latest.values())
        if any(status == TestStatus.FAIL for status in statuses):
            return "fail"
        if any(status == TestStatus.WARN for status in statuses):
            return "warn"
        # MANUAL and NOT_RUN are not degradations. A step that left a checklist or a
        # destructive probe for the human to decide has still done its job, and
        # colouring the whole step amber misreports that as a problem.
        return "pass"

    @staticmethod
    def _manual_count(results: list[TestResult]) -> int:
        """How many checks the human still has to decide, per check id."""
        latest: dict[str, TestStatus] = {}
        for item in results:
            latest[item.id] = item.status
        return sum(1 for status in latest.values() if status == TestStatus.MANUAL)

    def _record_phase(self, phase: TestPhase, results: list[TestResult]) -> None:
        self._completed_phases.add(phase)
        verdict = self._verdict(results)
        if phase is TestPhase.DISCOVERY and self._discovery_route in {"uia", "win32"}:
            # This phase exists to find a route that can drive MT4. A broker whose
            # login form is not exposed to UIA can never satisfy the UIA check, so
            # the warning on the primary route is not a degradation of the step once
            # the opted-in alternative resolved. The individual row keeps its own
            # WARN so nothing is rewritten; only the step summary is decided here.
            ready = next(
                (item for item in results if item.id == "MT4_DISCOVERY_READY"), None
            )
            if ready is not None and ready.status == TestStatus.PASS:
                verdict = "pass"
        self._phase_status[phase] = verdict
        self._phase_manual[phase] = self._manual_count(results)

    def _discovery_usable(self) -> bool:
        """Phase 2 may continue when a reliable control route exists.

        UIA stays primary. An explicitly opted-in Win32 dialog counts as an equally
        reliable route, because a broker whose login form is not exposed to UIA can
        never satisfy the UIA route.
        """
        return self._discovery_route in {"uia", "win32"}

    def _platform_commit(self) -> str:
        try:
            completed = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=Path(__file__).parents[3],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            if completed.returncode == 0:
                return completed.stdout.strip()[:40]
        except Exception:
            pass
        return os.environ.get("MT4_AGENT_GIT_COMMIT", "")[:40]

    def _manual_checklist(self) -> list[TestResult]:
        items = [
            (
                "MANUAL_WRONG_OTP",
                "Intentional wrong OTP",
                "Enter a deliberately invalid OTP only on a disposable test Account.",
            ),
            (
                "MANUAL_OTP_EXPIRY",
                "OTP expiry",
                "Wait for a disposable OTP to expire and confirm the result is otp_expired.",
            ),
            (
                "MANUAL_WORKER_KILL",
                "Worker termination",
                "Use a disposable process and stop the Agent while the worker is running.",
            ),
            (
                "MANUAL_MULTI_INSTANCE",
                "Multiple terminal.exe",
                "Create a second Profile only in a disposable Windows environment.",
            ),
            (
                "MANUAL_SYSTEM_TIME",
                "System clock change",
                "Test clock behavior in a disposable VM only.",
            ),
            (
                "MANUAL_UAC",
                "Elevation change",
                "Test administrator/non-administrator combinations in a disposable VM.",
            ),
            (
                "MANUAL_NETWORK",
                "Network interruption",
                "Test offline/reconnect in a disposable network environment.",
            ),
        ]
        return [
            TestResult(
                id=identifier,
                category=TestCategory.ENVIRONMENT,
                name=name,
                status=TestStatus.MANUAL,
                severity=TestSeverity.HIGH,
                test_class=TestClass.DESTRUCTIVE,
                message="MANUAL_TEST_REQUIRED: not run automatically.",
                technical_detail=steps,
                suggested_action="Follow docs/WINDOWS-MT4-TEST.md and use a "
                "disposable test environment.",
                requires_manual=True,
            )
            for identifier, name, steps in items
        ]

    async def _write(
        self, *, otp: str | None = None, uia_payload: dict[str, Any] | None = None
    ) -> None:
        candidates = secret_candidates(self.runtime.secrets_repository.get(), otp=otp)
        report = TestReport(
            report_id=self._report_id or new_report_id(),
            started_at=self._started_at,
            finished_at=now_utc(),
            platform=self.runtime.platform_name,
            windows_version=self._windows_version(),
            python_version=platform.python_version(),
            python_architecture=platform.machine(),
            agent_version=self.runtime.status().get("version", "unknown"),
            git_commit=self._platform_commit(),
            results=self._results,
            detected_controls=self._detected_controls,
            security_scan=dict(self._security_scan),
        )
        report.overall = report.recompute_overall()
        self._report_id = report.report_id
        self._report_dir = write_report(
            self.paths,
            report,
            candidates,
            uia_payload=uia_payload or (self._diagnostic or None),
        ).parent

    @staticmethod
    def _windows_version() -> str:
        import platform

        try:
            return ".".join(part for part in platform.win32_ver() if part)
        except Exception:
            return platform.release()

    def _add(self, results: list[TestResult]) -> None:
        self._results.extend(results)

    def _manual(self, identifier: str, name: str, message: str, action: str) -> TestResult:
        return TestResult(
            id=identifier,
            category=TestCategory.REAL_LOGIN,
            name=name,
            status=TestStatus.MANUAL,
            severity=TestSeverity.HIGH,
            test_class=TestClass.MANUAL,
            message=message,
            suggested_action=action,
            requires_manual=True,
        )

    async def run_phase(self, phase: TestPhase, account_id: str | None = None) -> list[TestResult]:
        async with self._lock:
            if phase == TestPhase.ENVIRONMENT:
                results = run_environment_checks(self.runtime, self.paths)
                candidates = secret_candidates(self.runtime.secrets_repository.get())
                security_results = scan_paths(
                    self.paths,
                    candidates,
                    extra_paths=[self.paths.dedup_file],
                )
                self._security_scan.update(
                    {item.id: item.status.value == "PASS" for item in security_results}
                )
                results.extend(security_results)
                results.extend(self._manual_checklist())
                self._add(results)
                self._record_phase(phase, results)
                await self._write()
                return results
            if phase == TestPhase.DISCOVERY:
                if account_id is None:
                    result = TestResult(
                        id="MT4_ACCOUNT_REQUIRED",
                        category=TestCategory.MT4_DISCOVERY,
                        name="Account selection",
                        status=TestStatus.FAIL,
                        severity=TestSeverity.MEDIUM,
                        message="An Account must be selected for discovery.",
                        suggested_action="Select an Account and run detection again.",
                    )
                    self._add([result])
                    await self._write()
                    return [result]
                try:
                    account = self.runtime.accounts.get(account_id)
                except NotFoundError:
                    raise
                outcome = await discover_account(account, self.runtime.automation)
                self._add(outcome.results)
                self._detected_controls = outcome.controls
                self._detected_account_id = account.id
                self._diagnostic = outcome.diagnostic
                self._discovery_route = outcome.route
                self._record_phase(phase, outcome.results)
                await self._write(uia_payload=outcome.diagnostic)
                return outcome.results
            if phase == TestPhase.SLACK:
                results = await run_slack_checks(self.runtime)
                self._add(results)
                self._record_phase(phase, results)
                await self._write()
                return results
            if phase in {TestPhase.REAL_LOGIN, TestPhase.GROUP}:
                raise ValueError(f"{phase.value} requires an explicit confirmation method")
            raise ValueError(f"Unknown test phase: {phase}")

    async def run_real_login(
        self,
        account_id: str,
        otp: str,
        *,
        confirmed: bool = False,
        broker_confirmed: bool = False,
        full_slack: bool = False,
    ) -> list[TestResult]:
        async with self._lock:
            if not self.windows_available:
                return [self._not_run_real("REAL_LOGIN_WINDOWS_REQUIRED")]
            if not broker_confirmed:
                return [
                    self._manual(
                        "REAL_LOGIN_BROKER_CONFIRMATION_REQUIRED",
                        "Broker readiness confirmation",
                        "Manually confirm Rakuten is not in a known maintenance/offline window.",
                        "There is no broker preflight; confirm the current broker state "
                        "before OTP testing.",
                    )
                ]
            if full_slack:
                if TestPhase.ENVIRONMENT not in self._completed_phases:
                    return [
                        self._manual(
                            "FULL_SLACK_PHASE1_REQUIRED",
                            "Full Slack E2E",
                            "Run Phase 1 Environment before starting Full Slack E2E.",
                            "Run Phase 1 and review failures first.",
                        )
                    ]
                if TestPhase.DISCOVERY not in self._completed_phases or (
                    not self._discovery_usable()
                ):
                    return [
                        self._manual(
                            "FULL_SLACK_PHASE2_REQUIRED",
                            "Full Slack E2E",
                            "Run Phase 2 MT4 Discovery and confirm it resolved a usable "
                            "control route first.",
                            "Select the Account and run Detect; Real Login needs either "
                            "UIA selectors or an enabled Win32 dialog fallback.",
                        )
                    ]
                account = self.runtime.accounts.get(account_id)
                session_id = f"slack-{uuid4().hex[:12]}"
                self._slack_sessions[session_id] = (account.alias, now_utc())
                result = self._manual(
                    "REAL_LOGIN_FULL_SLACK_MANUAL",
                    "Full Slack E2E",
                    "ACTION REQUIRED: send /mt4 "
                    f"{account.alias} <OTP> yourself; the runner never sends real OTPs "
                    "to Slack.",
                    f"After sending it, use Await with session_id {session_id}.",
                )
                result.evidence = {"session_id": session_id, "account_alias": account.alias}
                self._add([result])
                await self._write()
                return [result]
            if not confirmed:
                return [
                    self._manual(
                        "REAL_LOGIN_CONFIRMATION_REQUIRED",
                        "Real login confirmation",
                        "REAL ACCOUNT ACTION is disabled until the user explicitly confirms.",
                        "Review the Account/Server/Terminal values, then confirm "
                        "Start real login test.",
                    )
                ]
            if TestPhase.ENVIRONMENT not in self._completed_phases or (
                self._phase_status.get(TestPhase.ENVIRONMENT) == "fail"
            ):
                return [
                    self._manual(
                        "REAL_LOGIN_PHASE1_REQUIRED",
                        "Real login confirmation",
                        "Real login is blocked until Phase 1 Environment passes without "
                        "failures.",
                        "Run Phase 1 and fix the failures first.",
                    )
                ]
            if TestPhase.DISCOVERY not in self._completed_phases or (
                not self._discovery_usable()
            ):
                return [
                    self._manual(
                        "REAL_LOGIN_PHASE2_REQUIRED",
                        "Real login confirmation",
                        "Real login is blocked until Phase 2 resolves a usable control "
                        "route for this Account.",
                        "Run Detect first. Real Login needs either resolved UIA selectors "
                        "or an enabled Win32 dialog fallback.",
                    )
                ]
            try:
                account = self.runtime.accounts.get(account_id)
            except NotFoundError:
                result = TestResult(
                    id="REAL_LOGIN_ACCOUNT_NOT_FOUND",
                    category=TestCategory.REAL_LOGIN,
                    name="Real login target",
                    status=TestStatus.FAIL,
                    severity=TestSeverity.HIGH,
                    message="The selected Account was not found.",
                    suggested_action="Select an existing Account.",
                )
                self._add([result])
                await self._write(otp=otp)
                return [result]
            result_items: list[Any] = []
            completed = asyncio.Event()

            async def callback(items):
                result_items.extend(items)
                completed.set()

            try:
                with sensitive_values(otp):
                    self.runtime.login_service.submit(
                        LoginRequest(
                            sender_id="WINDOWS_TEST_RUNNER",
                            target=account.alias,
                            otp=SecretStr(otp),
                            source="windows-test",
                        ),
                        callback,
                    )
                    timeout = account.launch_timeout_seconds + account.login_timeout_seconds + 15
                    try:
                        await asyncio.wait_for(completed.wait(), timeout=timeout)
                    except TimeoutError:
                        result_items.append(
                            SimpleNamespace(
                                account_alias=account.alias,
                                status=LoginStatus.TIMEOUT,
                                error_category=SimpleNamespace(value="timeout"),
                                message="Acceptance test timed out waiting for completion.",
                                duration_ms=0,
                            )
                        )
            except (CommandError, AppError, ValueError) as exc:
                # Report the stage and the exception type only. The message itself can
                # echo an Account or broker value, so it is never included.
                result = TestResult(
                    id="REAL_LOGIN_SUBMIT_FAILED",
                    category=TestCategory.REAL_LOGIN,
                    name="Real login submission",
                    status=TestStatus.FAIL,
                    severity=TestSeverity.HIGH,
                    message="The login request could not be submitted.",
                    technical_detail=(
                        f"stage=queue_submit; error_type={type(exc).__name__}"
                    ),
                    suggested_action=(
                        "Check whether the Account already has a login in progress, "
                        "whether the queue is full, or whether the Agent is shutting down."
                    ),
                )
                self._add([result])
                await self._write(otp=otp)
                return [result]
            results = [self._item_result(item, otp) for item in result_items]
            leak_results = scan_paths(
                self.paths,
                secret_candidates(self.runtime.secrets_repository.get(), otp=otp),
            )
            self._security_scan.update(
                {item.id: item.status.value == "PASS" for item in leak_results}
            )
            results.extend(leak_results)
            self._add(results)
            self._record_phase(TestPhase.REAL_LOGIN, results)
            await self._write(otp=otp, uia_payload=self._diagnostic or None)
            return results

    _WIN32_SUGGESTION_REQUIRED = (
        "dialog_class",
        "anchors",
        "login_id_combo",
        "login_id_edit",
        "otp",
        "server_combo",
        "server_edit",
        "login_button",
    )

    async def inspect_win32(self, account_id: str) -> dict[str, Any]:
        """Read-only inspection of the target's login dialog.

        Proposes a Win32 dialog configuration for a broker or MT4 build that has
        never been adapted. Nothing is written here: the caller shows the suggestion
        and a human applies it. Only control metadata is read, so no credential can
        be exposed by inspecting.
        """
        report: dict[str, Any] = {
            "outcome": "unavailable",
            "detail": "",
            "suggested": {},
            "confidence": {},
            "titles": {},
            "menu_captions": [],
            "appliable": False,
            "missing": list(self._WIN32_SUGGESTION_REQUIRED),
        }
        try:
            account = self.runtime.accounts.get(account_id)
        except NotFoundError:
            report["detail"] = "the Account was not found"
            return report
        if not self.windows_available:
            report["detail"] = "WINDOWS_REAL_TEST_REQUIRED: Win32 inspection requires Windows."
            return report
        pids = self.runtime.automation._process_ids(account)
        try:
            report = self.runtime.automation._win32_inspect(pids, account)
        except Exception as exc:
            report["outcome"] = "error"
            # Never include the exception text: it could echo a control value.
            report["detail"] = f"inspection failed: {type(exc).__name__}"
        confidence = report.get("confidence") or {}
        missing = [key for key in self._WIN32_SUGGESTION_REQUIRED if confidence.get(key) != "HIGH"]
        report["missing"] = missing
        report["appliable"] = not missing
        self._add([self._win32_inspect_result(report)])
        return report

    def _win32_inspect_result(self, report: dict[str, Any]) -> TestResult:
        outcome = str(report.get("outcome") or "unavailable")
        missing = list(report.get("missing") or [])
        if outcome == "inspected" and not missing:
            status, message = (
                TestStatus.PASS,
                "Win32 dialog inspection produced a complete suggestion. Review it, then "
                "apply it to this Account.",
            )
        elif outcome == "inspected":
            status, message = (
                TestStatus.WARN,
                "Win32 dialog inspection found the dialog but could not resolve every "
                "field uniquely. Nothing was guessed; fill the listed fields by hand.",
            )
        else:
            status, message = (
                TestStatus.WARN,
                "Win32 dialog inspection could not inspect a login dialog.",
            )
        return TestResult(
            id="WIN32_DIALOG_INSPECTION",
            category=TestCategory.MT4_DISCOVERY,
            name="Win32 dialog inspection",
            status=status,
            severity=TestSeverity.MEDIUM,
            message=message,
            technical_detail=(
                f"outcome={outcome}; appliable={bool(report.get('appliable'))}; "
                f"unresolved={','.join(missing) if missing else 'none'}"
            ),
            suggested_action=(
                "Open the MT4 login dialog, then run Detect Win32 again."
                if outcome != "inspected"
                else "Apply the suggested settings once the list is empty."
                if not missing
                else "Fill the unresolved fields by hand, or open the dialog and retry."
            ),
            evidence={"suggested": report.get("suggested") or {}},
        )

    async def apply_win32_settings(
        self, account_id: str, suggestion: dict[str, Any]
    ) -> dict[str, Any]:
        """Write only the Account's Win32 fallback fields from a reviewed suggestion.

        Alias, login id, server, terminal path and cwd are never touched, so applying
        a suggestion cannot change which broker account or which MT4 instance the
        Account points at.
        """
        confidence = {str(k): str(v) for k, v in (suggestion.get("confidence") or {}).items()}
        proposed = dict(suggestion.get("suggested") or {})
        missing = [key for key in self._WIN32_SUGGESTION_REQUIRED if confidence.get(key) != "HIGH"]
        if missing:
            return {
                "applied": False,
                "reason": "refusing to apply: " + ", ".join(missing),
            }
        control_ids: dict[str, int] = {}
        for key in _WIN32_FALLBACK_KEYS:
            value = proposed.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or not 0 < value <= 65535:
                return {"applied": False, "reason": f"invalid suggested id for {key}"}
            control_ids[key] = value
        anchors = proposed.get("anchors") or []
        dialog_class = str(proposed.get("dialog_class") or "").strip()
        if not dialog_class or not isinstance(anchors, list) or not anchors:
            return {"applied": False, "reason": "dialog_class and anchors are required"}
        patch = AccountPatch(
            win32_fallback=Win32FallbackConfig(
                enabled=True,
                dialog_class=dialog_class,
                anchors=[str(item) for item in anchors],
                control_ids=control_ids,
            )
        )
        self.runtime.accounts.update(account_id, patch)
        return {"applied": True, "account_id": account_id, "control_ids": control_ids}

    async def await_slack_result(
        self,
        session_id: str,
        *,
        timeout_seconds: int = 120,
    ) -> list[TestResult]:
        session = self._slack_sessions.get(session_id)
        if session is None:
            return [
                self._manual(
                    "SLACK_SESSION_NOT_FOUND",
                    "Full Slack E2E result",
                    "The acceptance session id is unknown or already expired.",
                    "Start a new Full Slack E2E session.",
                )
            ]
        alias, started_at = session
        deadline = time.monotonic() + max(1, timeout_seconds)
        while time.monotonic() < deadline:
            for record in self.runtime.history.recent(100):
                if record.account_alias != alias or record.timestamp < started_at:
                    continue
                result = TestResult(
                    id="SLACK_E2E_RESULT",
                    category=TestCategory.SLACK,
                    name="Full Slack E2E result",
                    status=TestStatus.PASS if record.status.value == "success" else TestStatus.FAIL,
                    severity=TestSeverity.HIGH,
                    test_class=TestClass.REAL_LOGIN,
                    message="Slack result was correlated to the acceptance session.",
                    technical_detail=f"history_id={record.id}",
                    evidence={"account_alias": alias, "session_id": session_id},
                )
                self._add([result])
                await self._write()
                self._slack_sessions.pop(session_id, None)
                return [result]
            await asyncio.sleep(1.0)
        result = self._manual(
            "SLACK_E2E_TIMEOUT",
            "Full Slack E2E result",
            "No matching Slack result was observed before the acceptance timeout.",
            "Confirm the command was sent by an allowlisted user and retry.",
        )
        self._add([result])
        await self._write()
        return [result]

    async def run_group(
        self,
        group_name: str,
        otp: str,
        *,
        confirmed: bool = False,
        broker_confirmed: bool = False,
    ) -> list[TestResult]:
        async with self._lock:
            if not self.windows_available:
                return [self._not_run_real("GROUP_WINDOWS_REQUIRED", TestCategory.GROUP)]
            if not broker_confirmed:
                return [
                    self._manual(
                        "GROUP_BROKER_CONFIRMATION_REQUIRED",
                        "Broker readiness confirmation",
                        "Manually confirm Rakuten is not in a known maintenance/offline window.",
                        "There is no broker preflight; confirm the current broker state "
                        "before Group OTP testing.",
                    )
                ]
            if not confirmed:
                return [
                    self._manual(
                        "GROUP_CONFIRMATION_REQUIRED",
                        "Group login confirmation",
                        "Group login is disabled until the user explicitly confirms.",
                        "Review every Group member, then confirm the safe test Account list.",
                    )
                ]
            if TestPhase.ENVIRONMENT not in self._completed_phases:
                return [
                    self._manual(
                        "GROUP_PHASE1_REQUIRED", "Group login", "Run Phase 1 first.", "Run Phase 1."
                    )
                ]
            result_items: list[Any] = []
            completed = asyncio.Event()

            async def callback(items):
                result_items.extend(items)
                completed.set()

            try:
                with sensitive_values(otp):
                    self.runtime.login_service.submit(
                        LoginRequest(
                            sender_id="WINDOWS_TEST_RUNNER",
                            target=group_name,
                            otp=SecretStr(otp),
                            source="windows-test-group",
                        ),
                        callback,
                    )
                    with suppress(TimeoutError):
                        await asyncio.wait_for(completed.wait(), timeout=900)
                    if not result_items:
                        result_items.append(
                            SimpleNamespace(
                                account_alias=group_name,
                                status=LoginStatus.TIMEOUT,
                                error_category=SimpleNamespace(value="timeout"),
                                message="Acceptance Group test timed out.",
                                duration_ms=0,
                            )
                        )
            except (AppError, ValueError):
                result = TestResult(
                    id="GROUP_SUBMIT_FAILED",
                    category=TestCategory.GROUP,
                    name="Group login submission",
                    status=TestStatus.FAIL,
                    severity=TestSeverity.HIGH,
                    message="The Group request could not be submitted.",
                    suggested_action="Check the Group and Account configuration.",
                )
                self._add([result])
                await self._write(otp=otp)
                return [result]
            results = [
                self._item_result(item, otp, category=TestCategory.GROUP) for item in result_items
            ]
            security_results = scan_paths(
                self.paths,
                secret_candidates(self.runtime.secrets_repository.get(), otp=otp),
            )
            self._security_scan.update(
                {item.id: item.status.value == "PASS" for item in security_results}
            )
            results.extend(security_results)
            self._add(results)
            self._completed_phases.add(TestPhase.GROUP)
            await self._write(otp=otp)
            return results

    def _not_run_real(
        self, identifier: str, category: TestCategory = TestCategory.REAL_LOGIN
    ) -> TestResult:
        return TestResult(
            id=identifier,
            category=category,
            name="Windows real acceptance test",
            status=TestStatus.NOT_RUN,
            severity=TestSeverity.HIGH,
            test_class=TestClass.REAL_LOGIN,
            message="WINDOWS_REAL_TEST_REQUIRED: real login tests require Windows.",
            suggested_action="Run the Test Runner on Windows.",
        )

    @staticmethod
    def _item_result(
        item: Any, otp: str, category: TestCategory = TestCategory.REAL_LOGIN
    ) -> TestResult:
        status = getattr(item, "status", None)
        success = status == LoginStatus.SUCCESS
        message = redact_text(str(getattr(item, "message", "")), (otp,))
        error_category = getattr(getattr(item, "error_category", None), "value", "none")
        return TestResult(
            id=f"{category.value.upper()}_{getattr(item, 'account_alias', 'unknown')}",
            category=category,
            name="Login result",
            status=TestStatus.PASS if success else TestStatus.FAIL,
            severity=TestSeverity.HIGH,
            test_class=TestClass.REAL_LOGIN,
            message=message,
            technical_detail=f"error_category={error_category}",
            suggested_action="Review the sanitized diagnostic and Windows checklist."
            if not success
            else "No further action; the Account reached the configured verification.",
            evidence={"account_alias": getattr(item, "account_alias", "")},
        )

    async def refresh_report(self) -> None:
        await self._write(uia_payload=self._diagnostic or None)

    def apply_selectors(
        self, account_id: str, controls: list[DetectedControl], *, confirmed: bool
    ) -> dict[str, Any]:
        if not confirmed:
            return {"applied": False, "reason": "Explicit confirmation is required."}
        if self._detected_account_id != account_id:
            return {"applied": False, "reason": "Run discovery for this Account first."}
        account = self.runtime.accounts.get(account_id)
        if not controls:
            return {"applied": False, "reason": "No detected selectors were supplied."}
        if account.win32_fallback.enabled:
            return {
                "applied": False,
                "reason": (
                    "This Account drives MT4 through the Win32 dialog fallback, so UIA "
                    "Automation IDs are not applied."
                ),
            }
        detected = apply_confirmed_selectors(account, controls)
        if detected is None:
            return {"applied": False, "reason": "Some required selectors are not HIGH confidence."}
        self.runtime.accounts.update(account_id, AccountPatch(control_ids=detected))
        return {"applied": True, "control_ids": detected, "diff": selector_diff(account, controls)}

    def report_json(self) -> dict[str, Any] | None:
        if self._report_dir is None:
            return None
        return json.loads((self._report_dir / "report.json").read_text(encoding="utf-8"))

    def select_report(self, report_id: str) -> dict[str, Any]:
        if not report_id or any(
            character not in "0123456789-abcdef" for character in report_id.lower()
        ):
            raise NotFoundError("Invalid report id")
        directory = self.paths.reports_dir / report_id
        report_file = directory / "report.json"
        if not report_file.is_file():
            raise NotFoundError("Acceptance report not found")
        self._report_id = report_id
        self._report_dir = directory
        return json.loads(report_file.read_text(encoding="utf-8"))

    def report_path(self, filename: str) -> Path:
        if self._report_dir is None:
            raise NotFoundError("No acceptance report has been generated")
        safe_name = Path(filename).name
        if safe_name not in {
            "report.html",
            "report.json",
            "uia-tree-sanitized.json",
            "logs-sanitized.txt",
        }:
            raise NotFoundError("Unknown report artifact")
        path = self._report_dir / safe_name
        if not path.is_file():
            raise NotFoundError("Report artifact is unavailable")
        return path


__all__ = ["TestRunner"]
