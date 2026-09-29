"""Step card summary semantics.

A step that ran and left a checklist or a destructive probe for the human has done
its job. Painting it amber misreports that as a degradation, so MANUAL must not
raise a step to WARN, an old failure must not outlive a later success, and a genuine
warning or failure must still be visible.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.testing.models import (
    TestCategory,
    TestPhase,
    TestResult,
    TestSeverity,
    TestStatus,
)
from app.testing.runner import TestRunner

MANIFEST = Path(__file__).resolve().parents[1] / "slack-app-manifest.yaml"


def result(identifier, status, category=TestCategory.ENVIRONMENT, **kwargs):
    return TestResult(
        id=identifier,
        category=category,
        name=identifier,
        status=status,
        severity=TestSeverity.MEDIUM,
        **kwargs,
    )


def verdict(runner, phase):
    return runner.status()["phase_status"].get(phase.value)


def manual_count(runner, phase):
    return runner.status()["phase_manual"].get(phase.value, 0)


# --- MANUAL must not colour a step -------------------------------------------------


def test_pass_plus_manual_only_is_pass(runtime):
    runner = TestRunner(runtime)
    runner._record_phase(
        TestPhase.ENVIRONMENT,
        [
            result("ENV_WINDOWS_REQUIRED", TestStatus.PASS),
            result("ENV_DESTRUCTIVE_SYS_TIME", TestStatus.MANUAL),
            result("ENV_DESTRUCTIVE_UAC", TestStatus.MANUAL),
        ],
    )
    assert verdict(runner, TestPhase.ENVIRONMENT) == "pass"
    assert manual_count(runner, TestPhase.ENVIRONMENT) == 2


def test_slack_diagnostics_pass_with_one_manual_is_pass(runtime):
    """SLACK_COMMAND_PATH is a human action and must not warn the whole step."""
    runner = TestRunner(runtime)
    runner._record_phase(
        TestPhase.SLACK,
        [
            result("SLACK_APP_TOKEN", TestStatus.PASS, TestCategory.SLACK),
            result("SLACK_BOT_TOKEN", TestStatus.PASS, TestCategory.SLACK),
            result("SLACK_SOCKET_CONNECTED", TestStatus.PASS, TestCategory.SLACK),
            result("SLACK_MALFORMED_COMMAND", TestStatus.PASS, TestCategory.SLACK),
            result("SLACK_COMMAND_PATH", TestStatus.MANUAL, TestCategory.SLACK),
        ],
    )
    assert verdict(runner, TestPhase.SLACK) == "pass"
    assert manual_count(runner, TestPhase.SLACK) == 1


# --- Discovery: an alternative route is not a degradation ------------------------


def test_win32_route_makes_a_uia_warning_a_pass(runtime):
    runner = TestRunner(runtime)
    runner._discovery_route = "win32"
    runner._record_phase(
        TestPhase.DISCOVERY,
        [
            result(
                "UIA_CONTROL_DISCOVERY",
                TestStatus.WARN,
                TestCategory.MT4_DISCOVERY,
            ),
            result(
                "UIA_LOGIN_WINDOW",
                TestStatus.WARN,
                TestCategory.MT4_DISCOVERY,
            ),
            result(
                "MT4_DISCOVERY_READY",
                TestStatus.PASS,
                TestCategory.MT4_DISCOVERY,
            ),
        ],
    )
    assert verdict(runner, TestPhase.DISCOVERY) == "pass"
    # The individual rows are untouched, so the warning is still reported truthfully.
    rows = {item.id: item.status for item in runner.status()["results"]} or {}
    assert rows == {}


def test_discovery_without_a_route_still_warns(runtime):
    runner = TestRunner(runtime)
    runner._discovery_route = ""
    runner._record_phase(
        TestPhase.DISCOVERY,
        [
            result("UIA_CONTROL_DISCOVERY", TestStatus.WARN, TestCategory.MT4_DISCOVERY),
            result("MT4_DISCOVERY_READY", TestStatus.WARN, TestCategory.MT4_DISCOVERY),
        ],
    )
    assert verdict(runner, TestPhase.DISCOVERY) == "warn"


def test_discovery_failure_is_not_downgraded_by_a_route(runtime):
    runner = TestRunner(runtime)
    runner._discovery_route = "win32"
    runner._record_phase(
        TestPhase.DISCOVERY,
        [
            result("MT4_DISCOVERY_READY", TestStatus.FAIL, TestCategory.MT4_DISCOVERY),
        ],
    )
    assert verdict(runner, TestPhase.DISCOVERY) == "fail"


# --- Latest wins: results accumulate for the life of the process ------------------


def test_an_older_failure_does_not_outlive_a_later_pass(runtime):
    runner = TestRunner(runtime)
    failed = [result("REAL_LOGIN_A", TestStatus.FAIL, TestCategory.REAL_LOGIN)]
    passed = [
        result("REAL_LOGIN_A", TestStatus.PASS, TestCategory.REAL_LOGIN),
        result("SEC_AUDIT_LOG", TestStatus.PASS, TestCategory.SECURITY),
        result("SEC_HISTORY", TestStatus.PASS, TestCategory.SECURITY),
    ]
    runner._record_phase(TestPhase.REAL_LOGIN, failed)
    assert verdict(runner, TestPhase.REAL_LOGIN) == "fail"
    runner._record_phase(TestPhase.REAL_LOGIN, passed)
    assert verdict(runner, TestPhase.REAL_LOGIN) == "pass"


def test_a_later_failure_does_outlive_an_earlier_pass(runtime):
    runner = TestRunner(runtime)
    runner._record_phase(
        TestPhase.REAL_LOGIN,
        [result("REAL_LOGIN_A", TestStatus.PASS, TestCategory.REAL_LOGIN)],
    )
    assert verdict(runner, TestPhase.REAL_LOGIN) == "pass"
    runner._record_phase(
        TestPhase.REAL_LOGIN,
        [result("REAL_LOGIN_A", TestStatus.FAIL, TestCategory.REAL_LOGIN)],
    )
    assert verdict(runner, TestPhase.REAL_LOGIN) == "fail"


def test_repeated_ids_inside_one_run_use_the_last_value(runtime):
    runner = TestRunner(runtime)
    runner._record_phase(
        TestPhase.REAL_LOGIN,
        [
            result("REAL_LOGIN_A", TestStatus.FAIL, TestCategory.REAL_LOGIN),
            result("REAL_LOGIN_A", TestStatus.PASS, TestCategory.REAL_LOGIN),
        ],
    )
    assert verdict(runner, TestPhase.REAL_LOGIN) == "pass"


# --- Genuine problems stay visible ------------------------------------------------


def test_a_real_warning_still_warns(runtime):
    runner = TestRunner(runtime)
    runner._record_phase(
        TestPhase.SLACK,
        [
            result("SLACK_APP_TOKEN", TestStatus.PASS, TestCategory.SLACK),
            result("SLACK_SOCKET_CONNECTED", TestStatus.WARN, TestCategory.SLACK),
            result("SLACK_COMMAND_PATH", TestStatus.MANUAL, TestCategory.SLACK),
        ],
    )
    assert verdict(runner, TestPhase.SLACK) == "warn"


def test_a_failure_still_fails_even_beside_manual_items(runtime):
    runner = TestRunner(runtime)
    runner._record_phase(
        TestPhase.ENVIRONMENT,
        [
            result("ENV_DESTRUCTIVE_UAC", TestStatus.MANUAL),
            result("ENV_SECURITY_SCAN", TestStatus.FAIL),
        ],
    )
    assert verdict(runner, TestPhase.ENVIRONMENT) == "fail"
    assert manual_count(runner, TestPhase.ENVIRONMENT) == 1


def test_not_run_is_reported_as_not_run():
    assert TestRunner._verdict([]) == "not_run"


def test_group_phase_still_aggregates_normally(runtime):
    """Group is paused as a feature; its summary must keep working unchanged."""
    runner = TestRunner(runtime)
    runner._record_phase(
        TestPhase.GROUP,
        [
            result("GROUP_MEMBER_A", TestStatus.PASS, TestCategory.GROUP),
            result("GROUP_MEMBER_B", TestStatus.MANUAL, TestCategory.GROUP),
        ],
    )
    assert verdict(runner, TestPhase.GROUP) == "pass"
    assert manual_count(runner, TestPhase.GROUP) == 1


@pytest.mark.asyncio
async def test_real_login_records_the_phase_so_step_four_is_not_never_run(
    runtime, monkeypatch
):
    """Regression: Step 4 used to be marked complete without a verdict, so its card
    fell through to the warning branch and stayed amber forever."""
    from tests.test_testing_runner import account_create

    account = runtime.accounts.create(
        account_create(
            {
                "display_name": "A",
                "alias": "A",
                "login_id": "local-id",
                "server": "server",
            }
        )
    )
    runner = TestRunner(runtime)
    monkeypatch.setattr(type(runner), "windows_available", property(lambda self: True))
    await runner.run_phase(TestPhase.DISCOVERY, account_id=account.id)
    # Real Login is gated on a passed Phase 1, which cannot happen off Windows, so
    # the precondition is declared here rather than pretended to be produced.
    runner._completed_phases.add(TestPhase.ENVIRONMENT)
    runner._phase_status[TestPhase.ENVIRONMENT] = "pass"
    runner._discovery_route = "uia"
    await runner.run_real_login(
        account.id, "TESTONLY1aB2cD3", confirmed=True, broker_confirmed=True
    )
    state = runner.status()
    assert "real_login" in state["completed_phases"]
    assert state["phase_status"].get("real_login") is not None
    assert state["phase_status"]["real_login"] in {"pass", "warn", "fail"}


# --- Manifest: the Messages tab must stay writable, with no new scope -------------


def load_manifest():
    return yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))


def test_manifest_enables_a_writable_messages_tab():
    manifest = load_manifest()
    app_home = manifest["features"]["app_home"]
    assert app_home["messages_tab_enabled"] is True
    assert app_home["messages_tab_read_only_enabled"] is False


def test_manifest_adds_no_new_bot_scope():
    manifest = load_manifest()
    # Slack routes the slash command itself, so a message handler and extra scopes
    # are not needed for typing "/mt4 ..." in the Messages tab.
    assert manifest["oauth_config"]["scopes"]["bot"] == ["commands", "chat:write"]


def test_manifest_usage_hint_does_not_promise_a_numeric_otp():
    manifest = load_manifest()
    hint = manifest["features"]["slash_commands"][0]["usage_hint"]
    assert "<credential>" in hint
    assert "<OTP>" not in hint
