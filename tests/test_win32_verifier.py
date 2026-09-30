"""Success verification must observe the terminal the way the login wrote it.

Real hardware showed Rakuten MT4 logging in successfully while the Agent recorded a
failure. The login form is written through native Win32 calls, but the success check
observed windows through UI Automation, which is blind for that broker's dialog on the
same machine. These tests pin the fix: on the Win32 route the observation channel is
the native enumeration, it is scoped to the account's own pids, and a missing success
expression is reported immediately instead of after burning the whole login timeout.
"""

from __future__ import annotations

import re
import time

import pytest

from app.models.domain import AccountConfig, ErrorCategory, LoginStatus
from app.mt4.windows_automation import WindowsAutomation
from tests.support import account_values

SUCCESS_TITLE = "RakutenSecurities-Demo - 12345678 - Rakuten Securities, Inc."
SUCCESS_REGEX = r"^RakutenSecurities\-Demo\ \-\ .*\ \-\ Rakuten\ Securities,\ Inc\.$"


def make_account(**changes):
    values = {
        **account_values(),
        # The model requires at least 5s. Using the minimum keeps a deliberately
        # unconfirmed case from stalling the suite for the production timeout.
        "login_timeout_seconds": 5,
        "launch_timeout_seconds": 5,
        "window_title_regex": r"^Rakuten MetaTrader 4$",
        "success_window_title_regex": SUCCESS_REGEX,
        "win32_fallback": {
            "enabled": True,
            "dialog_class": "#32770",
            "anchors": ["ログインID", "パスワード", "サーバー"],
            "control_ids": {
                "login_id_combo": 1181,
                "login_id_edit": 1001,
                "otp": 1220,
                "server_combo": 1293,
                "server_edit": 1001,
                "login_button": 1,
            },
        },
    }
    values.update(changes)
    return AccountConfig.model_validate(values)


class ClosedDialog:
    """A login dialog that has already gone away."""

    def exists(self):
        return False


class OpenDialog:
    def exists(self):
        return True


def make_adapter(rows, *, uia_windows=(), uia_success=None, uia_failure=None):
    """An adapter whose UI Automation channel is blind unless a test supplies one."""
    adapter = object.__new__(WindowsAutomation)
    adapter._enum_top_windows = lambda: list(rows)
    adapter._windows = lambda pids: list(uia_windows)
    adapter._success_window_state = lambda pids, account: dict(uia_success or {})
    adapter._visible_window_state = lambda pids: dict(uia_failure or {})
    return adapter


def wait(adapter, account, dialog, *, success_before=None, failure_before=None):
    return adapter._wait_for_result(
        [27784],
        dialog,
        account,
        success_before or {},
        failure_before or {},
    )


# (1) The reported failure: UIA returns nothing, native sees the authenticated window.


def test_win32_route_succeeds_from_the_native_title_when_uia_is_blind():
    account = make_account()
    # The main window already carried the authenticated title before this attempt,
    # which is the real case: the title does not transition, it is simply still there.
    before = {f"handle:{1001}": SUCCESS_TITLE}
    adapter = make_adapter([(1001, 27784, "MetaQuotes::MetaTrader", SUCCESS_TITLE)])
    status, category, _message, token = wait(
        adapter, account, ClosedDialog(), success_before=before
    )
    assert status is LoginStatus.SUCCESS
    assert category is ErrorCategory.NONE
    assert token == "authenticated_window_present_after_login_closed"


def test_win32_route_succeeds_when_the_title_transitions():
    account = make_account()
    before = {f"handle:{1001}": "Rakuten MetaTrader 4"}
    adapter = make_adapter([(1001, 27784, "MetaQuotes::MetaTrader", SUCCESS_TITLE)])
    status, _category, _message, token = wait(
        adapter, account, ClosedDialog(), success_before=before
    )
    assert status is LoginStatus.SUCCESS
    assert token == "authenticated_window_transition"


# (2) A window that never matches the anchored expression still fails closed.


def test_win32_route_does_not_claim_success_from_an_unmatched_title():
    account = make_account()
    # A renamed or freshly opened window must not be mistaken for an authenticated
    # one: the anchored expression has to filter the native titles too.
    adapter = make_adapter(
        [(1001, 27784, "MetaQuotes::MetaTrader", "Rakuten MetaTrader 4")]
    )
    started = time.monotonic()
    status, category, _message, _token = wait(adapter, account, ClosedDialog())
    assert status is LoginStatus.FAILED
    assert category is ErrorCategory.UI_VERIFICATION_UNVERIFIED
    assert time.monotonic() - started < account.login_timeout_seconds + 5


def test_win32_route_requires_the_expression_and_not_merely_a_title_change():
    account = make_account()
    before = {f"handle:{1001}": "MetaEditor"}
    adapter = make_adapter(
        [(1001, 27784, "MetaQuotes::MetaTrader", "Rakuten MetaTrader 4")]
    )
    status, category, _message, _token = wait(
        adapter, account, ClosedDialog(), success_before=before
    )
    assert status is LoginStatus.FAILED
    assert category is ErrorCategory.UI_VERIFICATION_UNVERIFIED


# (3) Failure wording is still detected, natively, on the Win32 route.


def test_win32_route_detects_a_broker_rejection_from_the_native_title():
    account = make_account()
    rows = [(1002, 27784, "#32770", "サーバーへ接続できません - Rakuten")]
    adapter = make_adapter(rows)
    status, category, message, _token = wait(adapter, account, OpenDialog())
    assert status is LoginStatus.FAILED
    assert category is ErrorCategory.BROKER_OFFLINE
    assert "rejected" in message


