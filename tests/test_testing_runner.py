from __future__ import annotations

import asyncio
import sys

import pytest

from app.models.domain import AccountCreate, GroupCreate
from app.testing.models import DetectedControl, TestPhase, TestStatus
from app.testing.mt4_discovery import apply_confirmed_selectors, discover_account, selector_diff
from app.testing.runner import TestRunner
from tests.support import account_values


def account_create(values):
    return AccountCreate.model_validate({**account_values(), **values})


class _FakeElement:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _FakeControl:
    def __init__(self, control_type="Edit"):
        self.element_info = _FakeElement(
            control_type=control_type,
            name="",
            automation_id="",
            class_name="",
            is_password=False,
            process_id=27784,
        )


class _FakeWindow:
    def __init__(self, title):
        self._title = title

    def window_text(self):
        return self._title


class _FakeAdapter:
    """Minimal WindowsAutomation stand-in exposing only the discovery surface."""

    def __init__(self, title, control_type="Edit"):
        self._window = _FakeWindow(title)
        self._control = _FakeControl(control_type=control_type)

    def _process_ids(self, account):
        return [27784]

    def _windows(self, pids):
        return [self._window]

    def _window_text(self, window):
        return window.window_text()

    def _window_key(self, window):
        return "handle:1"

    def _visible_controls(self, window, control_type):
        if control_type != self._control.element_info.control_type:
            return []
        return [self._control]


def _login_window_result(outcome):
    return next(item for item in outcome.results if item.id == "UIA_LOGIN_WINDOW")


@pytest.mark.asyncio
async def test_login_window_diagnostic_follows_configured_regex_not_english_literal(
    runtime, monkeypatch
):
    """Rakuten titles its login dialog 'Rakuten MetaTrader 4' with no 'login'."""
    account = runtime.accounts.create(
        account_create(
            {
                "display_name": "A",
                "alias": "A",
                "login_id": "local-id",
                "server": "server",
                "window_title_regex": r"^Rakuten MetaTrader 4$",
            }
        )
    )
    monkeypatch.setattr(sys, "platform", "win32")
    outcome = await discover_account(account, _FakeAdapter("Rakuten MetaTrader 4"))
    assert _login_window_result(outcome).status == TestStatus.PASS


@pytest.mark.asyncio
async def test_login_window_diagnostic_warns_when_window_misses_configured_regex(
    runtime, monkeypatch
):
    """A title containing 'login' must not override the configured regex."""
    account = runtime.accounts.create(
        account_create(
            {
                "display_name": "A",
                "alias": "A",
                "login_id": "local-id",
                "server": "server",
                "window_title_regex": r"^Rakuten MetaTrader 4$",
            }
        )
    )
    monkeypatch.setattr(sys, "platform", "win32")
    outcome = await discover_account(account, _FakeAdapter("Some Broker Login"))
    assert _login_window_result(outcome).status == TestStatus.WARN


@pytest.mark.asyncio
async def test_login_window_diagnostic_keeps_english_fallback_without_configured_regex(
    runtime, monkeypatch
):
    account = runtime.accounts.create(
        account_create(
            {
                "display_name": "A",
                "alias": "A",
                "login_id": "local-id",
                "server": "server",
                "window_title_regex": None,
            }
        )
    )
    monkeypatch.setattr(sys, "platform", "win32")
    outcome = await discover_account(account, _FakeAdapter("MT4 Login"))
    assert _login_window_result(outcome).status == TestStatus.PASS


@pytest.mark.asyncio
async def test_environment_phase_is_safe_and_marks_non_windows_as_not_run(runtime):
    runner = TestRunner(runtime)
    results = await runner.run_phase(TestPhase.ENVIRONMENT)
    assert any(item.status == TestStatus.NOT_RUN for item in results)
    assert runner.report_id is not None
    assert (runtime.paths.reports_dir / runner.report_id / "report.json").is_file()
    assert not runtime.login_service.active_job_count


