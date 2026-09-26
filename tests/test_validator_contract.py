from __future__ import annotations

from app.accounts import validator
from app.models.domain import AccountConfig


def test_windows_absolute_terminal_path_is_checked_when_platform_is_windows(monkeypatch):
    account = AccountConfig.model_validate(
        {
            "display_name": "A",
            "alias": "A",
            "login_id": "id",
            "server": "server",
            "terminal_path": r"C:\Rakuten\terminal.exe",
            "control_ids": {"login_id": "login", "otp": "otp"},
        }
    )
    monkeypatch.setattr(validator.sys, "platform", "win32")
    monkeypatch.setattr(validator.Path, "is_file", lambda self: False)
    result = validator.validate_account_configuration(account)
    terminal_check = next(item for item in result.checks if item.name == "terminal_exists")
    assert terminal_check.passed is False
    success_check = next(
        item for item in result.checks if item.name == "success_window_title_regex"
    )
    assert success_check.passed is False
    assert all(
        item.passed for item in result.checks if item.name in {"login_id_selector", "otp_selector"}
    )
    assert next(item for item in result.checks if item.name == "server_selector").passed is False
