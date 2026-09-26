"""Safe Slack acceptance checks. This module never posts test messages."""

from __future__ import annotations

import time

from app.adapters.slack.commands import CommandError, parse_slash_command
from app.adapters.slack.dedup import DuplicateGuard, make_event_key
from app.models.errors import AppError
from app.runtime import AgentRuntime
from app.testing.models import TestCategory, TestClass, TestResult, TestSeverity, TestStatus


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
) -> TestResult:
    return TestResult(
        id=identifier,
        category=TestCategory.SLACK,
        name=name,
        status=status,
        severity=severity,
        test_class=test_class,
        message=message,
        technical_detail=detail,
        suggested_action=action,
    )


async def run_slack_checks(runtime: AgentRuntime) -> list[TestResult]:
    started = time.monotonic()
    results: list[TestResult] = []
    public = runtime.settings.public_slack_settings()
    results.append(
        _result(
            "SLACK_APP_TOKEN",
            "App-level token configured",
            TestStatus.PASS if public.app_token_configured else TestStatus.FAIL,
            "App-level token is configured."
            if public.app_token_configured
            else "App-level token is missing.",
            action="Create a Socket Mode App-level token and save it in Web Admin.",
            severity=TestSeverity.HIGH,
        )
    )
    results.append(
        _result(
            "SLACK_BOT_TOKEN",
            "Bot token configured",
            TestStatus.PASS if public.bot_token_configured else TestStatus.FAIL,
            "Bot token is configured." if public.bot_token_configured else "Bot token is missing.",
            action="Install the Slack app and save its Bot token.",
            severity=TestSeverity.HIGH,
        )
    )
    state, connected, last_error = runtime.slack.public_status()
    results.append(
        _result(
            "SLACK_SOCKET_CONNECTED",
            "Socket Mode connection",
            TestStatus.PASS if connected else TestStatus.FAIL,
            "Socket Mode is connected." if connected else "Socket Mode is not connected.",
            detail=(last_error or state or "")[:300],
            action="Check Socket Mode, tokens, network, and allowlist.",
            severity=TestSeverity.HIGH,
        )
    )
    try:
        identity = await runtime.slack.test_connection()
        identity_ok = bool(identity.get("ok")) or bool(identity.get("team"))
        results.append(
            _result(
                "SLACK_BOT_IDENTITY",
                "Bot identity / auth test",
                TestStatus.PASS if identity_ok else TestStatus.FAIL,
                "Slack auth test succeeded." if identity_ok else "Slack auth test did not succeed.",
                detail=str(identity.get("message") or identity.get("state") or "")[:300],
                action="Check the Workspace, Bot installation, and token prefixes.",
                severity=TestSeverity.MEDIUM,
            )
        )
    except Exception as exc:
        results.append(
            _result(
                "SLACK_BOT_IDENTITY",
                "Bot identity / auth test",
                TestStatus.FAIL,
                "Slack auth test raised an unexpected error.",
                detail=type(exc).__name__,
                action="Check the sanitized Agent log and Network settings.",
                severity=TestSeverity.MEDIUM,
            )
        )
    allowlist = list(public.allowed_slack_user_ids)
    results.append(
        _result(
            "SLACK_ALLOWLIST",
            "Slack User allowlist",
            TestStatus.PASS if allowlist else TestStatus.FAIL,
            "At least one Slack User ID is allowed." if allowlist else "The allowlist is empty.",
            detail=f"allowed_count={len(allowlist)}",
            action="Add your Slack Member ID in Web Admin → Slack.",
            severity=TestSeverity.HIGH,
        )
    )
    malformed = False
    try:
        parse_slash_command("A not-an-otp")
    except CommandError:
        malformed = True
    results.append(
        _result(
            "SLACK_MALFORMED_COMMAND",
            "Malformed command rejection",
            TestStatus.PASS if malformed else TestStatus.FAIL,
            "Malformed command is rejected locally."
            if malformed
            else "Malformed command was accepted.",
            action="Keep the parser strict; do not relax OTP validation.",
            severity=TestSeverity.MEDIUM,
        )
    )
    try:
        runtime.resolver.resolve("__acceptance_unknown_alias__")
    except AppError:
        unknown = True
    else:
        unknown = False
    results.append(
        _result(
            "SLACK_UNKNOWN_ALIAS",
            "Unknown alias rejection",
            TestStatus.PASS if unknown else TestStatus.FAIL,
            "Unknown alias is rejected before automation."
            if unknown
            else "Unknown alias was resolved unexpectedly.",
            action="Review the Account catalog and resolver.",
            severity=TestSeverity.MEDIUM,
        )
    )
    duplicate_guard = DuplicateGuard()
    event = {"trigger_id": "acceptance-local", "text": "A 111111"}
    first = duplicate_guard.is_duplicate(make_event_key(event, duplicate_guard.key))
    second = duplicate_guard.is_duplicate(make_event_key(event, duplicate_guard.key))
    results.append(
        _result(
            "SLACK_DUPLICATE_GUARD",
            "Local duplicate guard",
            TestStatus.PASS if first is False and second else TestStatus.FAIL,
            "A repeated local delivery is suppressed without Slack traffic."
            if first is False and second
            else "Local duplicate guard behavior is incorrect.",
            severity=TestSeverity.MEDIUM,
        )
    )
    results.append(
        _result(
            "SLACK_COMMAND_PATH",
            "Live /mt4 status command",
            TestStatus.MANUAL,
            "ACTION REQUIRED: send /mt4 status in the controlled Slack channel.",
            detail="The runner does not post messages to real Slack.",
            action="Send /mt4 status, then confirm the Agent reply in the test session.",
            severity=TestSeverity.HIGH,
            test_class=TestClass.MANUAL,
        )
    )
    duration = int((time.monotonic() - started) * 1000)
    return [item.model_copy(update={"duration_ms": duration}) for item in results]


__all__ = ["run_slack_checks"]
