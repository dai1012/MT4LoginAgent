"""Portable Account payloads for unit tests.

The tests create a real temporary .exe placeholder so Windows validator checks can
run without a Windows filesystem, while macOS/Linux still use the same payload.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

_TEST_ROOT = Path(tempfile.mkdtemp(prefix="mt4-login-agent-tests-"))
_TEST_TERMINAL = _TEST_ROOT / "terminal.exe"
_TEST_TERMINAL.write_bytes(b"test placeholder")
_TEST_PROFILE = _TEST_ROOT / "profile"
_TEST_PROFILE.mkdir(exist_ok=True)

ACCOUNT_DEFAULTS: dict[str, Any] = {
    "display_name": "Rakuten-A",
    "alias": "A",
    "login_id": "LOCAL-LOGIN-ID",
    "server": "RakutenMT4-Demo",
    "terminal_path": str(_TEST_TERMINAL),
    "profile_path": str(_TEST_PROFILE),
    "window_title_regex": r"(?i)^.*login.*$",
    "success_window_title_regex": r"^Rakuten authenticated$",
    "control_ids": {
        "login_id": "loginIdEdit",
        "otp": "otpEdit",
        "server": "serverCombo",
        "login_button": "loginButton",
    },
}


def instance_paths(alias: str) -> dict[str, str]:
    """Return a distinct, on-disk terminal file and profile directory for an alias.

    MetaTrader runs each concurrently open account from its own terminal
    installation, and the catalog refuses to let two enabled Accounts resolve to the
    same instance. The paths must really exist, otherwise the Windows validator
    rejects the account for a missing terminal file or profile directory before the
    guard is ever reached.
    """
    terminal = _TEST_ROOT / f"terminal-{alias}.exe"
    terminal.write_bytes(b"test placeholder")
    profile = _TEST_PROFILE / alias
    profile.mkdir(parents=True, exist_ok=True)
    return {"terminal_path": str(terminal), "profile_path": str(profile)}


def account_values(**overrides: Any) -> dict[str, Any]:
    values = {**ACCOUNT_DEFAULTS, **overrides}
    control_ids = {**ACCOUNT_DEFAULTS["control_ids"], **overrides.get("control_ids", {})}
    values["control_ids"] = control_ids
    return values


__all__ = ["ACCOUNT_DEFAULTS", "account_values", "instance_paths"]
