from __future__ import annotations

from app.config.paths import AppPaths
from app.config.repositories import SettingsRepository
from app.models.domain import AppSettings
from app.testing.__main__ import web_url_for_data_dir


def test_test_runner_web_url_uses_configured_port(tmp_path):
    paths = AppPaths(tmp_path / "data")
    paths.ensure()
    SettingsRepository(paths).set(AppSettings(web_port=9123))
    assert web_url_for_data_dir(str(paths.data_dir)) == "http://127.0.0.1:9123/#windows-test"
