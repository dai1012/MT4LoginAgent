from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from app.models.domain import SecretsConfig
from tests.support import wait_for


@pytest.mark.asyncio
async def test_connection_test_reports_rejected_bot_token(runtime, monkeypatch):
    runtime.secrets_repository.set(
        SecretsConfig(
            slack_app_token=SecretStr("xapp-test"),
            slack_bot_token=SecretStr("xoxb-invalid"),
        )
    )

    def invalid_auth_test(self):
        return {"ok": False, "error": "invalid_auth"}

    monkeypatch.setattr("slack_sdk.WebClient.auth_test", invalid_auth_test)
    result = await runtime.slack.test_connection()
    assert result["ok"] is False
    assert result["state"] == "token_rejected"
    assert "invalid_auth" in result["message"]


@pytest.mark.asyncio
async def test_async_transport_status_refresh(runtime):
    async def is_connected():
        return False

    runtime.slack._handler = SimpleNamespace(client=SimpleNamespace(is_connected=is_connected))
    runtime.slack._connected = True
    runtime.slack._state = "connected"
    await runtime.slack._refresh_connection_state()
    state, connected, error = runtime.slack.public_status()
    assert state == "disconnected"
    assert connected is False
    assert "disconnected" in (error or "")


@pytest.mark.asyncio
async def test_supervisor_rebuilds_a_stale_disconnected_handler(runtime, monkeypatch):
    gateway = runtime.slack
    stale = SimpleNamespace(client=SimpleNamespace(is_connected=lambda: False))
    gateway._handler = stale
    gateway._connected = False
    gateway._disconnected_at = 0.0
    runtime.settings_repository.update(slack_enabled=True)
    runtime.secrets_repository.set(
        SecretsConfig(
            slack_app_token=SecretStr("xapp-test"), slack_bot_token=SecretStr("xoxb-test")
        )
    )
    closed = []
    started = []

    async def close_handler(handler):
        closed.append(handler)

    async def start():
        started.append(True)
        return True

    monkeypatch.setattr(gateway, "_close_handler", close_handler)
    monkeypatch.setattr(gateway, "start", start)
    monkeypatch.setattr(gateway, "_retry_delay", 0.001)
    task = asyncio.create_task(gateway._reconnect_supervisor())
    # Wait for the supervisor to act rather than assuming it fits in a fixed sleep.
    await wait_for(lambda: bool(started))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed == [stale]
    assert started
