from __future__ import annotations

import asyncio
import json
from datetime import timedelta

import pytest
from pydantic import SecretStr

from app.models.domain import (
    AccountConfig,
    AccountCreate,
    AutomationResult,
    GroupCreate,
    GroupPatch,
    LoginRequest,
    LoginStatus,
    utc_now,
)
from app.models.errors import ConfigurationError, DomainValidationError
from app.mt4.factory import build_automation
from app.mt4.mock_automation import MockAutomation


def make_account(outcome="success", alias="A") -> AccountConfig:
    return AccountConfig.model_validate(
        {
            "display_name": alias,
            "alias": alias,
            "login_id": "local-id",
            "server": "demo-server",
            "terminal_path": r"C:\Rakuten\terminal.exe",
            "mock_outcome": outcome,
        }
    )


@pytest.mark.asyncio
async def test_mock_success_failure_timeout():
    mock = MockAutomation()
    success = await mock.login(make_account("success"), SecretStr("123456"))
    failed = await mock.login(make_account("failure"), SecretStr("123456"))
    timeout = await mock.login(make_account("timeout"), SecretStr("123456"))
    assert success.status == LoginStatus.SUCCESS
    assert failed.status == LoginStatus.FAILED
    assert timeout.status == LoginStatus.TIMEOUT


@pytest.mark.asyncio
async def test_group_partial_failure_and_history_never_contains_otp(runtime, tmp_path):
    runtime.accounts.create(
        AccountCreate.model_validate(
            {
                "display_name": "A",
                "alias": "A",
                "login_id": "local-a",
                "server": "demo",
                "terminal_path": r"C:\Rakuten\terminal.exe",
                "mock_outcome": "failure",
            }
        )
    )
    runtime.accounts.create(
        AccountCreate.model_validate(
            {
                "display_name": "B",
                "alias": "B",
                "login_id": "local-b",
                "server": "demo",
                "terminal_path": r"C:\Rakuten\terminal.exe",
                "mock_outcome": "success",
            }
        )
    )
    group = runtime.groups.create(
        GroupCreate(name="GROUP1", account_ids=[a.id for a in runtime.accounts.list()])
    )
    results = []
    done = asyncio.Event()

    async def callback(items):
        results.extend(items)
        done.set()

    runtime.login_service.submit(
        LoginRequest(
            sender_id="U1111111111", target=group.name, otp=SecretStr("123456"), source="test"
        ),
        callback,
    )
    await asyncio.wait_for(done.wait(), timeout=2)
    assert [item.status for item in results] == [LoginStatus.FAILED, LoginStatus.SUCCESS]
    assert len(runtime.history.recent()) == 2
    history_text = runtime.paths.history_file.read_text(encoding="utf-8")
    assert "123456" not in history_text
    assert "otp" not in history_text.casefold()
    for line in history_text.splitlines():
        assert set(json.loads(line)) == {
            "id",
            "timestamp",
            "sender",
            "target",
            "account_alias",
            "status",
            "error_category",
            "duration_ms",
        }
    await runtime.login_service.shutdown()
    with pytest.raises(DomainValidationError, match="shutting down"):
        runtime.login_service.submit(
            LoginRequest(
                sender_id="U1111111111", target="A", otp=SecretStr("123456"), source="test"
            )
        )


@pytest.mark.asyncio
async def test_group_stops_using_an_otp_after_it_expires_mid_group(runtime, monkeypatch):
    for alias in ("A", "B"):
        runtime.accounts.create(
            AccountCreate.model_validate(
                {
                    "display_name": alias,
                    "alias": alias,
                    "login_id": f"local-{alias}",
                    "server": "demo",
                    "terminal_path": r"C:\Rakuten\terminal.exe",
                }
            )
        )
    group = runtime.groups.create(
        GroupCreate(name="GROUP1", account_ids=[a.id for a in runtime.accounts.list()])
    )

    async def slow_login(_account, _otp):
        await asyncio.sleep(0.03)
        return AutomationResult(status=LoginStatus.SUCCESS, message="ok")

    monkeypatch.setattr(runtime.login_service.automation, "login", slow_login)
    runtime.login_service.otp_max_age_seconds = 0.01
    results = []
    done = asyncio.Event()

    async def callback(items):
        results.extend(items)
        done.set()

    runtime.login_service.submit(
        LoginRequest(
            sender_id="U1111111111", target=group.name, otp=SecretStr("123456"), source="test"
        ),
        callback,
    )
    await asyncio.wait_for(done.wait(), timeout=2)
    assert results[0].status == LoginStatus.SUCCESS
    assert results[1].error_category.value == "otp_expired"