# (4) Multi-instance isolation: another terminal's title must not confirm this one.


def test_another_terminals_authenticated_title_cannot_confirm_this_account():
    account = make_account()
    adapter = make_adapter([(2002, 99999, "MetaQuotes::MetaTrader", SUCCESS_TITLE)])
    status, category, _message, _token = wait(adapter, account, ClosedDialog())
    assert status is LoginStatus.FAILED
    assert category is ErrorCategory.UI_VERIFICATION_UNVERIFIED


def test_native_window_state_is_strictly_pid_scoped():
    adapter = make_adapter([])
    rows = [
        (10, 111, "#32770", "Rakuten MetaTrader 4"),
        (11, 222, "MetaQuotes::MetaTrader", SUCCESS_TITLE),  # other terminal
        (12, 111, "MetaQuotes::MetaTrader", SUCCESS_TITLE),
        (13, 333, "x", ""),  # other terminal, and untitled
    ]
    adapter._enum_top_windows = lambda: list(rows)
    state = adapter._native_window_state([111])
    assert state == {
        "handle:10": "Rakuten MetaTrader 4",
        "handle:12": SUCCESS_TITLE,
    }


# (5) A missing success expression is reported at once, not after the whole timeout.


def test_missing_success_regex_fails_fast_without_burning_the_timeout():
    account = make_account(success_window_title_regex="")
    adapter = make_adapter([(1001, 27784, "MetaQuotes::MetaTrader", SUCCESS_TITLE)])
    started = time.monotonic()
    status, category, message, _token = wait(adapter, account, ClosedDialog())
    elapsed = time.monotonic() - started
    assert status is LoginStatus.FAILED
    assert category is ErrorCategory.UI_VERIFICATION_UNVERIFIED
    assert "success_window_title_regex" in message
    assert elapsed < 2.0, "must not wait out login_timeout_seconds"


def test_a_present_title_alone_cannot_rescue_a_missing_success_regex():
    account = make_account(success_window_title_regex="")
    adapter = make_adapter([(1001, 27784, "MetaQuotes::MetaTrader", SUCCESS_TITLE)])
    status, category, _message, _token = wait(adapter, account, ClosedDialog())
    assert status is LoginStatus.FAILED
    assert category is ErrorCategory.UI_VERIFICATION_UNVERIFIED


# (6) The UIA primary route is untouched.


def test_uia_route_still_uses_uia_and_is_not_rewired_to_native():
    account = make_account(win32_fallback={"enabled": False})
    seen = {}

    class Probe(WindowsAutomation):
        pass

    adapter = object.__new__(Probe)
    adapter._enum_top_windows = lambda: seen.setdefault("native", []) or []
    adapter._windows = lambda pids: []
    adapter._success_window_state = lambda pids, acct: seen.setdefault("uia", {}) or {}
    adapter._visible_window_state = lambda pids: {}
    wait(adapter, account, ClosedDialog())
    assert "native" not in seen, "a UIA account must not be observed natively"
    assert seen.get("uia") == {}, "a UIA account must be observed through UIA"


def test_uia_route_still_confirms_success_through_uia():
    account = make_account(win32_fallback={"enabled": False})
    before = {"handle:7": SUCCESS_TITLE}
    adapter = make_adapter([], uia_success={"handle:7": SUCCESS_TITLE})
    status, _category, _message, token = wait(
        adapter, account, ClosedDialog(), success_before=before
    )
    assert status is LoginStatus.SUCCESS
    assert token == "authenticated_window_present_after_login_closed"


# (7) A Win32 account that is actually UIA driven keeps working through the fallback.


def test_win32_route_still_tries_uia_once_at_the_end():
    account = make_account()
    adapter = make_adapter([], uia_success={"handle:9": SUCCESS_TITLE})
    status, _category, _message, token = wait(adapter, account, ClosedDialog())
    assert status is LoginStatus.SUCCESS
    assert token == "authenticated_window_present_after_login_closed"


# (8) The outer budget message must not blame UI Automation any more.


def test_outer_wait_budget_message_does_not_blame_ui_automation():
    import inspect

    # The budget lives in the isolated worker wrapper that login() delegates to.
    source = inspect.getsource(WindowsAutomation._login_isolated)
    assert "Windows automation worker exceeded its hard wait budget" in source
    assert "Windows UI Automation exceeded its hard wait budget" not in source


# (9) No control text is read on the native observation path.


def test_native_observation_reads_only_top_level_window_metadata():
    calls = []

    def enum_rows():
        calls.append("enum")
        return [(1001, 27784, "MetaQuotes::MetaTrader", SUCCESS_TITLE)]

    adapter = make_adapter([])
    adapter._enum_top_windows = enum_rows
    state = adapter._native_window_state([27784])
    assert calls == ["enum"]
    assert all(key.startswith("handle:") for key in state)
    # No value-bearing accessors are involved: the fake has none of them.
    assert not hasattr(adapter, "get_value")


@pytest.mark.parametrize(
    ("title", "matches"),
    [
        (SUCCESS_TITLE, True),
        ("Rakuten MetaTrader 4", False),
        ("", False),
    ],
)
def test_success_expression_matches_only_the_anchored_title(title, matches):
    assert bool(re.search(SUCCESS_REGEX, title)) is matches
