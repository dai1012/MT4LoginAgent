from __future__ import annotations

import json

import pytest

from app.config.paths import AppPaths
from app.main import create_application
from app.runtime import AgentRuntime, build_runtime
from tests.support import account_values


@pytest.fixture
def runtime(tmp_path) -> AgentRuntime:
    return build_runtime(tmp_path / "data")


@pytest.fixture
def mock_runtime(tmp_path) -> AgentRuntime:
    """A runtime whose automation is the scripted one, on any operating system.

    The runtime picks its automation while it is being constructed, from the settings
    on disk, so a test cannot change that by patching sys.platform afterwards. Tests
    about orchestration and completion need the scripted automation rather than the
    real Windows one, so they ask for it explicitly instead of relying on where they
    happen to run.
    """
    data_dir = tmp_path / "data"
    paths = AppPaths.from_value(data_dir)
    paths.ensure()
    paths.settings_file.write_text(
        json.dumps({"automation_mode": "mock"}), encoding="utf-8"
    )
    return build_runtime(data_dir)


@pytest.fixture
def account_payload() -> dict[str, object]:
    return account_values()


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