@pytest.mark.asyncio
async def test_disabled_account_is_failed_and_does_not_start_automation(runtime):
    account = runtime.accounts.create(
        AccountCreate.model_validate(
            {
                "display_name": "A",
                "alias": "A",
                "login_id": "id",
                "server": "server",
                "terminal_path": r"C:\Rakuten\terminal.exe",
                "enabled": False,
            }
        )
    )
    done = asyncio.Event()
    results = []

    async def callback(items):
        results.extend(items)
        done.set()

    runtime.login_service.submit(
        LoginRequest(sender_id="U1", target=account.alias, otp=SecretStr("1234"), source="test"),
        callback,
    )
    await asyncio.wait_for(done.wait(), timeout=2)
    assert results[0].error_category.value == "account_disabled"
    await runtime.login_service.shutdown()


@pytest.mark.asyncio
async def test_queued_job_reloads_account_state_before_execution(runtime):
    account = runtime.accounts.create(
        AccountCreate.model_validate(
            {
                "display_name": "A",
                "alias": "A",
                "login_id": "id",
                "server": "server",
                "terminal_path": r"C:\Rakuten\terminal.exe",
            }
        )
    )
    second_account = runtime.accounts.create(
        AccountCreate.model_validate(
            {
                "display_name": "B",
                "alias": "B",
                "login_id": "id-b",
                "server": "server",
                "terminal_path": r"C:\Rakuten\terminal.exe",
            }
        )
    )

    class BlockingAutomation:
        mode = "test"

        def __init__(self):
            self.calls = 0
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def login(self, account_config, otp):
            self.calls += 1
            self.started.set()
            await self.release.wait()
            from app.models.domain import AutomationResult

            return AutomationResult(status=LoginStatus.SUCCESS, message="test success")

        async def account_status(self, account_config):
            raise NotImplementedError

        async def close(self):
            return None

    blocking = BlockingAutomation()
    runtime.login_service.automation = blocking
    first_done = asyncio.Event()
    second_done = asyncio.Event()
    first_results = []
    second_results = []

    async def first_callback(items):
        first_results.extend(items)
        first_done.set()

    async def second_callback(items):
        second_results.extend(items)
        second_done.set()

    runtime.login_service.submit(
        LoginRequest(sender_id="U1", target=account.alias, otp=SecretStr("1234"), source="test"),
        first_callback,
    )
    await asyncio.wait_for(blocking.started.wait(), timeout=2)
    runtime.login_service.submit(
        LoginRequest(
            sender_id="U1", target=second_account.alias, otp=SecretStr("1234"), source="test"
        ),
        second_callback,
    )
    runtime.accounts.set_enabled(second_account.id, False)
    blocking.release.set()
    await asyncio.wait_for(first_done.wait(), timeout=2)
    await asyncio.wait_for(second_done.wait(), timeout=2)
    assert blocking.calls == 1
    assert second_results[0].error_category.value == "account_disabled"
    await runtime.login_service.shutdown()


@pytest.mark.asyncio
async def test_login_queue_rejects_excess_jobs(runtime):
    account = runtime.accounts.create(
        AccountCreate.model_validate(
            {
                "display_name": "A",
                "alias": "A",
                "login_id": "id",
                "server": "server",
                "terminal_path": r"C:\Rakuten\terminal.exe",
            }
        )
    )

    class BlockingAutomation:
        mode = "test"

        def __init__(self):
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def login(self, account_config, otp):
            self.started.set()
            await self.release.wait()
            from app.models.domain import AutomationResult

            return AutomationResult(status=LoginStatus.SUCCESS, message="test success")

        async def account_status(self, account_config):
            raise NotImplementedError

        async def close(self):
            return None

    blocking = BlockingAutomation()
    runtime.login_service.automation = blocking
    runtime.login_service.max_jobs = 1
    done = asyncio.Event()

    async def callback(items):
        done.set()

    runtime.login_service.submit(
        LoginRequest(sender_id="U1", target=account.alias, otp=SecretStr("1234"), source="test"),
        callback,
    )
    await asyncio.wait_for(blocking.started.wait(), timeout=2)
    with pytest.raises(DomainValidationError, match="queue is full"):
        runtime.login_service.submit(
            LoginRequest(sender_id="U1", target=account.alias, otp=SecretStr("1234"), source="test")
        )
    blocking.release.set()
    await asyncio.wait_for(done.wait(), timeout=2)
    await runtime.login_service.shutdown()


