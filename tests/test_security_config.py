from __future__ import annotations

import json
import logging
import os
import stat
from dataclasses import asdict
from pathlib import Path

import pytest
from pydantic import SecretStr

from app.config.repositories import SecretsRepository, SettingsRepository
from app.config.service import SettingsService
from app.models.domain import AppSettings, LoginRequest
from app.security.logging_config import configure_logging
from app.security.redaction import RedactingFilter, redact_text, sensitive_values

POSIX = os.name == "posix"


def _writable(path: Path) -> bool:
    """The property the Agent actually depends on for its own files.

    On Windows ``chmod`` only toggles the read-only attribute, so the mode bits read
    0666 for any writable file no matter what mode was requested. Asserting 0600 there
    would be asserting something untrue, and pretending otherwise would hide the real
    gap. What is assertable is the actual requirement: the hardening must not lock the
    Agent out of its own secrets and log files. The equivalent Windows guarantee is the
    inherited ACL, which Python does not expose and this project does not tighten, so it
    is documented rather than claimed.
    """
    return bool(path.stat().st_mode & stat.S_IWRITE)


def test_secret_file_permission_assertion_matches_platform_semantics(tmp_path):
    """The 0600 guarantee is a POSIX guarantee; Windows only exposes writability."""
    target = tmp_path / "probe.json"
    target.write_text("{}", encoding="utf-8")
    target.chmod(0o600)
    if POSIX:
        assert oct(target.stat().st_mode & 0o777) == "0o600"
    else:
        # Asking for 0600 must not have made the file read-only on Windows.
        assert _writable(target)

def test_redacts_otp_command_and_slack_tokens():
    assert "123456" not in redact_text("command=/mt4 A 123456", ("123456",))
    assert "xoxb-secret" not in redact_text("token=xoxb-secret-value")
    assert "135791" not in redact_text('{"command": "/mt4", "text": "A 135791"}')
    assert "246813" not in redact_text("{'text': 'A 246813'}")
    with sensitive_values("987654"):
        assert "987654" not in redact_text("OTP 987654")
    with sensitive_values("839214"):
        pass
    assert "839214" not in redact_text("login failed for 839214")
    assert "839214" not in redact_text("text=%2Fmt4+A+839214")
    assert "839214" not in redact_text("A 839214")


def test_live_secret_registry_covers_more_otps_than_the_queue_limit():
    for index in range(40):
        with sensitive_values(f"queued{index:06d}"):
            pass
    assert "queued000000" not in redact_text("queued000000")


def test_redacting_filter_handles_log_args(caplog):
    logger = logging.getLogger("test.redaction")
    logger.addFilter(RedactingFilter())
    with caplog.at_level(logging.INFO):
        logger.info("payload=%s", "/mt4 A 246810")
    assert "246810" not in caplog.text
    assert "<redacted>" in caplog.text


def test_login_request_repr_and_serialization_do_not_expose_otp():
    request = LoginRequest(
        sender_id="U1",
        target="A",
        otp=SecretStr("123456"),
        source="test",
    )
    assert "123456" not in repr(request)
    assert "123456" not in str(asdict(request))


def test_redacting_filter_removes_exception_dump(caplog):
    logger = logging.getLogger("test.redaction.exception")
    logger.addFilter(RedactingFilter())
    with caplog.at_level(logging.ERROR):
        try:
            raise ValueError("OTP 135790")
        except ValueError:
            logger.error("operation failed", exc_info=True)
    assert "135790" not in caplog.text
    assert "Traceback" not in caplog.text


def test_settings_and_secrets_are_separate_and_not_returned(tmp_path):
    from app.config.paths import AppPaths

    paths = AppPaths(tmp_path)
    paths.ensure()
    settings_repo = SettingsRepository(paths)
    secrets_repo = SecretsRepository(paths)
    service = SettingsService(settings_repo, secrets_repo)
    settings = settings_repo.get()
    assert settings.allowed_slack_user_ids == []
    secrets_repo.set(
        type(secrets_repo.get())(
            slack_app_token=SecretStr("xapp-private"),
            slack_bot_token=SecretStr("xoxb-private"),
        )
    )
    public = service.public_slack_settings()
    assert public.app_token_configured and public.bot_token_configured
    assert "xapp-private" not in public.model_dump_json()
    assert "xoxb-private" in paths.secrets_file.read_text(encoding="utf-8")
    assert (
        "xapp-private" not in paths.settings_file.read_text(encoding="utf-8")
        if paths.settings_file.exists()
        else True
    )
    if POSIX:
        assert oct(paths.secrets_file.stat().st_mode & 0o777) == "0o600"
    else:
        assert _writable(paths.secrets_file)


def test_runtime_log_files_are_private(tmp_path):
    log_dir = tmp_path / "logs"
    configure_logging(log_dir)
    logging.getLogger("test.private-log").error("no secrets here")
    log_file = log_dir / "agent.log"
    assert log_file.exists()
    if POSIX:
        assert oct(log_file.stat().st_mode & 0o777) == "0o600"
        assert oct(log_dir.stat().st_mode & 0o777) == "0o700"
    else:
        # The write bit is the only permission Windows expresses through stat().
        assert _writable(log_file), "the Agent must still be able to write its own log"
        assert _writable(log_dir)


def test_atomic_settings_round_trip(tmp_path):
    from app.config.paths import AppPaths
    from app.config.repositories import SettingsRepository

    paths = AppPaths(tmp_path)
    paths.ensure()
    repo = SettingsRepository(paths)
    repo.set(AppSettings(allowed_slack_user_ids=["U123456789"]))
    assert SettingsRepository(paths).get().allowed_slack_user_ids == ["U123456789"]


def test_legacy_otp_max_age_from_previous_schema_does_not_block_startup(tmp_path, caplog):
    from app.config.paths import AppPaths

    paths = AppPaths(tmp_path)
    paths.ensure()
    paths.settings_file.write_text('{"otp_max_age_seconds": 900}\n', encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        settings = SettingsRepository(paths).get()

    assert settings.otp_max_age_seconds == 300
    assert "not Rakuten broker OTP validity" in caplog.text


def test_legacy_migration_does_not_widen_the_model_stale_cutoff_cap():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AppSettings(otp_max_age_seconds=900)


@pytest.mark.parametrize("value", [29, 901])
def test_otp_max_age_outside_the_previous_schema_still_fails_loudly(tmp_path, value):
    from app.config.paths import AppPaths
    from app.models.errors import ConfigurationError

    paths = AppPaths(tmp_path)
    paths.ensure()
    paths.settings_file.write_text(
        json.dumps({"otp_max_age_seconds": value}) + "\n", encoding="utf-8"
    )

    with pytest.raises(ConfigurationError):
        SettingsRepository(paths).get()
