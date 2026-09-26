"""Phase 1 environment and safe configuration checks."""

from __future__ import annotations

import os
import platform
import sys
import time
from pathlib import Path
from typing import Any

from app.accounts.validator import validate_account_configuration
from app.config.paths import AppPaths
from app.runtime import AgentRuntime
from app.testing.models import TestCategory, TestClass, TestResult, TestSeverity, TestStatus


def mask_path(path: Path | str) -> str:
    text = str(path)
    if os.name == "nt":
        parts = Path(text).parts
        if len(parts) > 2:
            return (
                str(Path(parts[0]) / parts[1] / "<data-dir>" / Path(*parts[3:]))
                if len(parts) > 3
                else "<data-dir>"
            )
        return "<data-dir>"
    return "<data-dir>"


def _result(
    identifier: str,
    name: str,
    status: TestStatus,
    message: str,
    *,
    detail: str = "",
    action: str = "",
    severity: TestSeverity = TestSeverity.INFO,
    test_class: TestClass = TestClass.SAFE,
    evidence: dict[str, Any] | None = None,
) -> TestResult:
    return TestResult(
        id=identifier,
        category=TestCategory.ENVIRONMENT,
        name=name,
        status=status,
        severity=severity,
        test_class=test_class,
        message=message,
        technical_detail=detail,
        suggested_action=action,
        evidence=evidence or {},
    )


def _windows_guard_result() -> list[TestResult]:
    return [
        _result(
            "ENV_WINDOWS_REQUIRED",
            "Windows platform required",
            TestStatus.NOT_RUN,
            "WINDOWS_REAL_TEST_REQUIRED: Windows-only checks were not run.",
            detail="sys.platform is not win32.",
            action="Run the same Test Runner on Windows.",
            severity=TestSeverity.HIGH,
        )
    ]


def _safe_filesystem_result(paths: AppPaths) -> list[TestResult]:
    results: list[TestResult] = []
    exists = paths.data_dir.is_dir()
    results.append(
        _result(
            "ENV_DATA_DIR",
            "Runtime data directory",
            TestStatus.PASS if exists else TestStatus.FAIL,
            "Data directory is available." if exists else "Data directory is missing.",
            detail=mask_path(paths.data_dir),
            action="Run install.bat and check permissions.",
            severity=TestSeverity.MEDIUM,
        )
    )
    try:
        probe = paths.data_dir / ".acceptance-write-probe"
        probe.write_text("probe", encoding="utf-8")
        probe.unlink(missing_ok=True)
        writable = True
    except OSError as exc:
        writable = False
        detail = type(exc).__name__
    else:
        detail = ""
    results.append(
        _result(
            "ENV_DATA_WRITABLE",
            "Runtime data is writable",
            TestStatus.PASS if writable else TestStatus.FAIL,
            "Runtime data is writable." if writable else "Runtime data is not writable.",
            detail=detail,
            action="Fix permissions on the runtime data directory.",
            severity=TestSeverity.HIGH,
        )
    )
    results.append(
        _result(
            "ENV_LOG_DIR",
            "Log directory",
            TestStatus.PASS if paths.log_dir.is_dir() else TestStatus.FAIL,
            "Log directory is available."
            if paths.log_dir.is_dir()
            else "Log directory is missing.",
            detail=mask_path(paths.log_dir),
            action="Check the runtime data directory and logging configuration.",
            severity=TestSeverity.MEDIUM,
        )
    )
    return results