@pytest.mark.asyncio
async def test_same_account_cannot_be_submitted_while_pending(runtime):
    account = runtime.accounts.create(
        AccountCreate.model_validate(
            {
                "display_name": "A",
                "alias": "A",
                "login_id": "id",
                "server": "server",
                "terminal_path": r"C:\Rakuten\terminal.exe",
            }
        )
    )

    class BlockingAutomation:
        mode = "test"

        def __init__(self):
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def login(self, account_config, otp):
            self.started.set()
            await self.release.wait()
            from app.models.domain import AutomationResult

            return AutomationResult(status=LoginStatus.SUCCESS, message="test success")

        async def account_status(self, account_config):
            raise NotImplementedError

        async def close(self):
            return None

    blocking = BlockingAutomation()
    runtime.login_service.automation = blocking
    done = asyncio.Event()

    async def callback(items):
        done.set()

    runtime.login_service.submit(
        LoginRequest(sender_id="U1", target=account.alias, otp=SecretStr("1234"), source="test"),
        callback,
    )
    await asyncio.wait_for(blocking.started.wait(), timeout=2)
    with pytest.raises(DomainValidationError, match="already have a login"):
        runtime.login_service.submit(
            LoginRequest(sender_id="U1", target=account.alias, otp=SecretStr("1234"), source="test")
        )
    blocking.release.set()
    await asyncio.wait_for(done.wait(), timeout=2)
    await runtime.login_service.shutdown()


@pytest.mark.asyncio
async def test_expired_queued_otp_is_not_sent_to_automation(runtime):
    account = runtime.accounts.create(
        AccountCreate.model_validate(
            {
                "display_name": "A",
                "alias": "A",
                "login_id": "id",
                "server": "server",
                "terminal_path": r"C:\Rakuten\terminal.exe",
            }
        )
    )

    class FailingAutomation:
        mode = "test"

        def __init__(self):
            self.calls = 0

        async def login(self, account_config, otp):
            self.calls += 1
            raise AssertionError("expired OTP must not reach automation")

        async def account_status(self, account_config):
            raise NotImplementedError

        async def close(self):
            return None

    automation = FailingAutomation()
    runtime.login_service.automation = automation
    runtime.login_service.otp_max_age_seconds = 30
    results = []
    done = asyncio.Event()

    async def callback(items):
        results.extend(items)
        done.set()

    request = LoginRequest(
        sender_id="U1",
        target=account.alias,
        otp=SecretStr("1234"),
        source="test",
        received_at=utc_now() - timedelta(seconds=60),
    )
    runtime.login_service.submit(request, callback)
    await asyncio.wait_for(done.wait(), timeout=2)
    assert automation.calls == 0
    assert results[0].error_category.value == "otp_expired"
    await runtime.login_service.shutdown()


@pytest.mark.asyncio
async def test_cancelled_job_sends_completion_for_remaining_accounts(runtime):
    accounts = [
        runtime.accounts.create(
            AccountCreate.model_validate(
                {
                    "display_name": alias,
                    "alias": alias,
                    "login_id": alias,
                    "server": "server",
                    "terminal_path": r"C:\Rakuten\terminal.exe",
                }
            )
        )
        for alias in ("A", "B")
    ]
    group = runtime.groups.create(
        GroupCreate(name="GROUP1", account_ids=[item.id for item in accounts])
    )

    class BlockingAutomation:
        mode = "test"

        def __init__(self):
            self.started = asyncio.Event()

        async def login(self, account_config, otp):
            self.started.set()
            await asyncio.Event().wait()

        async def account_status(self, account_config):
            raise NotImplementedError

        async def close(self):
            return None

    blocking = BlockingAutomation()
    runtime.login_service.automation = blocking
    results = []
    done = asyncio.Event()

    async def callback(items):
        results.extend(items)
        done.set()

    runtime.login_service.submit(
        LoginRequest(sender_id="U1", target=group.name, otp=SecretStr("1234"), source="test"),
        callback,
    )
    await asyncio.wait_for(blocking.started.wait(), timeout=2)
    task = next(iter(runtime.login_service._tasks))
    task.cancel()
    await asyncio.wait_for(done.wait(), timeout=2)
    assert len(results) == 2
    assert all(item.error_category.value in {"cancelled", "timeout"} for item in results)
    await runtime.login_service.shutdown()


