from __future__ import annotations

import asyncio

import pytest

from app.models.domain import AccountCreate, GroupCreate
from app.testing.models import DetectedControl, TestPhase, TestStatus
from app.testing.mt4_discovery import apply_confirmed_selectors, discover_account, selector_diff
from app.testing.runner import TestRunner


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
    assert results[0].status == TestStatus.MANUAL
    assert called is False


@pytest.mark.asyncio
async def test_real_login_is_blocked_until_environment_and_discovery(runtime, monkeypatch):
    runner = TestRunner(runtime)
    monkeypatch.setattr(type(runner), "windows_available", property(lambda self: True))
    results = await runner.run_real_login("account", "123456", confirmed=True)
    assert results[0].id == "REAL_LOGIN_PHASE1_REQUIRED"
    assert results[0].status == TestStatus.MANUAL


@pytest.mark.asyncio
async def test_discovery_on_non_windows_never_claims_real_success(runtime):
    account = runtime.accounts.create(
        AccountCreate.model_validate(
            {
                "display_name": "A",
                "alias": "A",
                "login_id": "local-id",
                "server": "server",
                "terminal_path": r"C:\Rakuten\terminal.exe",
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
            AccountCreate.model_validate(
                {
                    "display_name": alias,
                    "alias": alias,
                    "login_id": f"local-{alias}",
                    "server": "server",
                    "terminal_path": r"C:\Rakuten\terminal.exe",
                    "mock_outcome": outcome,
                }
            )
        )
    group = runtime.groups.create(
        GroupCreate(name="GROUP1", account_ids=[a.id for a in runtime.accounts.list()])
    )
    runner = TestRunner(runtime)
    monkeypatch.setattr(type(runner), "windows_available", property(lambda self: True))
    await runner.run_phase(TestPhase.ENVIRONMENT)
    results = await runner.run_group(group.name, "123456", confirmed=True)
    assert [item.status for item in results[:2]] == [TestStatus.FAIL, TestStatus.PASS]
    assert runner.report_id is not None


@pytest.mark.asyncio
async def test_full_slack_mode_creates_manual_session_without_otp(runtime, monkeypatch):
    account = runtime.accounts.create(
        AccountCreate.model_validate(
            {
                "display_name": "A",
                "alias": "A",
                "login_id": "local-id",
                "server": "server",
                "terminal_path": r"C:\Rakuten\terminal.exe",
            }
        )
    )
    runner = TestRunner(runtime)
    monkeypatch.setattr(type(runner), "windows_available", property(lambda self: True))
    await runner.run_phase(TestPhase.ENVIRONMENT)
    await runner.run_phase(TestPhase.DISCOVERY, account_id=account.id)
    results = await runner.run_real_login(account.id, "", confirmed=True, full_slack=True)
    assert results[0].status == TestStatus.MANUAL
    assert results[0].evidence["session_id"].startswith("slack-")
    assert "123456" not in results[0].safe_dict()["message"]


@pytest.mark.asyncio
async def test_full_slack_result_times_out_without_claiming_success(runtime, monkeypatch):
    account = runtime.accounts.create(
        AccountCreate.model_validate(
            {
                "display_name": "A",
                "alias": "A",
                "login_id": "local-id",
                "server": "server",
                "terminal_path": r"C:\Rakuten\terminal.exe",
            }
        )
    )
    runner = TestRunner(runtime)
    monkeypatch.setattr(type(runner), "windows_available", property(lambda self: True))
    await runner.run_phase(TestPhase.ENVIRONMENT)
    await runner.run_phase(TestPhase.DISCOVERY, account_id=account.id)
    results = await runner.run_real_login(account.id, "", confirmed=True, full_slack=True)
    session_id = results[0].evidence["session_id"]
    timeout_results = await runner.await_slack_result(session_id, timeout_seconds=1)
    assert timeout_results[0].id == "SLACK_E2E_TIMEOUT"
    assert timeout_results[0].status == TestStatus.MANUAL


@pytest.mark.asyncio
async def test_full_slack_await_can_be_cancelled(runtime, monkeypatch):
    account = runtime.accounts.create(
        AccountCreate.model_validate(
            {
                "display_name": "A",
                "alias": "A",
                "login_id": "local-id",
                "server": "server",
                "terminal_path": r"C:\Rakuten\terminal.exe",
            }
        )
    )
    runner = TestRunner(runtime)
    monkeypatch.setattr(type(runner), "windows_available", property(lambda self: True))
    await runner.run_phase(TestPhase.ENVIRONMENT)
    await runner.run_phase(TestPhase.DISCOVERY, account_id=account.id)
    results = await runner.run_real_login(account.id, "", confirmed=True, full_slack=True)
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

    runtime.accounts.create(
        AccountCreate.model_validate(
            {
                "display_name": "A",
                "alias": "A",
                "login_id": "local-id",
                "server": "server",
                "terminal_path": r"C:\missing\terminal.exe",
                "profile_path": r"C:\missing\profile",
            }
        )
    )
    monkeypatch.setattr(environment.sys, "platform", "win32")
    results = environment.run_environment_checks(runtime, runtime.paths)
    terminal = next(item for item in results if item.id == "ENV_TERMINAL_PATHS")
    assert terminal.status == TestStatus.FAIL


def test_selector_application_requires_all_high_confidence_controls(runtime):
    account = runtime.accounts.create(
        AccountCreate.model_validate(
            {
                "display_name": "A",
                "alias": "A",
                "login_id": "local-id",
                "server": "server",
                "terminal_path": r"C:\Rakuten\terminal.exe",
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
