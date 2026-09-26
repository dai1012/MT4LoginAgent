from __future__ import annotations

import pytest

from app.main import create_application
from app.runtime import AgentRuntime, build_runtime


@pytest.fixture
def runtime(tmp_path) -> AgentRuntime:
    return build_runtime(tmp_path / "data")


@pytest.fixture
def account_payload() -> dict[str, object]:
    return {
        "display_name": "Rakuten-A",
        "alias": "A",
        "login_id": "LOCAL-LOGIN-ID",
        "server": "RakutenMT4-Demo",
        "terminal_path": r"C:\Rakuten\terminal.exe",
    }


@pytest.fixture
def client(runtime):
    from fastapi.testclient import TestClient

    app = create_application(runtime)
    token = runtime.ensure_web_admin_token()
    with TestClient(
        app,
        base_url="http://127.0.0.1",
        headers={"X-Admin-Token": token},
    ) as test_client:
        yield test_client