@pytest.mark.asyncio
async def test_queued_account_target_does_not_execute_replacement_group(runtime):
    account = runtime.accounts.create(
        AccountCreate.model_validate(
            {
                "display_name": "A",
                "alias": "A",
                "login_id": "a",
                "server": "server",
                "terminal_path": r"C:\Rakuten\terminal.exe",
            }
        )
    )
    replacement = runtime.accounts.create(
        AccountCreate.model_validate(
            {
                "display_name": "B",
                "alias": "B",
                "login_id": "b",
                "server": "server",
                "terminal_path": r"C:\Rakuten\terminal.exe",
            }
        )
    )

    class BlockingAutomation:
        mode = "test"

        def __init__(self):
            self.calls = 0
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def login(self, account_config, otp):
            self.calls += 1
            self.started.set()
            await self.release.wait()
            from app.models.domain import AutomationResult

            return AutomationResult(status=LoginStatus.SUCCESS, message="test success")

        async def account_status(self, account_config):
            raise NotImplementedError

        async def close(self):
            return None

    blocking = BlockingAutomation()
    runtime.login_service.automation = blocking
    first_done = asyncio.Event()

    async def first_callback(items):
        first_done.set()

    runtime.login_service.submit(
        LoginRequest(sender_id="U1", target="A", otp=SecretStr("1234"), source="test"),
        first_callback,
    )
    await asyncio.wait_for(blocking.started.wait(), timeout=2)
    with pytest.raises(DomainValidationError, match="already have a login"):
        runtime.login_service.submit(
            LoginRequest(sender_id="U1", target="A", otp=SecretStr("1234"), source="test")
        )
    runtime.accounts.delete(account.id)
    runtime.groups.create(GroupCreate(name="A", account_ids=[replacement.id]))
    blocking.release.set()
    await asyncio.wait_for(first_done.wait(), timeout=2)
    assert blocking.calls == 1
    await runtime.login_service.shutdown()


@pytest.mark.asyncio
async def test_group_member_removal_does_not_skip_later_members(runtime):
    accounts = [
        runtime.accounts.create(
            AccountCreate.model_validate(
                {
                    "display_name": alias,
                    "alias": alias,
                    "login_id": alias,
                    "server": "server",
                    "terminal_path": r"C:\Rakuten\terminal.exe",
                }
            )
        )
        for alias in ("A", "B", "C")
    ]
    group = runtime.groups.create(
        GroupCreate(name="GROUP1", account_ids=[item.id for item in accounts])
    )

    class BlockingAutomation:
        mode = "test"

        def __init__(self):
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.calls = 0

        async def login(self, account_config, otp):
            self.calls += 1
            if self.calls == 1:
                self.started.set()
                await self.release.wait()
            from app.models.domain import AutomationResult

            return AutomationResult(status=LoginStatus.SUCCESS, message="test success")

        async def account_status(self, account_config):
            raise NotImplementedError

        async def close(self):
            return None

    blocking = BlockingAutomation()
    runtime.login_service.automation = blocking
    results = []
    done = asyncio.Event()

    async def callback(items):
        results.extend(items)
        done.set()

    runtime.login_service.submit(
        LoginRequest(sender_id="U1", target=group.name, otp=SecretStr("1234"), source="test"),
        callback,
    )
    await asyncio.wait_for(blocking.started.wait(), timeout=2)
    runtime.groups.update(
        group.id,
        GroupPatch(account_ids=[accounts[0].id, accounts[2].id]),
    )
    blocking.release.set()
    await asyncio.wait_for(done.wait(), timeout=2)
    assert [item.account_alias for item in results] == ["A", "B", "C"]
    assert results[1].error_category.value == "target_changed"
    assert results[2].status == LoginStatus.SUCCESS
    await runtime.login_service.shutdown()


def test_non_windows_factory_uses_mock_or_rejects_explicit_windows():
    from app.models.domain import AppSettings, AutomationMode

    assert build_automation(AppSettings(automation_mode=AutomationMode.MOCK)).mode == "mock"
    with pytest.raises(ConfigurationError):
        build_automation(AppSettings(automation_mode=AutomationMode.WINDOWS))