def _config_results(runtime: AgentRuntime) -> list[TestResult]:
    results: list[TestResult] = []
    try:
        settings = runtime.settings.get()
        results.append(
            _result(
                "ENV_RUNTIME_CONFIG",
                "Runtime configuration loads",
                TestStatus.PASS,
                "Runtime settings load successfully.",
                detail=f"automation_mode={settings.automation_mode}",
                severity=TestSeverity.MEDIUM,
            )
        )
    except Exception as exc:
        results.append(
            _result(
                "ENV_RUNTIME_CONFIG",
                "Runtime configuration loads",
                TestStatus.FAIL,
                "Runtime settings could not be loaded.",
                detail=type(exc).__name__,
                action="Restore settings.json from a backup.",
                severity=TestSeverity.HIGH,
            )
        )
    try:
        secrets = runtime.secrets_repository.get()
    except Exception as exc:
        results.append(
            _result(
                "ENV_SECRETS_FILE",
                "Secrets file loads",
                TestStatus.FAIL,
                "Secrets configuration could not be loaded.",
                detail=type(exc).__name__,
                action="Restore secrets.json or regenerate it in Web Admin.",
                severity=TestSeverity.CRITICAL,
            )
        )
        return results
    admin_configured = bool(secrets.web_admin_token_configured)
    results.append(
        _result(
            "ENV_ADMIN_TOKEN",
            "Local admin token configured",
            TestStatus.PASS if admin_configured else TestStatus.FAIL,
            "Local admin token is configured."
            if admin_configured
            else "Local admin token is missing.",
            detail="Token value is never recorded.",
            action="Restart the Agent or create the token through Web Admin.",
            severity=TestSeverity.HIGH,
        )
    )
    return results


def _account_results(runtime: AgentRuntime) -> list[TestResult]:
    results: list[TestResult] = []
    try:
        accounts = runtime.accounts.list()
    except Exception as exc:
        return [
            _result(
                "ENV_ACCOUNTS_FILE",
                "Account catalog loads",
                TestStatus.FAIL,
                "The Account catalog could not be loaded.",
                detail=type(exc).__name__,
                action="Restore accounts.json or fix its JSON structure.",
                severity=TestSeverity.CRITICAL,
            )
        ]
    if not accounts:
        return [
            _result(
                "ENV_ACCOUNTS",
                "At least one Account configured",
                TestStatus.WARN,
                "No Account is configured yet.",
                action="Add an Account in Web Admin before Phase 2.",
                severity=TestSeverity.MEDIUM,
            )
        ]
    invalid = []
    for account in accounts:
        validation = validate_account_configuration(account)
        if not validation.valid:
            invalid.append(account.alias)
    results.append(
        _result(
            "ENV_ACCOUNTS",
            "Account configuration integrity",
            TestStatus.PASS if not invalid else TestStatus.FAIL,
            "Configured Accounts pass static validation."
            if not invalid
            else "One or more Accounts fail static validation.",
            detail=",".join(invalid) if invalid else "",
            action="Fix the listed Account checks in Web Admin.",
            severity=TestSeverity.HIGH,
        )
    )
    if sys.platform == "win32":
        missing_terminals = [
            account.alias
            for account in accounts
            if not Path(account.terminal_path).expanduser().is_file()
        ]
        results.append(
            _result(
                "ENV_TERMINAL_PATHS",
                "MT4 terminal paths",
                TestStatus.PASS if not missing_terminals else TestStatus.FAIL,
                "Configured terminal paths exist."
                if not missing_terminals
                else "One or more configured terminal paths are missing.",
                detail=",".join(missing_terminals),
                action="Correct the Account terminal paths before discovery.",
                severity=TestSeverity.HIGH,
            )
        )
    return results


