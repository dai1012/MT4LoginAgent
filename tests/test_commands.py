from __future__ import annotations

import asyncio

import pytest

from app.adapters.slack.commands import CommandError, CommandKind, parse_slash_command
from app.adapters.slack.processor import SlackCommandProcessor
from app.models.domain import AccountCreate


def test_parse_login_and_status():
    login = parse_slash_command("A 123456")
    assert login.kind == CommandKind.LOGIN
    assert login.target == "A"
    assert login.otp == "123456"
    assert parse_slash_command("/mt4 status").kind == CommandKind.STATUS


@pytest.mark.parametrize(
    "text",
    [
        "A abc",
        "A 123",
        "A " + "9" * 33,  # over the 32 character ceiling
        "",
        "A 123456 extra",
        "/mt4foo 123456",
        "A 123456" + "x" * 300,
    ],
)
def test_invalid_command_shapes_are_rejected(text):
    with pytest.raises(CommandError):
        parse_slash_command(text)


@pytest.mark.asyncio
async def test_processor_rejects_unauthorized_before_processing(runtime, account_payload):
    runtime.accounts.create(AccountCreate.model_validate(account_payload))
    runtime.settings_repository.update(allowed_slack_user_ids=["U1111111111"])
    processor = SlackCommandProcessor(
        runtime.login_service, lambda user: user == "U1111111111", runtime.status
    )
    responses = []
    says = []

    async def ack(**kwargs):
        responses.append(kwargs)

    async def say(**kwargs):
        says.append(kwargs)

    await processor.handle(ack=ack, say=say, body={"user_id": "U2222222222", "text": "A 123456"})
    assert responses and "未授权" in responses[0]["text"]
    assert says == []


@pytest.mark.asyncio
async def test_processor_status_and_invalid_alias(runtime, account_payload):
    runtime.settings_repository.update(allowed_slack_user_ids=["U1111111111"])
    processor = SlackCommandProcessor(
        runtime.login_service, lambda user: user == "U1111111111", runtime.status
    )
    responses = []

    async def ack(**kwargs):
        responses.append(kwargs)

    async def say(**kwargs):
        raise AssertionError("status must not send a completion message")

    await processor.handle(ack=ack, say=say, body={"user_id": "U1111111111", "text": "status"})
    await processor.handle(
        ack=ack, say=say, body={"user_id": "U1111111111", "text": "MISSING 123456"}
    )
    assert "Rakuten MT4" in responses[0]["text"]
    assert "does not exist" in responses[1]["text"]


@pytest.mark.asyncio
async def test_ack_failure_releases_dedup_key_for_retry(runtime, account_payload):
    runtime.accounts.create(AccountCreate.model_validate(account_payload))
    runtime.settings_repository.update(allowed_slack_user_ids=["U1111111111"])
    processor = SlackCommandProcessor(
        runtime.login_service, lambda user: user == "U1111111111", runtime.status
    )
    body = {"user_id": "U1111111111", "text": "A 123456", "trigger_id": "retry-1"}
    responses = []
    fail_ack = True

    async def ack(**kwargs):
        nonlocal fail_ack
        if fail_ack:
            fail_ack = False
            raise ConnectionError("simulated reconnect")

        responses.append(kwargs)

    async def say(**kwargs):
        return None

    await processor.handle(ack=ack, say=say, body=body)
    await processor.handle(ack=ack, say=say, body=body)
    assert responses and "处理中" in responses[0]["text"]
    await runtime.login_service.shutdown()


@pytest.mark.asyncio
async def test_duplicate_slack_delivery_is_ignored(runtime, account_payload):
    runtime.accounts.create(AccountCreate.model_validate(account_payload))
    runtime.settings_repository.update(allowed_slack_user_ids=["U1111111111"])
    processor = SlackCommandProcessor(
        runtime.login_service, lambda user: user == "U1111111111", runtime.status
    )
    responses = []
    says = []

    async def ack(**kwargs):
        responses.append(kwargs)

    async def say(**kwargs):
        says.append(kwargs)

    body = {
        "user_id": "U1111111111",
        "text": "A 123456",
        "trigger_id": "trigger-duplicate-1",
    }
    await processor.handle(ack=ack, say=say, body=body)
    await processor.handle(ack=ack, say=say, body=body)
    assert "处理中" in responses[0]["text"]
    assert "重复" in responses[1]["text"]
    for _ in range(20):
        if says:
            break
        await asyncio.sleep(0.01)
    assert len(runtime.history.recent()) == 1
    await runtime.login_service.shutdown()


@pytest.mark.asyncio
async def test_processor_acknowledges_and_sends_safe_completion(runtime, account_payload):
    runtime.accounts.create(AccountCreate.model_validate(account_payload))
    runtime.settings_repository.update(allowed_slack_user_ids=["U1111111111"])
    processor = SlackCommandProcessor(
        runtime.login_service, lambda user: user == "U1111111111", runtime.status
    )
    responses = []
    says = []

    async def ack(**kwargs):
        responses.append(kwargs)

    async def say(**kwargs):
        says.append(kwargs)

    await processor.handle(
        ack=ack,
        say=say,
        body={"user_id": "U1111111111", "text": "A 123456", "thread_ts": "123.45"},
    )
    assert "处理中" in responses[0]["text"]
    for _ in range(20):
        if says:
            break
        await asyncio.sleep(0.01)
    assert says and "登录成功" in says[0]["text"]
    assert "MOCK/非真实登录" in says[0]["text"]
    assert "123456" not in str(says)
    await runtime.login_service.shutdown()