@pytest.mark.asyncio
async def test_real_login_requires_explicit_confirmation_and_never_submits(runtime, monkeypatch):
    runner = TestRunner(runtime)
    monkeypatch.setattr(type(runner), "windows_available", property(lambda self: True))
    called = False

    def submit(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("real login must not submit without confirmation")

    monkeypatch.setattr(runtime.login_service, "submit", submit)
    results = await runner.run_real_login("missing", "123456", confirmed=False)
    assert results[0].id == "REAL_LOGIN_BROKER_CONFIRMATION_REQUIRED"
    assert results[0].status == TestStatus.MANUAL
    assert called is False


@pytest.mark.asyncio
async def test_real_login_is_blocked_until_environment_and_discovery(runtime, monkeypatch):
    runner = TestRunner(runtime)
    monkeypatch.setattr(type(runner), "windows_available", property(lambda self: True))
    results = await runner.run_real_login(
        "account", "123456", confirmed=True, broker_confirmed=True
    )
    assert results[0].id == "REAL_LOGIN_PHASE1_REQUIRED"
    assert results[0].status == TestStatus.MANUAL


@pytest.mark.asyncio
async def test_discovery_on_non_windows_never_claims_real_success(runtime):
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
    outcome = await discover_account(account, runtime.automation)
    assert outcome.results[0].status == TestStatus.NOT_RUN
    assert "WINDOWS_REAL_TEST_REQUIRED" in outcome.results[0].message


@pytest.mark.asyncio
async def test_group_runner_reports_each_member_result(runtime, monkeypatch):
    for alias, outcome in (("A", "failure"), ("B", "success")):
        runtime.accounts.create(
            account_create(
                {
                    "display_name": alias,
                    "alias": alias,
                    "login_id": f"local-{alias}",
                    "server": "server",
                    "mock_outcome": outcome,
                }
            )
        )
    group = runtime.groups.create(
        GroupCreate(
            name="GROUP1",
            account_ids=[a.id for a in runtime.accounts.list()],
            shared_otp_confirmed=True,
        )
    )
    runner = TestRunner(runtime)
    monkeypatch.setattr(type(runner), "windows_available", property(lambda self: True))
    await runner.run_phase(TestPhase.ENVIRONMENT)
    results = await runner.run_group(group.name, "123456", confirmed=True, broker_confirmed=True)
    assert [item.status for item in results[:2]] == [TestStatus.FAIL, TestStatus.PASS]
    assert runner.report_id is not None


@pytest.mark.asyncio
async def test_full_slack_mode_creates_manual_session_without_otp(runtime, monkeypatch):
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
    await runner.run_phase(TestPhase.ENVIRONMENT)
    await runner.run_phase(TestPhase.DISCOVERY, account_id=account.id)
    results = await runner.run_real_login(
        account.id, "", confirmed=True, broker_confirmed=True, full_slack=True
    )
    assert results[0].status == TestStatus.MANUAL
    assert results[0].evidence["session_id"].startswith("slack-")
    assert "123456" not in results[0].safe_dict()["message"]


@pytest.mark.asyncio
async def test_full_slack_result_times_out_without_claiming_success(runtime, monkeypatch):
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
    await runner.run_phase(TestPhase.ENVIRONMENT)
    await runner.run_phase(TestPhase.DISCOVERY, account_id=account.id)
    results = await runner.run_real_login(
        account.id, "", confirmed=True, broker_confirmed=True, full_slack=True
    )
    session_id = results[0].evidence["session_id"]
    timeout_results = await runner.await_slack_result(session_id, timeout_seconds=1)
    assert timeout_results[0].id == "SLACK_E2E_TIMEOUT"
    assert timeout_results[0].status == TestStatus.MANUAL


@pytest.mark.asyncio
async def test_full_slack_await_can_be_cancelled(runtime, monkeypatch):
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
    await runner.run_phase(TestPhase.ENVIRONMENT)
    await runner.run_phase(TestPhase.DISCOVERY, account_id=account.id)
    results = await runner.run_real_login(
        account.id, "", confirmed=True, broker_confirmed=True, full_slack=True
    )
    task = asyncio.create_task(
        runner.await_slack_result(results[0].evidence["session_id"], timeout_seconds=60)
    )
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_environment_reports_missing_terminal_on_windows_contract(runtime, monkeypatch):
    from app.testing import environment

    valid = account_create(
        {
            "display_name": "A",
            "alias": "A",
            "login_id": "local-id",
            "server": "server",
        }
    )
    invalid = valid.model_copy(
        update={
            "terminal_path": r"C:\missing\terminal.exe",
            "profile_path": r"C:\missing\profile",
        }
    )
    monkeypatch.setattr(runtime.accounts, "list", lambda: [invalid])
    monkeypatch.setattr(environment.sys, "platform", "win32")
    results = environment.run_environment_checks(runtime, runtime.paths)
    terminal = next(item for item in results if item.id == "ENV_TERMINAL_PATHS")
    assert terminal.status == TestStatus.FAIL


def test_selector_application_requires_all_high_confidence_controls(runtime):
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
    controls = [
        DetectedControl(field=field, automation_id=f"id-{field}", confidence="HIGH")
        for field in ("login_id", "otp", "server", "login_button")
    ]
    assert apply_confirmed_selectors(account, controls)["server"] == "id-server"
    assert selector_diff(account, controls)["otp"]["detected"] == "id-otp"
    assert apply_confirmed_selectors(account, controls[:2]) is None
    runner = TestRunner(runtime)
    runner._detected_account_id = "another-account"
    assert runner.apply_selectors(account.id, controls, confirmed=True)["applied"] is False
