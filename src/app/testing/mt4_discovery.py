"""Safe MT4 process and UIA discovery for the acceptance runner.

This module never types an OTP and never launches a second login implementation.
It only inspects the existing WindowsAutomation adapter and produces sanitized
candidate metadata for a human-confirmed selector decision.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.models.domain import AccountConfig
from app.security.redaction import redact_text
from app.testing.models import (
    DetectedControl,
    TestCategory,
    TestClass,
    TestResult,
    TestSeverity,
    TestStatus,
)

MAX_DIAGNOSTIC_NODES = 400
MAX_DIAGNOSTIC_DEPTH = 4
_REQUIRED_FIELDS = ("login_id", "otp", "server", "login_button")


@dataclass(slots=True)
class DiscoveryOutcome:
    results: list[TestResult] = field(default_factory=list)
    controls: list[DetectedControl] = field(default_factory=list)
    diagnostic: dict[str, Any] = field(default_factory=dict)
    account: AccountConfig | None = None
    missing_fields: list[str] = field(default_factory=list)
    route: str = ""


def _result(
    identifier: str,
    name: str,
    status: TestStatus,
    message: str,
    *,
    detail: str = "",
    action: str = "",
    severity: TestSeverity = TestSeverity.INFO,
    evidence: dict[str, Any] | None = None,
) -> TestResult:
    return TestResult(
        id=identifier,
        category=TestCategory.MT4_DISCOVERY,
        name=name,
        status=status,
        severity=severity,
        test_class=TestClass.SAFE,
        message=message,
        technical_detail=detail,
        suggested_action=action,
        evidence=evidence or {},
    )


def _not_run() -> DiscoveryOutcome:
    return DiscoveryOutcome(
        results=[
            _result(
                "MT4_WINDOWS_REQUIRED",
                "MT4 discovery",
                TestStatus.NOT_RUN,
                "WINDOWS_REAL_TEST_REQUIRED: discovery requires Windows and MT4.",
                action="Run Phase 2 on the Windows acceptance machine.",
                severity=TestSeverity.HIGH,
            )
        ]
    )


def _masked_value(value: str, account: AccountConfig) -> str:
    masked = redact_text(value, (account.login_id,))
    if len(masked) > 512:
        return masked[:512] + "…"
    return masked


def _patterns(control: Any) -> list[str]:
    patterns: list[str] = []
    for attribute, label in (
        ("iface_value", "ValuePattern"),
        ("iface_invoke", "InvokePattern"),
        ("iface_toggle", "TogglePattern"),
        ("iface_selection_item", "SelectionItemPattern"),
        ("iface_expand", "ExpandCollapsePattern"),
    ):
        if getattr(control, attribute, None) is not None:
            patterns.append(label)
    getter = getattr(control, "get_toggle_state", None)
    if callable(getter):
        patterns.append("TogglePattern")
    return sorted(set(patterns))


def _control_fields(metadata: dict[str, Any]) -> dict[str, Any]:
    """Drop the window-title annotation so DetectedControl stays extra-free.

    ``_collect_controls`` attaches ``window_title`` to each row for the login-window
    scan, but DetectedControl forbids extra keys.
    """
    return {key: value for key, value in metadata.items() if key != "window_title"}


def _matches_login_window(account: AccountConfig, title: str) -> bool:
    """Decide whether a discovered window is the login window.

    This mirrors ``WindowsAutomation._find_login_window`` so the diagnostic and the
    real login path cannot disagree. The account's ``window_title_regex`` is the
    single source of truth; the English "login" literal is only a fallback for
    accounts that never configured one, because a broker such as Rakuten titles its
    login dialog "Rakuten MetaTrader 4" with no "login" substring at all.
    """
    pattern = (account.window_title_regex or "").strip()
    if pattern:
        try:
            return bool(re.search(pattern, title))
        except re.error:
            return False
    return "login" in title.casefold()


def _control_metadata(control: Any, account: AccountConfig) -> dict[str, Any]:
    element = getattr(control, "element_info", None)
    control_type = _masked_value(str(getattr(element, "control_type", "") or ""), account)
    name = _masked_value(str(getattr(element, "name", "") or ""), account)
    automation_id = _masked_value(str(getattr(element, "automation_id", "") or ""), account)
    class_name = _masked_value(str(getattr(element, "class_name", "") or ""), account)
    is_password = bool(getattr(element, "is_password", False))
    return {
        "automation_id": automation_id,
        "name": name,
        "control_type": control_type,
        "class_name": class_name,
        "is_password": is_password,
        "patterns": _patterns(control),
    }


def _win32_discovery(adapter: Any, account: AccountConfig, pids: list[int]) -> tuple[Any, bool]:
    """Report whether the opted-in Win32 login dialog and its ids are usable."""
    error = ""
    try:
        dialog = adapter._win32_dialog(pids, account)
    except Exception as exc:
        # A programming or environment fault must stay visible instead of being
        # reported as "no dialog matched".
        dialog = None
        error = f"{type(exc).__name__}: {exc}"
    if dialog is None:
        return (
            _result(
                "WIN32_DIALOG_DISCOVERY",
                "Win32 fallback dialog",
                TestStatus.WARN,
                "No Win32 login dialog satisfied every precondition (pid, dialog class, "
                "window_title_regex, anchor texts, control ids).",
                detail=(
                    f"win32_fallback enabled but the dialog was not usable; {error}"
                    if error
                    else "win32_fallback enabled but the dialog was not usable"
                ),
                action="Open the login dialog, then re-check dialog_class, anchors and ids.",
                severity=TestSeverity.HIGH,
            ),
            False,
        )
    try:
        controls = adapter._win32_controls(dialog, account)
    except Exception:
        controls = {}
    resolved = sorted(key for key, value in controls.items() if value is not None)
    return (
        _result(
            "WIN32_DIALOG_DISCOVERY",
            "Win32 fallback dialog",
            TestStatus.PASS,
            "The Win32 login dialog was located and its configured control ids resolved.",
            detail=",".join(resolved),
            action="Real Login will drive this dialog by native control id.",
            severity=TestSeverity.HIGH,
        ),
        True,
    )


def _collect_controls(
    adapter: Any, account: AccountConfig, pids: list[int]
) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for window in adapter._windows(pids):
        try:
            window_title = _masked_value(adapter._window_text(window), account)
        except Exception:
            continue
        for control_type in ("Edit", "Button", "ComboBox", "CheckBox"):
            for control in adapter._visible_controls(window, control_type):
                metadata = _control_metadata(control, account)
                identity = (
                    metadata["control_type"],
                    metadata["automation_id"],
                    metadata["name"],
                )
                if identity in seen:
                    continue
                seen.add(identity)
                metadata["window_title"] = window_title
                found.append(metadata)
                if len(found) >= MAX_DIAGNOSTIC_NODES:
                    return found
    return found


def _diagnostic_payload(
    adapter: Any,
    account: AccountConfig,
    pids: list[int],
) -> dict[str, Any]:
    windows: list[dict[str, Any]] = []
    for window in adapter._windows(pids):
        try:
            title = _masked_value(adapter._window_text(window), account)
            key = adapter._window_key(window)
        except Exception:
            continue
        controls: list[dict[str, Any]] = []
        for control_type in ("Edit", "Button", "ComboBox", "CheckBox"):
            for control in adapter._visible_controls(window, control_type):
                controls.append(_control_metadata(control, account))
                if len(controls) >= MAX_DIAGNOSTIC_NODES:
                    break
            if len(controls) >= MAX_DIAGNOSTIC_NODES:
                break
        windows.append(
            {
                "title": title,
                "window_key": key,
                "controls": controls[:MAX_DIAGNOSTIC_NODES],
            }
        )
        if len(windows) >= 50:
            break
    return {
        "account_alias": account.alias,
        "process_ids": pids,
        "max_depth": MAX_DIAGNOSTIC_DEPTH,
        "max_nodes": MAX_DIAGNOSTIC_NODES,
        "windows": windows,
        "note": "No control values were read; Login ID text is redacted.",
    }


def _detect_field(field_name: str, controls: list[dict[str, Any]]) -> DetectedControl | None:
    configured = ""
    candidates: list[dict[str, Any]] = []
    for item in controls:
        if item["control_type"] == field_name_to_control_type(field_name):
            candidates.append(item)
    if not candidates:
        return None
    exact = [
        item for item in candidates if item["automation_id"] and item["automation_id"] == configured
    ]
    if len(exact) == 1:
        item = exact[0]
        return DetectedControl(
            field=field_name,
            **item,
            confidence="HIGH",
        )
    item = candidates[0]
    return DetectedControl(
        field=field_name,
        **item,
        confidence="NEEDS_CONFIRMATION",
    )


def field_name_to_control_type(field_name: str) -> str:
    return {
        "login_id": "Edit",
        "otp": "Edit",
        "server": "ComboBox",
        "login_button": "Button",
        "save_login": "CheckBox",
    }.get(field_name, "Edit")


async def discover_account(account: AccountConfig, adapter: Any) -> DiscoveryOutcome:
    import sys

    if sys.platform != "win32":
        return _not_run()
    outcome = DiscoveryOutcome(account=account)
    started = time.monotonic()
    terminal = Path(str(account.terminal_path)).expanduser()
    if not terminal.is_file():
        outcome.results.append(
            _result(
                "MT4_TERMINAL_PATH",
                "MT4 executable exists",
                TestStatus.FAIL,
                "Configured terminal.exe was not found.",
                detail="Path is intentionally omitted from the report.",
                action="Correct the Account terminal path.",
                severity=TestSeverity.CRITICAL,
            )
        )
        return outcome
    outcome.results.append(
        _result(
            "MT4_TERMINAL_PATH",
            "MT4 executable exists",
            TestStatus.PASS,
            "Configured terminal.exe exists.",
            evidence={"file_name": terminal.name},
            severity=TestSeverity.HIGH,
        )
    )
    try:
        pids = adapter._process_ids(account)
    except Exception as exc:
        outcome.results.append(
            _result(
                "MT4_PROCESS_RESOLUTION",
                "MT4 process resolution",
                TestStatus.FAIL,
                "MT4 process could not be resolved safely.",
                detail=type(exc).__name__,
                action="Resolve the Profile/process ambiguity before running a login.",
                severity=TestSeverity.CRITICAL,
            )
        )
        return outcome
    if len(pids) > 1:
        outcome.results.append(
            _result(
                "MT4_PROCESS_RESOLUTION",
                "MT4 process resolution",
                TestStatus.FAIL,
                "Multiple MT4 instances matched this Account.",
                detail=f"matched_processes={len(pids)}",
                action="Configure a unique Profile path.",
                severity=TestSeverity.CRITICAL,
            )
        )
    elif not pids:
        outcome.results.append(
            _result(
                "MT4_PROCESS_RESOLUTION",
                "MT4 process resolution",
                TestStatus.WARN,
                "No running MT4 process matched; discovery cannot inspect a login dialog yet.",
                action="Start the target MT4 or run discovery again after launch.",
                severity=TestSeverity.HIGH,
            )
        )
    else:
        outcome.results.append(
            _result(
                "MT4_PROCESS_RESOLUTION",
                "MT4 process resolution",
                TestStatus.PASS,
                "Exactly one MT4 process matched this Account.",
                evidence={"process_count": 1},
                severity=TestSeverity.HIGH,
            )
        )
    if not pids:
        outcome.diagnostic = {"account_alias": account.alias, "process_ids": [], "windows": []}
        return outcome
    try:
        raw_controls = _collect_controls(adapter, account, pids)
        outcome.diagnostic = _diagnostic_payload(adapter, account, pids)
    except Exception as exc:
        outcome.results.append(
            _result(
                "UIA_INSPECTION",
                "UIA inspection",
                TestStatus.FAIL,
                "UIA inspection failed.",
                detail=type(exc).__name__,
                action="Check the sanitized diagnostic and Windows UIA prerequisites.",
                severity=TestSeverity.CRITICAL,
            )
        )
        return outcome
    configured_ids = account.control_ids
    for field_name in _REQUIRED_FIELDS:
        configured = str(configured_ids.get(field_name) or "")
        exact = [
            item
            for item in raw_controls
            if configured
            and item["automation_id"] == configured
            and item["control_type"] == field_name_to_control_type(field_name)
        ]
        if len(exact) == 1:
            outcome.controls.append(
                DetectedControl(
                    field=field_name, **_control_fields(exact[0]), confidence="HIGH"
                )
            )
        else:
            candidates = [
                item
                for item in raw_controls
                if item["control_type"] == field_name_to_control_type(field_name)
            ]
            if len(candidates) == 1:
                outcome.controls.append(
                    DetectedControl(
                        field=field_name,
                        **_control_fields(candidates[0]),
                        confidence="NEEDS_CONFIRMATION",
                    )
                )
            else:
                # Nothing was actually found for this field. Emitting a placeholder
                # row here made the UI look like the control had been detected, so the
                # field is reported as missing instead.
                outcome.missing_fields.append(field_name)
    uia_ready = bool(outcome.controls) and not outcome.missing_fields and not any(
        item.confidence != "HIGH" for item in outcome.controls
    )
    unresolved = [
        item.field for item in outcome.controls if item.confidence != "HIGH"
    ] + outcome.missing_fields
    outcome.results.append(
        _result(
            "UIA_CONTROL_DISCOVERY",
            "UIA control discovery",
            TestStatus.PASS if not unresolved else TestStatus.WARN,
            "UIA controls were discovered."
            if not unresolved
            else "UI Automation did not resolve every login control.",
            detail=",".join(unresolved),
            action=(
                "UIA is not the route for this Account; the Win32 dialog fallback "
                "addresses the same dialog by native control id."
                if account.win32_fallback.enabled
                else "Open the MT4 login dialog and run Detect again; review candidates "
                "and apply selectors explicitly."
            ),
            severity=TestSeverity.HIGH,
            evidence={"control_count": len(outcome.controls), "needs_confirmation": unresolved},
        )
    )
    login_window_seen = any(
        _matches_login_window(account, str(item.get("window_title") or ""))
        for item in raw_controls
    )
    outcome.results.append(
        _result(
            "UIA_LOGIN_WINDOW",
            "Login dialog discovery",
            TestStatus.PASS if login_window_seen else TestStatus.WARN,
            "A window matching the Account window_title_regex was found."
            if login_window_seen
            else "No window matching the Account window_title_regex was found; "
            "the Account may need MT4 launched first.",
            action="Open the MT4 login dialog and repeat discovery.",
            severity=TestSeverity.HIGH,
        )
    )
    win32_dialog_found = False
    if account.win32_fallback.enabled:
        win32_result, win32_dialog_found = _win32_discovery(adapter, account, pids)
        outcome.results.append(win32_result)
    outcome.route = "win32" if win32_dialog_found else ("uia" if uia_ready else "")
    outcome.results.append(
        _result(
            "MT4_DISCOVERY_READY",
            "Discovery readiness",
            TestStatus.PASS if outcome.route else TestStatus.WARN,
            "A reliable control route is available; Real Login can continue."
            if outcome.route
            else "No reliable control route was resolved; Real Login stays blocked.",
            detail=f"route={outcome.route or 'none'}",
            action=(
                "Proceed to Real Login through the Win32 dialog."
                if outcome.route == "win32"
                else "Proceed to Real Login with the UIA selectors."
                if outcome.route == "uia"
                else "Open the MT4 login dialog, then run Detect again."
            ),
            severity=TestSeverity.HIGH,
            evidence={
                "route": outcome.route or "none",
                "missing_fields": outcome.missing_fields,
            },
        )
    )
    if win32_dialog_found and not login_window_seen:
        outcome.results.append(
            _result(
                "UIA_LOGIN_WINDOW",
                "Login dialog discovery",
                TestStatus.PASS,
                "The login dialog is not exposed through UI Automation; it was "
                "located through the configured Win32 fallback.",
                detail="win32_fallback",
                action="Continue with Real Login; no UIA selectors are required.",
                severity=TestSeverity.HIGH,
            )
        )
    duration = int((time.monotonic() - started) * 1000)
    outcome.results = [
        item.model_copy(update={"duration_ms": duration}) for item in outcome.results
    ]
    return outcome


def selector_diff(
    account: AccountConfig, controls: list[DetectedControl]
) -> dict[str, dict[str, str | None]]:
    detected = {item.field: item.automation_id or None for item in controls}
    current = {field: account.control_ids.get(field) for field in _REQUIRED_FIELDS}
    return {
        field: {"current": current.get(field), "detected": detected.get(field)}
        for field in _REQUIRED_FIELDS
    }


def apply_confirmed_selectors(
    account: AccountConfig, controls: list[DetectedControl]
) -> dict[str, str | None] | None:
    high = {item.field: item.automation_id for item in controls if item.confidence == "HIGH"}
    if set(high) != set(_REQUIRED_FIELDS) or any(not high[field] for field in _REQUIRED_FIELDS):
        return None
    return high


__all__ = [
    "DiscoveryOutcome",
    "apply_confirmed_selectors",
    "discover_account",
    "selector_diff",
]
