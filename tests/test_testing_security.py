from __future__ import annotations

from pydantic import SecretStr

from app.config.paths import AppPaths
from app.models.domain import SecretsConfig
from app.testing.security import (
    contains_secret,
    sanitize_payload,
    scan_paths,
    scan_text,
    secret_candidates,
)


def test_secret_scan_and_sanitization_never_return_secret_values(tmp_path):
    secrets = SecretsConfig(
        slack_app_token=SecretStr("xapp-private-value"),
        slack_bot_token=SecretStr("xoxb-private-value"),
        web_admin_token=SecretStr("admin-private-value"),
        dedup_hmac_key=SecretStr("ab" * 32),
    )
    candidates = secret_candidates(secrets, otp="123456")
    assert "123456" in candidates
    assert contains_secret("command /mt4 A 123456", candidates)
    result = scan_text("agent log", "OTP 123456", candidates)
    assert result.status.value == "FAIL"
    assert "123456" not in result.technical_detail
    payload = sanitize_payload(
        {"message": "OTP 123456", "nested": ["xapp-private-value"]}, candidates
    )
    assert "123456" not in payload["message"]
    assert "xapp-private-value" not in payload["nested"][0]


def test_scan_paths_never_scans_secrets_file_as_a_false_positive(tmp_path):
    paths = AppPaths(tmp_path / "data")
    paths.ensure()
    paths.log_dir.joinpath("agent.log").write_text("no secrets here", encoding="utf-8")
    paths.history_file.write_text('{"status":"failed"}\n', encoding="utf-8")
    candidates = ("xapp-private-value",)
    results = scan_paths(paths, candidates)
    assert all(result.status.value == "PASS" for result in results)
    assert all("xapp-private-value" not in result.technical_detail for result in results)
