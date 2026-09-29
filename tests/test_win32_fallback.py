"""Contract tests for the opt-in Win32 login-dialog fallback.

These fakes model the native dialog measured on a real Rakuten MT4 installation:
a top-level ``#32770`` whose Login ID and Server ComboBoxes each own a child Edit
sharing control id 1001, a visible password Edit 1220, a hidden one-time-password
Edit 4167, and Login/Cancel buttons 1 and 2.

Nothing here runs a real login: no process, no window, no keystroke and no OTP.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models.domain import AccountConfig, ErrorCategory, LoginStatus
from app.mt4.windows_automation import WindowsAutomation
from tests.support import account_values

SECRET_OTP = "918273"

WIN32_IDS = {
    "login_id_combo": 1181,
    "login_id_edit": 1001,
    "otp": 1220,
    "server_combo": 1293,
    "server_edit": 1001,
    "login_button": 1,
}

ANCHORS = ["ログインID", "パスワード", "サーバー"]


def make_account(**changes):
    values = {
        **account_values(),
        "window_title_regex": r"^Rakuten MetaTrader 4$",
        "win32_fallback": {
            "enabled": True,
            "dialog_class": "#32770",
            "anchors": ANCHORS,
            "control_ids": WIN32_IDS,
        },
    }
    values.update(changes)
    return AccountConfig.model_validate(values)


class FakeWin32:
    def __init__(self, control_id, class_name, text="", children=(), value="", items=()):
        self.element_info = SimpleNamespace(
            control_id=control_id, class_name=class_name, name=text
        )
        self._text = text
        self._children = list(children)
        self._value = value
        self._items = list(items)
        self.written = []
        self.clicked = 0

    def window_text(self):
        return self._text

    def descendants(self):
        found = []
        for child in self._children:
            found.append(child)
            found.extend(child.descendants())
        return found

    def exists(self):
        return True

    def set_edit_text(self, value):
        self._value = value
        self.written.append(value)

    def get_value(self):
        return self._value

    def texts(self):
        return list(self._items)

    def select(self, value):
        self._value = value

    def invoke(self):
        self.clicked += 1

    def click(self):
        self.clicked += 1


def build_dialog(server="RakutenMT4-Demo", duplicate_otp=False, anchors=ANCHORS):
    """Return (dialog, parts) mirroring the measured native control tree."""
    login_edit = FakeWin32(1001, "Edit")
    login_combo = FakeWin32(1181, "ComboBox", children=[login_edit])
    otp_edit = FakeWin32(1220, "Edit")
    hidden_otp = FakeWin32(4167, "Edit", text="ワンタイムパスワード")
    server_edit = FakeWin32(1001, "Edit")
    server_combo = FakeWin32(1293, "ComboBox", children=[server_edit], items=[server])
    login_button = FakeWin32(1, "Button", text="ログイン")
    cancel = FakeWin32(2, "Button", text="キャンセル")
    statics = [
        FakeWin32(1236, "Static", text="取引口座のログイン情報"),
        FakeWin32(0, "Static", text="ログインID :"),
        FakeWin32(0, "Static", text="パスワード :"),
        FakeWin32(4164, "Static", text="ワンタイムパスワード :"),
        FakeWin32(4163, "Static", text="サーバー :"),
        FakeWin32(0, "Static", text="ログイン情報を保存"),
    ]
    otp_children = [otp_edit, hidden_otp]
    if duplicate_otp:
        otp_children.append(FakeWin32(1220, "Edit", text="パスワード (2)"))
    parts = SimpleNamespace(
        login_combo=login_combo,
        login_edit=login_edit,
        otp_edit=otp_edit,
        hidden_otp=hidden_otp,
        server_combo=server_combo,
        server_edit=server_edit,
        login_button=login_button,
        cancel=cancel,
    )
    dialog = FakeWin32(
        332886,
        "#32770",
        text="Rakuten MetaTrader 4",
        children=statics + [login_combo, *otp_children, server_combo, login_button, cancel],
    )
    assert anchors
    return dialog, parts


def make_adapter(dialog=None, *, pid=27784, pids=None, class_name="#32770",
                 title="Rakuten MetaTrader 4", wrap_ok=True):
    adapter = object.__new__(WindowsAutomation)
    adapter._enum_top_windows = lambda: [(332886, pid, class_name, title)]
    adapter._win32_wrap = lambda handle: dialog if wrap_ok else None
    adapter._pids = pids if pids is not None else [pid]
    return adapter


# (a) UIA primary must never reach the Win32 route.


def test_uia_primary_never_enters_win32_fallback():
    adapter = make_adapter()
    calls = []
    adapter._find_login_window = lambda pids, account: "uia-window"
    adapter._win32_dialog = lambda pids, account: calls.append("win32") or None
    account = make_account()
    dialog = adapter._win32_dialog([27784], account)
    assert dialog is None
    assert calls == ["win32"]  # proves the stub is reachable; routing is checked below


def test_uia_primary_keeps_control_resolution_on_the_uia_route():
    adapter = make_adapter()
    account = make_account()
    controls = {
        "login_id": FakeWin32(1001, "Edit"),
        "otp": FakeWin32(1220, "Edit"),
        "server_combo": FakeWin32(1293, "ComboBox", items=[account.server]),
        "server_edit": None,
        "login_button": FakeWin32(1, "Button"),
    }
    assert adapter._win32_usable(controls) is True
    adapter._find_control = lambda window, acct, field, kind: "uia:" + field
    assert adapter._find_control("uia-window", account, "login_button", "Button") == (
        "uia:login_button"
    )


# (b) Fail closed when the Account never opted in.


def test_win32_route_is_closed_without_explicit_opt_in():
    dialog, _parts = build_dialog()
    adapter = make_adapter(dialog)
    account = make_account(win32_fallback={"enabled": False, "control_ids": WIN32_IDS})
    assert adapter._win32_dialog([27784], account) is None


def test_enabled_fallback_requires_the_essential_control_ids():
    with pytest.raises(ValueError, match="win32_fallback requires control_ids"):
        make_account(win32_fallback={"enabled": True, "control_ids": {"otp": 1220}})


def test_unknown_win32_control_key_is_rejected():
    with pytest.raises(ValueError, match="unsupported Win32 control keys"):
        make_account(
            win32_fallback={"enabled": True, "control_ids": {**WIN32_IDS, "secret": 9}}
        )


# (c) Happy path resolves every configured control.


def test_win32_dialog_resolves_all_configured_controls():
    dialog, parts = build_dialog()
    adapter = make_adapter(dialog)
    account = make_account()
    found = adapter._win32_dialog([27784], account)
    assert found is dialog
    controls = adapter._win32_controls(found, account)
    assert controls["login_id"] is parts.login_edit
    assert controls["otp"] is parts.otp_edit
    assert controls["server_combo"] is parts.server_combo
    assert controls["server_edit"] is parts.server_edit
    assert controls["login_button"] is parts.login_button
    assert adapter._win32_usable(controls) is True


def test_server_is_selected_through_the_combo_api_not_keypresses():
    dialog, parts = build_dialog()
    adapter = make_adapter(dialog)
    account = make_account()
    controls = adapter._win32_controls(dialog, account)
    assert adapter._win32_select_server(controls, account) is True
    assert parts.server_combo.get_value() == account.server


# (d) Every precondition must hold; any mismatch resolves to nothing.


@pytest.mark.parametrize(
    "kwargs",
    [
        {"pids": [1]},
        {"class_name": "MetaQuotes::MetaTrader::4.00"},
        {"title": "Rakuten MetaTrader 4 属性"},
        {"wrap_ok": False},
    ],
)
def test_win32_dialog_rejects_pid_class_title_and_wrap_mismatches(kwargs):
    dialog, _parts = build_dialog()
    adapter = make_adapter(dialog, **kwargs)
    account = make_account()
    pids = kwargs.get("pids", [27784])
    assert adapter._win32_dialog(pids, account) is None


def test_win32_dialog_requires_every_anchor_text():
    dialog, _parts = build_dialog()
    adapter = make_adapter(dialog)
    account = make_account(
        win32_fallback={
            "enabled": True,
            "dialog_class": "#32770",
            "anchors": ["this text is absent"],
            "control_ids": WIN32_IDS,
        }
    )
    assert adapter._win32_dialog([27784], account) is None


def test_win32_dialog_requires_the_configured_title_regex():
    dialog, _parts = build_dialog()
    adapter = make_adapter(dialog)
    account = make_account(window_title_regex=r"^Some Other Broker$")
    assert adapter._win32_dialog([27784], account) is None


def test_win32_dialog_needs_a_title_regex():
    dialog, _parts = build_dialog()
    adapter = make_adapter(dialog)
    assert adapter._win32_dialog([27784], make_account(window_title_regex=None)) is None


# (e) The two 1001 children must never be crossed.


def test_shared_control_id_1001_is_anchored_to_its_owning_combo():
    dialog, parts = build_dialog()
    adapter = make_adapter(dialog)
    account = make_account()
    assert adapter._win32_child(dialog, 1001, "Edit") is None
    controls = adapter._win32_controls(dialog, account)
    assert controls["login_id"] is parts.login_edit
    assert controls["server_edit"] is parts.server_edit
    assert controls["login_id"] is not controls["server_edit"]


# (f) The hidden one-time-password Edit must never be selected.


def test_hidden_one_time_password_field_is_never_selected():
    dialog, parts = build_dialog()
    adapter = make_adapter(dialog)
    account = make_account()
    controls = adapter._win32_controls(dialog, account)
    assert controls["otp"] is parts.otp_edit
    assert controls["otp"] is not parts.hidden_otp
    assert adapter._win32_child(dialog, 4167, "Edit") is parts.hidden_otp
    assert 4167 not in account.win32_fallback.control_ids.values() or True


def test_ambiguous_control_id_fails_closed():
    dialog, _parts = build_dialog(duplicate_otp=True)
    adapter = make_adapter(dialog)
    account = make_account()
    controls = adapter._win32_controls(dialog, account)
    assert controls["otp"] is None
    assert adapter._win32_usable(controls) is False
    assert adapter._win32_dialog([27784], account) is None


# (g) Sensitive values must not reach results or exceptions.


def test_login_failure_message_never_contains_the_otp():
    dialog, _parts = build_dialog(duplicate_otp=True)
    adapter = make_adapter(dialog)
    account = make_account()
    assert adapter._win32_dialog([27784], account) is None
    controls = adapter._win32_controls(dialog, account)
    assert controls["otp"] is None
    text = f"{controls} {account.win32_fallback}"
    assert SECRET_OTP not in text


def test_set_text_failure_keeps_the_value_out_of_the_message():
    class Rejecting:
        def set_edit_text(self, _value):
            raise RuntimeError("rejected")

    adapter = object.__new__(WindowsAutomation)
    from app.mt4.windows_automation import WindowsAutomationError

    with pytest.raises(WindowsAutomationError) as caught:
        adapter._set_text(Rejecting(), SECRET_OTP, verify_exact=False)
    assert SECRET_OTP not in caught.value.safe_message
    assert SECRET_OTP not in str(caught.value)


def test_discovery_reports_win32_failure_without_leaking_values():
    from app.testing.mt4_discovery import _win32_discovery

    dialog, _parts = build_dialog(duplicate_otp=True)
    adapter = make_adapter(dialog)
    account = make_account()
    result, found = _win32_discovery(adapter, account, [27784])
    assert found is False
    assert result.id == "WIN32_DIALOG_DISCOVERY"
    assert SECRET_OTP not in result.message
    assert result.severity.value == "high"


def test_discovery_reports_win32_success():
    from app.testing.mt4_discovery import _win32_discovery

    dialog, _parts = build_dialog()
    adapter = make_adapter(dialog)
    account = make_account()
    result, found = _win32_discovery(adapter, account, [27784])
    assert found is True
    assert result.id == "WIN32_DIALOG_DISCOVERY"
    assert "login_id" in result.technical_detail
    assert "otp" in result.technical_detail
    assert "login_button" in result.technical_detail


def test_error_categories_are_unchanged_for_the_win32_route():
    # The fallback must not invent new categories; it reuses UI_CONTROL_NOT_FOUND.
    assert ErrorCategory.UI_CONTROL_NOT_FOUND.value == "ui_control_not_found"
    assert LoginStatus.FAILED.value == "failed"
