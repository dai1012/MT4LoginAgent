from __future__ import annotations

import json
import socket

import pytest

from app.main import _port_is_available, create_application


def test_port_probe_rejects_a_second_listener():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        assert _port_is_available(listener.getsockname()[1]) is False


@pytest.mark.asyncio
async def test_non_ascii_admin_token_is_rejected_without_500(runtime):
    app = create_application(runtime)
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/api/runtime",
        "raw_path": b"/api/runtime",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"127.0.0.1"), (b"x-admin-token", b"\xe9")],
        "client": ("127.0.0.1", 1),
        "server": ("127.0.0.1", 8765),
    }
    await app(scope, receive, send)
    start = next(message for message in messages if message["type"] == "http.response.start")
    assert start["status"] == 401


def test_web_health_dashboard_and_static_shell(client):
    assert client.get("/api/health").json()["status"] == "ok"
    assert client.get("/api/runtime").json()["agent_status"] == "running"
    assert client.get("/api/health", headers={"X-Admin-Token": ""}).status_code == 200
    assert client.get("/api/runtime", headers={"X-Admin-Token": ""}).status_code == 401
    assert client.get("/api/dashboard").status_code == 200
    page = client.get("/")
    assert page.status_code == 200
    assert "Rakuten MT4 Remote Login Agent" in page.text
    assert client.get("/static/styles.css").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/api/health", headers={"host": "192.168.1.20"}).status_code == 400
    oversized = client.post(
        "/api/accounts",
        content=b"x" * (128 * 1024 + 1),
        headers={"content-type": "application/json"},
    )
    assert oversized.status_code == 413
    assert (
        client.post(
            "/api/slack/test",
            headers={"Origin": "https://evil.example"},
        ).status_code
        == 403
    )


def test_web_account_group_crud_and_history_schema(client, account_payload):
    response = client.post("/api/accounts", json=account_payload)
    assert response.status_code == 201
    account = response.json()
    assert client.post(f"/api/accounts/{account['id']}/validate").json()["valid"] is True
    group = client.post("/api/groups", json={"name": "GROUP1", "account_ids": [account["id"]]})
    assert group.status_code == 201
    assert client.get("/api/groups").json()[0]["accounts"][0]["alias"] == "A"
    assert (
        client.post(f"/api/accounts/{account['id']}/enabled", json={"enabled": False}).json()[
            "enabled"
        ]
        is False
    )
    assert client.delete(f"/api/groups/{group.json()['id']}").status_code == 200
    assert client.get("/api/history").json() == []
    assert "otp" not in client.get("/api/dashboard").text.casefold()


def test_web_slack_tokens_are_write_only(client):
    response = client.put(
        "/api/slack",
        json={
            "enabled": False,
            "allowed_slack_user_ids": ["U123456789"],
            "app_token": "xapp-test-secret",
            "bot_token": "xoxb-test-secret",
        },
    )
    assert response.status_code == 200
    text = response.text
    assert "xapp-test-secret" not in text
    assert "xoxb-test-secret" not in text
    assert response.json()["app_token_configured"] is True
    assert response.json()["bot_token_configured"] is True
    assert client.get("/api/slack").text.find("xapp-test-secret") == -1
    assert client.put("/api/settings", json={"automation_mode": "mock"}).status_code == 405
    assert (
        client.put(
            "/api/slack",
            json={
                "enabled": False,
                "allowed_slack_user_ids": ["not a user id"],
                "app_token": None,
                "bot_token": None,
            },
        ).status_code
        == 422
    )


def test_web_rejects_invalid_account_and_unknown_id(client, account_payload):
    bad = client.post("/api/accounts", json={**account_payload, "alias": "bad alias"})
    assert bad.status_code == 422
    missing = client.get("/api/accounts/not-found")
    assert missing.status_code == 404


def test_account_can_be_disabled(client, account_payload):
    account = client.post("/api/accounts", json=account_payload).json()
    assert (
        client.post(f"/api/accounts/{account['id']}/enabled", json={"enabled": False}).status_code
        == 200
    )


def test_favicon_returns_a_bodyless_204(client):
    """A 204 must not carry a rendered body.

    Returning JSON with a 204 renders b"null" against a response that declares no
    Content-Length, which uvicorn rejects with "Response content longer than
    Content-Length".
    """
    response = client.get("/favicon.ico")
    assert response.status_code == 204
    assert response.content == b""
    lowered = {key.lower() for key in response.headers}
    assert "content-type" not in lowered


def test_account_win32_fallback_round_trips_through_the_api(client):
    from tests.support import account_values

    win32 = {
        "enabled": True,
        "dialog_class": "#32770",
        "anchors": ["ログインID :", "サーバー :"],
        "control_ids": {
            "login_id_combo": 1181,
            "login_id_edit": 1001,
            "otp": 1220,
            "server_combo": 1293,
            "server_edit": 1001,
            "login_button": 1,
        },
    }
    created = client.post(
        "/api/accounts",
        json={
            **account_values(),
            "display_name": "Rakuten-Win32",
            "alias": "W",
            "win32_fallback": win32,
        },
    )
    assert created.status_code == 201, created.text
    account = created.json()
    assert account["win32_fallback"] == win32

    fetched = client.get(f"/api/accounts/{account['id']}").json()
    assert fetched["win32_fallback"] == win32

    updated = client.put(f"/api/accounts/{account['id']}", json={"display_name": "Renamed"})
    assert updated.status_code == 200, updated.text
    assert updated.json()["win32_fallback"] == win32

    # The Win32 block must carry only dialog/class/anchor/id data, never secrets.
    assert set(win32["control_ids"]) == {
        "login_id_combo", "login_id_edit", "otp", "server_combo", "server_edit", "login_button",
    }
    assert "secrets" not in fetched
    assert "password" not in json_key_blob(fetched["win32_fallback"])


def test_account_rejects_win32_fallback_without_required_control_ids(client):
    from tests.support import account_values

    response = client.post(
        "/api/accounts",
        json={
            **account_values(),
            "display_name": "Bad",
            "alias": "B",
            "win32_fallback": {"enabled": True, "control_ids": {"otp": 1220}},
        },
    )
    assert response.status_code == 422
    assert "win32_fallback requires control_ids" in response.text


def test_account_rejects_unknown_win32_control_keys(client):
    from tests.support import account_values

    response = client.post(
        "/api/accounts",
        json={
            **account_values(),
            "display_name": "Bad",
            "alias": "C",
            "win32_fallback": {
                "enabled": True,
                "control_ids": {
                    "login_id_edit": 1001,
                    "otp": 1220,
                    "login_button": 1,
                    "secret": 7,
                },
            },
        },
    )
    assert response.status_code == 422
    assert "unsupported Win32 control keys" in response.text


def json_key_blob(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True).casefold()
