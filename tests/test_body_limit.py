from __future__ import annotations

import pytest

from app.web.body_limit import RequestBodyLimitMiddleware


@pytest.mark.asyncio
async def test_body_limit_counts_chunked_request_without_content_length():
    seen = []

    async def app(scope, receive, send):
        seen.append(True)
        assert await receive()["body"] == b""

    messages = [
        {"type": "http.request", "body": b"x" * 100, "more_body": True},
        {"type": "http.request", "body": b"x" * 100, "more_body": False},
    ]
    sent = []

    async def receive():
        return messages.pop(0)

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "headers": [],
        "path": "/api/accounts",
    }
    await RequestBodyLimitMiddleware(app, max_bytes=150)(scope, receive, send)
    assert seen == []
    assert sent[0]["status"] == 413


@pytest.mark.asyncio
async def test_body_limit_replays_accepted_body():
    bodies = []

    async def app(scope, receive, send):
        bodies.append((await receive())["body"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    messages = [{"type": "http.request", "body": b"hello", "more_body": False}]
    sent = []

    async def receive():
        return messages.pop(0)

    async def send(message):
        sent.append(message)

    await RequestBodyLimitMiddleware(app, max_bytes=150)(
        {"type": "http", "method": "POST", "headers": [], "path": "/api/accounts"},
        receive,
        send,
    )
    assert bodies == [b"hello"]
    assert sent[0]["status"] == 200
