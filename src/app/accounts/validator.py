from __future__ import annotations

import os
import re
import sys
from pathlib import Path, PureWindowsPath

from app.models.api import ValidationCheck, ValidationResult
from app.models.domain import AccountConfig, is_anchored_regex, is_safe_window_regex


def validate_account_configuration(account: AccountConfig) -> ValidationResult:
    checks: list[ValidationCheck] = []
    checks.append(
        ValidationCheck(
            name="required_fields",
            passed=bool(
                account.display_name
                and account.alias
                and account.login_id
                and account.server
                and account.terminal_path
            ),
            detail="Required account fields are present",
        )
    )

    path_value = account.terminal_path
    is_windows_path = bool(re.match(r"^[A-Za-z]:[\\/]", path_value))
    path_object = PureWindowsPath(path_value) if is_windows_path else Path(path_value)
    executable_suffix = path_object.suffix.casefold() == ".exe"
    checks.append(
        ValidationCheck(
            name="executable_extension",
            passed=executable_suffix,
            detail="terminal path must end with .exe"
            if not executable_suffix
            else "Executable extension is .exe",
        )
    )

    if sys.platform == "win32":
        window_title_present = bool(account.window_title_regex)
        checks.append(
            ValidationCheck(
                name="window_title_regex_present",
                passed=window_title_present,
                detail="Windows requires an explicit login-window title expression",
            )
        )
        absolute_path = path_object.is_absolute()
        checks.append(
            ValidationCheck(
                name="terminal_path_absolute",
                passed=absolute_path,
                detail="Windows terminal path is absolute"
                if absolute_path
                else "Windows terminal path must be absolute to avoid launching the wrong binary",
            )
        )
        exists = Path(path_value).expanduser().is_file()
        checks.append(
            ValidationCheck(
                name="terminal_exists",
                passed=exists,
                detail="Configured terminal.exe exists"
                if exists
                else "Configured file was not found on this Windows PC",
            )
        )
    else:
        checks.append(
            ValidationCheck(
                name="terminal_exists",
                passed=True,
                detail="Existence is deferred to the Windows real-machine test",
            )
        )

    if sys.platform == "win32":
        profile_present = bool(account.profile_path)
        checks.append(
            ValidationCheck(
                name="profile_path_present",
                passed=profile_present,
                detail="Windows requires a Profile path to disambiguate MT4 instances",
            )
        )
    if account.profile_path and sys.platform == "win32":
        profile_absolute = Path(account.profile_path).expanduser().is_absolute()
        checks.append(
            ValidationCheck(
                name="profile_path_absolute",
                passed=profile_absolute,
                detail="Windows profile path is absolute"
                if profile_absolute
                else "Windows profile path must be absolute",
            )
        )
        profile_exists = Path(account.profile_path).expanduser().is_dir()
        checks.append(
            ValidationCheck(
                name="profile_exists",
                passed=profile_exists,
                detail="Profile directory exists"
                if profile_exists
                else "Configured profile directory was not found",
            )
        )
    else:
        checks.append(
            ValidationCheck(
                name="profile_exists",
                passed=True,
                detail="Optional profile is syntactically acceptable",
            )
        )

    regex_valid = is_safe_window_regex(account.window_title_regex)
    checks.append(
        ValidationCheck(
            name="window_title_regex",
            passed=regex_valid,
            detail="Window title expression is valid and bounded",
        )
    )
    success_regex_valid = is_safe_window_regex(account.success_window_title_regex)
    success_expression_present = bool(account.success_window_title_regex)
    success_check_passed = success_regex_valid and (
        sys.platform != "win32"
        or (success_expression_present and is_anchored_regex(account.success_window_title_regex))
    )
    checks.append(
        ValidationCheck(
            name="success_window_title_regex",
            passed=success_check_passed,
            detail=(
                "Authenticated main-window expression is valid"
                if success_check_passed
                else (
                    "Windows requires an anchored authenticated main-window expression "
                    "for verified success"
                )
            ),
        )
    )
    if sys.platform == "win32":
        for field in ("login_id", "otp", "server", "login_button"):
            has_selector = bool(account.control_ids.get(field))
            checks.append(
                ValidationCheck(
                    name=f"{field}_selector",
                    passed=has_selector,
                    detail=(
                        f"Explicit {field} UIA selector is configured"
                        if has_selector
                        else f"Windows requires an explicit selector for {field}"
                    ),
                )
            )

    if (
        os.name == "nt"
        and account.process_name
        and not account.process_name.lower().endswith(".exe")
    ):
        checks.append(
            ValidationCheck(
                name="process_name",
                passed=False,
                detail="process_name should normally end with .exe",
            )
        )
    else:
        checks.append(
            ValidationCheck(
                name="process_name", passed=True, detail="Optional process name is acceptable"
            )
        )

    return ValidationResult(valid=all(check.passed for check in checks), checks=checks)