def run_environment_checks(runtime: AgentRuntime, paths: AppPaths) -> list[TestResult]:
    """Run only SAFE checks; never launches MT4, sends Slack, or accepts OTP."""
    started = time.monotonic()
    results: list[TestResult] = []
    results.extend(_safe_filesystem_result(paths))
    results.extend(_config_results(runtime))
    results.extend(_account_results(runtime))
    results.append(
        _result(
            "ENV_PYTHON",
            "Python runtime",
            TestStatus.PASS,
            "Python interpreter is available.",
            detail=f"{platform.python_version()} ({platform.machine()})",
            severity=TestSeverity.LOW,
        )
    )
    if sys.platform != "win32":
        results.extend(_windows_guard_result())
        return [_with_duration(result, started) for result in results]

    lock_exists = paths.instance_file.exists()
    results.append(
        _result(
            "ENV_INSTANCE_LOCK",
            "Single-instance lock",
            TestStatus.PASS if lock_exists else TestStatus.WARN,
            "Instance lock file is present."
            if lock_exists
            else "Instance lock file is not present yet.",
            detail="The runner never deletes a live lock.",
            action="Start the Agent once and confirm the second-instance guard.",
            severity=TestSeverity.MEDIUM,
        )
    )
    local_app_data = os.environ.get("LOCALAPPDATA")
    results.append(
        _result(
            "ENV_LOCALAPPDATA",
            "Windows LOCALAPPDATA",
            TestStatus.PASS if local_app_data else TestStatus.FAIL,
            "LOCALAPPDATA is available." if local_app_data else "LOCALAPPDATA is missing.",
            detail="<runtime-data>" if local_app_data else "",
            action="Run the Agent from the intended Windows user account.",
            severity=TestSeverity.MEDIUM,
        )
    )
    try:
        port = runtime.settings.get().web_port
    except Exception:
        port = 0
    results.append(
        _result(
            "ENV_WEB_PORT",
            "Local Web Admin port",
            TestStatus.PASS if port > 0 else TestStatus.FAIL,
            "Local Web Admin port is configured."
            if port > 0
            else "Local Web Admin port is invalid.",
            detail=f"port={port}" if port else "",
            action="Check settings.json web_port.",
            severity=TestSeverity.MEDIUM,
        )
    )
    try:
        version = platform.win32_ver()
        windows_version = ".".join(part for part in version if part)
    except Exception:
        windows_version = platform.release()
    results.append(
        _result(
            "ENV_WINDOWS",
            "Windows version",
            TestStatus.PASS,
            "Windows platform detected.",
            detail=windows_version,
            severity=TestSeverity.MEDIUM,
        )
    )
    try:
        import psutil  # noqa: F401
        import pywinauto  # type: ignore[import-not-found]
        from pywinauto import Desktop  # type: ignore[import-not-found]
    except Exception as exc:
        results.append(
            _result(
                "ENV_PYWINAUTO",
                "pywinauto and process dependencies",
                TestStatus.FAIL,
                "Windows automation dependencies could not be imported.",
                detail=type(exc).__name__,
                action="Run install.bat and inspect the dependency error.",
                severity=TestSeverity.CRITICAL,
            )
        )
    else:
        uia_available = getattr(pywinauto, "UIA_support", None) is not False
        results.append(
            _result(
                "ENV_PYWINAUTO",
                "pywinauto and process dependencies",
                TestStatus.PASS if uia_available else TestStatus.FAIL,
                "pywinauto UIA support is available."
                if uia_available
                else "pywinauto reports that UIA support is unavailable.",
                detail="No provider or credential data is recorded.",
                action="Install comtypes and run install.bat again.",
                severity=TestSeverity.CRITICAL,
            )
        )
        try:
            if hasattr(sys, "coinit_flags"):
                sys.coinit_flags = 2
            Desktop(backend="uia")
        except Exception as exc:
            results.append(
                _result(
                    "ENV_UIA_BACKEND",
                    "UIA backend self-check",
                    TestStatus.FAIL,
                    "UIA backend could not be initialized.",
                    detail=type(exc).__name__,
                    action="Run on a Windows desktop with COM/UIA available.",
                    severity=TestSeverity.CRITICAL,
                )
            )
        else:
            results.append(
                _result(
                    "ENV_UIA_BACKEND",
                    "UIA backend self-check",
                    TestStatus.PASS,
                    "UIA backend initialized successfully.",
                    severity=TestSeverity.HIGH,
                )
            )
    return [_with_duration(result, started) for result in results]


def _with_duration(result: TestResult, started: float) -> TestResult:
    return result.model_copy(update={"duration_ms": int((time.monotonic() - started) * 1000)})


__all__ = ["mask_path", "run_environment_checks"]
