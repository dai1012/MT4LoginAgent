"""Per-Slack-user Account isolation.

Two people share one workspace. Each may only drive the Account aliases bound to
their own Slack User ID, must not learn anything about the other's Accounts, and
must not see the other's login results. These tests pin that boundary, including
the requirement that "not allowed" and "does not exist" are indistinguishable.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.adapters.slack.gateway import SlackGateway
from app.adapters.slack.processor import SlackCommandProcessor
from app.main import create_application
from app.models.domain import AccountCreate, GroupCreate
from tests.support import account_values, instance_paths

USER_A = "U1111111111"
USER_B = "U2222222222"
GENERIC = "Target unavailable"


def make_processor(runtime, *, bindings, post_private=None, allowed=(USER_A, USER_B)):
    """A processor wired like the gateway, with the bindings under test."""
    posted: list[dict] = []

    async def recorder(channel, user, text):
        posted.append({"channel": channel, "user": user, "text": text})

    processor = SlackCommandProcessor(
        runtime.login_service,
        lambda user: user in allowed,
        runtime.status,
        aliases_for=lambda user: frozenset(bindings.get(user, ())),
        post_private=post_private or recorder,
    )
    return processor, posted


async def run_command(processor, text, *, user, channel="C0123"):
    responses: list[dict] = []
    broadcasts: list[dict] = []

    async def ack(**kwargs):
        responses.append(kwargs)

    async def say(**kwargs):  # pragma: no cover - must never be reached
        broadcasts.append(kwargs)

    await processor.handle(
        ack=ack, say=say, body={"user_id": user, "channel_id": channel, "text": text}
    )
    return responses, broadcasts


def two_accounts(runtime):
    """Two Accounts that differ only in alias, each in its own terminal instance."""
    created = []
    for alias, login_id in (("A", "login-a"), ("B", "login-b")):
        created.append(
            runtime.accounts.create(
                AccountCreate.model_validate(
                    {
                        **account_values(),
                        **instance_paths(alias),
                        "alias": alias,
                        "display_name": alias,
                        "login_id": login_id,
                    }
                )
            )
        )
    return created


@pytest.mark.asyncio
async def test_user_may_operate_only_their_own_alias(runtime):
    two_accounts(runtime)
    processor, _posted = make_processor(runtime, bindings={USER_A: ["A"]})
    ok, broadcasts = await run_command(processor, "A 123456", user=USER_A)
    assert "处理中" in ok[0]["text"]
    assert broadcasts == []

    denied, _ = await run_command(processor, "B 123456", user=USER_A)
    assert GENERIC in denied[0]["text"]
    await runtime.login_service.shutdown()


@pytest.mark.asyncio
async def test_two_users_are_isolated_from_each_other(runtime):
    two_accounts(runtime)
    processor, _ = make_processor(runtime, bindings={USER_A: ["A"], USER_B: ["B"]})

    denied, _ = await run_command(processor, "B 123456", user=USER_A)
    assert GENERIC in denied[0]["text"]

    ok, _ = await run_command(processor, "B 123456", user=USER_B)
    assert "处理中" in ok[0]["text"]
    await runtime.login_service.shutdown()


@pytest.mark.asyncio
async def test_unbound_target_and_missing_target_are_indistinguishable(runtime):
    two_accounts(runtime)
    processor, _ = make_processor(runtime, bindings={USER_A: ["A"]})
    existing_but_not_mine, _ = await run_command(processor, "B 123456", user=USER_A)
    never_existed, _ = await run_command(processor, "ZZZ 123456", user=USER_A)
    assert existing_but_not_mine[0]["text"] == never_existed[0]["text"]


@pytest.mark.asyncio
async def test_allowed_user_with_no_binding_can_do_nothing(runtime):
    """Fail-closed: being in the allowlist must not imply access to everything."""
    two_accounts(runtime)
    processor, _ = make_processor(runtime, bindings={})
    for target in ("A", "B", "ZZZ"):
        responses, _ = await run_command(processor, f"{target} 123456", user=USER_A)
        assert GENERIC in responses[0]["text"], target


@pytest.mark.asyncio
async def test_group_is_denied_when_any_member_is_not_bound(runtime):
    a, b = two_accounts(runtime)
    runtime.groups.create(GroupCreate(name="G1", account_ids=[a.id, b.id]))
    processor, _ = make_processor(runtime, bindings={USER_A: ["A"]})
    responses, _ = await run_command(processor, "G1 123456", user=USER_A)
    assert GENERIC in responses[0]["text"]
    # The reply must not name the member that was missing.
    assert "B" not in responses[0]["text"].split("Web Admin")[0]


@pytest.mark.asyncio
async def test_group_runs_when_every_member_is_bound(runtime):
    a, b = two_accounts(runtime)
    runtime.groups.create(GroupCreate(name="G1", account_ids=[a.id, b.id]))
    processor, _ = make_processor(runtime, bindings={USER_A: ["A", "B"]})
    responses, _ = await run_command(processor, "G1 123456", user=USER_A)
    assert GENERIC not in responses[0]["text"]
    await runtime.login_service.shutdown()


@pytest.mark.asyncio
async def test_denied_request_never_reaches_the_queue(runtime):
    """A refused target must not occupy a login slot or touch MT4."""
    two_accounts(runtime)
    processor, _ = make_processor(runtime, bindings={USER_A: ["A"]})
    for _ in range(3):
        await run_command(processor, "B 123456", user=USER_A)
    assert runtime.login_service.active_job_count == 0
    assert runtime.login_service.queued_job_count == 0


@pytest.mark.asyncio
async def test_status_shows_only_the_callers_aliases(runtime):
    two_accounts(runtime)
    processor, _ = make_processor(runtime, bindings={USER_A: ["A"]})
    responses, _ = await run_command(processor, "status", user=USER_A)
    text = responses[0]["text"]
    assert "A" in text
    # No global counters and no other user's alias.
    assert "B" not in text
    assert "Active jobs" not in text
    assert "Accounts: 2" not in text


@pytest.mark.asyncio
async def test_status_without_bindings_says_no_accounts_assigned(runtime):
    two_accounts(runtime)
    processor, _ = make_processor(runtime, bindings={})
    responses, _ = await run_command(processor, "status", user=USER_A)
    # Judge the Accounts line alone: the header legitimately contains "Agent" and
    # "Rakuten", so a whole-text search for an alias letter proves nothing.
    accounts_line = responses[0]["text"].splitlines()[-1]
    assert accounts_line == "Accounts: No accounts assigned."


@pytest.mark.asyncio
async def test_completion_goes_privately_and_never_broadcasts(runtime):
    two_accounts(runtime)
    processor, posted = make_processor(runtime, bindings={USER_A: ["A"]})
    _ok, broadcasts = await run_command(processor, "A 123456", user=USER_A)
    for _ in range(20):
        if posted:
            break
        await asyncio.sleep(0.01)
    assert posted, "the completion must be delivered privately"
    assert posted[0]["user"] == USER_A
    assert posted[0]["channel"] == "C0123"
    assert broadcasts == []


def test_gateway_allowed_aliases_defaults_to_nothing(runtime):
    two_accounts(runtime)
    runtime.settings_repository.update(allowed_slack_user_ids=[USER_A, USER_B])
    gateway = object.__new__(SlackGateway)
    gateway.settings = runtime.settings
    assert gateway._allowed_aliases(USER_A) == frozenset()
    assert gateway._allowed_aliases("  ") == frozenset()

    runtime.settings_repository.update(
        slack_user_account_bindings={USER_A: ["A"], USER_B: []}
    )
    assert gateway._allowed_aliases(USER_A) == frozenset({"A"})
    assert gateway._allowed_aliases(USER_B) == frozenset()


@pytest.mark.asyncio
async def test_private_poster_uses_ephemeral_in_a_channel(runtime, monkeypatch):
    calls: list[tuple] = []

    class FakeClient:
        def __init__(self, token=None):
            calls.append(("client", token))

        async def chat_postEphemeral(self, **kwargs):
            calls.append(("ephemeral", kwargs))

        async def chat_postMessage(self, **kwargs):
            calls.append(("message", kwargs))

    import slack_sdk.web.async_client as async_client

    monkeypatch.setattr(async_client, "AsyncWebClient", FakeClient)
    gateway = object.__new__(SlackGateway)
    poster = gateway._private_poster(
        SimpleNamespace(slack_bot_token=SimpleNamespace(get_secret_value=lambda: "xoxb-test"))
    )
    await poster("C0123", USER_A, "done")
    assert calls[-1] == (
        "ephemeral",
        {"channel": "C0123", "user": USER_A, "text": "done"},
    )


@pytest.mark.asyncio
async def test_private_poster_uses_dm_message_in_a_dm(runtime, monkeypatch):
    calls: list[tuple] = []

    class FakeClient:
        def __init__(self, token=None):
            pass

        async def chat_postEphemeral(self, **kwargs):
            calls.append(("ephemeral", kwargs))

        async def chat_postMessage(self, **kwargs):
            calls.append(("message", kwargs))

    import slack_sdk.web.async_client as async_client

    monkeypatch.setattr(async_client, "AsyncWebClient", FakeClient)
    gateway = object.__new__(SlackGateway)
    poster = gateway._private_poster(
        SimpleNamespace(slack_bot_token=SimpleNamespace(get_secret_value=lambda: "xoxb-test"))
    )
    await poster("D0123", USER_A, "done")
    # A DM is already private, and the ephemeral API is a poor fit there.
    assert calls[-1] == ("message", {"channel": "D0123", "text": "done"})


@pytest.mark.asyncio
async def test_private_poster_refuses_to_deliver_without_a_destination(runtime, monkeypatch):
    def explode(**kwargs):  # pragma: no cover - must not be called
        raise AssertionError("must not post anywhere")

    class FakeClient:
        def __init__(self, token=None):
            pass

        chat_postEphemeral = explode
        chat_postMessage = explode

    import slack_sdk.web.async_client as async_client

    monkeypatch.setattr(async_client, "AsyncWebClient", FakeClient)
    gateway = object.__new__(SlackGateway)
    poster = gateway._private_poster(
        SimpleNamespace(slack_bot_token=SimpleNamespace(get_secret_value=lambda: "xoxb-test"))
    )
    await poster("", USER_A, "done")
    await poster("C0123", "", "done")


def test_settings_normalise_bindings(runtime):
    runtime.settings_repository.update(
        slack_user_account_bindings={
            "  u1111111111 ": [" A ", "A", ""],
            "U2222222222": [],
        }
    )
    current = runtime.settings_repository.get()
    assert current.slack_user_account_bindings == {
        "U1111111111": ["A"],
        "U2222222222": [],
    }


def test_api_rejects_bindings_for_unknown_aliases(runtime):
    two_accounts(runtime)
    app = create_application(runtime)
    token = runtime.ensure_web_admin_token()
    with TestClient(app, base_url="http://127.0.0.1", headers={"X-Admin-Token": token}) as client:
        bad = client.put(
            "/api/slack",
            json={
                "enabled": True,
                "allowed_slack_user_ids": [USER_A],
                "slack_user_account_bindings": {USER_A: ["NOPE"]},
            },
        )
        assert bad.status_code == 422
        assert "NOPE" in bad.json()["detail"]

        good = client.put(
            "/api/slack",
            json={
                "enabled": True,
                "allowed_slack_user_ids": [USER_A, USER_B],
                "slack_user_account_bindings": {USER_A: ["A"], USER_B: ["B"]},
            },
        )
        assert good.status_code == 200
        body = good.json()
        assert body["slack_user_account_bindings"] == {USER_A: ["A"], USER_B: ["B"]}
