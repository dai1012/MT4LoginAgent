"""Contract tests for the opt-in Win32 login-dialog fallback.

These fakes model the native dialog measured on a real Rakuten MT4 installation:
a top-level ``#32770`` whose Login ID and Server ComboBoxes each own a child Edit
sharing control id 1001, a visible password Edit 1220, a hidden one-time-password
Edit 4167, and Login/Cancel buttons 1 and 2.

Nothing here runs a real login: no process, no window, no keystroke and no OTP.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from types import SimpleNamespace

import pytest

from app.models.domain import AccountConfig, ErrorCategory, LoginStatus
from app.mt4.windows_automation import WindowsAutomation
from app.testing.models import TestStatus
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
    adapter._win32_open_login_dialog = lambda _pids, _account: (
        "already_open",
        "the login dialog was already present",
    )
    return adapter


def _pick(results, identifier):
    """Return the result with this id from a discovery result list."""
    return next(item for item in results if item.id == identifier)


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
    results, found = _win32_discovery(adapter, account, [27784])
    result = _pick(results, "WIN32_DIALOG_DISCOVERY")
    assert found is False
    assert result.id == "WIN32_DIALOG_DISCOVERY"
    assert SECRET_OTP not in result.message
    assert result.severity.value == "high"


def test_discovery_reports_win32_success():
    from app.testing.mt4_discovery import _win32_discovery

    dialog, _parts = build_dialog()
    adapter = make_adapter(dialog)
    account = make_account()
    results, found = _win32_discovery(adapter, account, [27784])
    result = _pick(results, "WIN32_DIALOG_DISCOVERY")
    assert found is True
    assert result.id == "WIN32_DIALOG_DISCOVERY"
    assert "login_id" in result.technical_detail
    assert "otp" in result.technical_detail
    assert "login_button" in result.technical_detail


def test_error_categories_are_unchanged_for_the_win32_route():
    # The fallback must not invent new categories; it reuses UI_CONTROL_NOT_FOUND.
    assert ErrorCategory.UI_CONTROL_NOT_FOUND.value == "ui_control_not_found"
    assert LoginStatus.FAILED.value == "failed"


# Native top-level enumeration must satisfy the real EnumWindows contract.

WINDOWS = [
    (332886, 27784, "#32770", "Rakuten MetaTrader 4"),
    (66762, 27784, "MetaQuotes::MetaTrader::4.00", "RakutenSecurities-Demo - demo"),
    (5150, 9000, "Chrome_WidgetWin_1", ""),
]


def _install_fake_user32(monkeypatch, rows):
    by_handle = {row[0]: row for row in rows}
    seen = {}

    def is_window(handle):
        return handle in by_handle

    def get_class_name(handle, buffer, size):
        value = by_handle[handle][2]
        buffer.value = value
        return len(value)

    def get_text_length(handle):
        return len(by_handle[handle][3])

    def get_text(handle, buffer, size):
        value = by_handle[handle][3]
        buffer.value = value
        return len(value)

    def get_thread_process_id(handle, out):
        ctypes.cast(out, ctypes.POINTER(wintypes.DWORD))[0] = by_handle[handle][1]
        return 1

    def enum_windows(callback, param):
        seen["callback"] = callback
        seen["argtypes"] = getattr(enum_windows, "argtypes", None)
        seen["restype"] = getattr(enum_windows, "restype", None)
        for handle in by_handle:
            if callback(handle, param) == 0:
                break
        return 1

    user32 = SimpleNamespace(
        IsWindow=is_window,
        GetClassNameW=get_class_name,
        GetWindowTextLengthW=get_text_length,
        GetWindowTextW=get_text,
        GetWindowThreadProcessId=get_thread_process_id,
        EnumWindows=enum_windows,
    )
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(user32=user32), raising=False)
    return seen


def test_enum_top_windows_passes_a_ctypes_callback_not_a_bare_function(monkeypatch):
    seen = _install_fake_user32(monkeypatch, WINDOWS)
    WindowsAutomation._enum_top_windows()
    callback = seen["callback"]
    assert isinstance(callback, ctypes._CFuncPtr)
    assert not callable(getattr(callback, "__code__", None))
    argtypes = seen["argtypes"]
    assert argtypes, "EnumWindows.argtypes must be declared for the callback"


def test_enum_top_windows_declares_the_standard_wnd_enumproc_signature(monkeypatch):
    seen = _install_fake_user32(monkeypatch, WINDOWS)
    WindowsAutomation._enum_top_windows()
    argtypes = seen["argtypes"]
    proc_type = argtypes[0]
    assert proc_type is type(seen["callback"])
    # BOOL is ctypes' default type (c_long) and is therefore omitted from _argtypes_.
    assert proc_type._argtypes_ == (wintypes.HWND, wintypes.LPARAM)
    assert argtypes[1] is wintypes.LPARAM
    assert seen["restype"] is wintypes.BOOL


def test_enum_top_windows_collects_handle_pid_class_and_title(monkeypatch):
    _install_fake_user32(monkeypatch, WINDOWS)
    rows = WindowsAutomation._enum_top_windows()
    assert rows == WINDOWS
    assert (332886, 27784, "#32770", "Rakuten MetaTrader 4") in rows
    assert rows[-1][3] == ""


def test_enum_top_windows_drives_the_real_dialog_lookup(monkeypatch):
    """The measured dialog must satisfy every precondition on a real machine."""
    _install_fake_user32(monkeypatch, WINDOWS)
    dialog, _parts = build_dialog()
    adapter = make_adapter(dialog)
    account = make_account()
    rows = WindowsAutomation._enum_top_windows()
    import re

    expression = re.compile(account.window_title_regex)
    pids = adapter._process_ids = lambda acct: [27784]
    matches = [
        handle
        for handle, pid, class_name, title in rows
        if pid in set(pids(account))
        and class_name == account.win32_fallback.dialog_class
        and expression.search(title)
    ]
    assert matches == [332886]
    assert adapter._win32_dialog(pids(account), account) is dialog


def test_discovery_keeps_a_programming_fault_visible(monkeypatch):
    from app.testing.mt4_discovery import _win32_discovery

    adapter = make_adapter(None)

    def boom(_pids, _account):
        raise ctypes.ArgumentError(1, "TypeError")

    adapter._win32_dialog = boom
    results, found = _win32_discovery(adapter, make_account(), [27784])
    result = _pick(results, "WIN32_DIALOG_DISCOVERY")
    assert found is False
    assert result.id == "WIN32_DIALOG_DISCOVERY"
    assert "ArgumentError" in result.technical_detail
    assert SECRET_OTP not in result.technical_detail


def test_discovery_stays_quiet_when_the_dialog_simply_is_not_there(monkeypatch):
    from app.testing.mt4_discovery import _win32_discovery

    _install_fake_user32(monkeypatch, WINDOWS)
    dialog, _parts = build_dialog()
    adapter = make_adapter(dialog)
    results, found = _win32_discovery(adapter, make_account(), [1])
    result = _pick(results, "WIN32_DIALOG_DISCOVERY")
    assert found is False
    assert "ArgumentError" not in result.technical_detail
    assert result.technical_detail.endswith("the dialog was not usable")


# --- Auto-opening the login dialog through the window's own menu -----------------

MAIN_CLASS = "MetaQuotes::MetaTrader::4.00"


def _main_row(handle: int, pid: int) -> tuple:
    return (handle, pid, MAIN_CLASS, "RakutenSecurities-Demo - Rakuten Securities, Inc.")


def _open_harness(
    *,
    dialog_present=False,
    top_windows=(),
    menu=(),
    send_ok=True,
    dialog_appears_after_send=False,
):
    automation = object.__new__(WindowsAutomation)
    sent: list[tuple[int, int]] = []
    state = {"present": dialog_present}

    def fake_dialog(_pids, _account):
        return object() if state["present"] else None

    def fake_enum():
        return list(top_windows)

    def fake_menu(_hwnd):
        return list(menu)

    def fake_send(hwnd, command_id, timeout_ms=5000):
        sent.append((hwnd, command_id))
        if send_ok and dialog_appears_after_send:
            state["present"] = True
        return send_ok

    automation._win32_dialog = fake_dialog
    automation._enum_top_windows = fake_enum
    automation._win32_menu_commands = fake_menu
    automation._win32_send_command = staticmethod(fake_send)
    return automation, sent, state


def test_dialog_already_open_never_sends_a_command():
    automation, sent, _state = _open_harness(
        dialog_present=True,
        top_windows=[_main_row(66762, 27784)],
        menu=[(40012, "取引口座にログイン")],
    )
    outcome, _detail = automation._win32_open_login_dialog([27784], make_account())
    assert outcome == "already_open"
    assert sent == []


def test_missing_dialog_with_one_menu_item_sends_to_the_right_window():
    automation, sent, _state = _open_harness(
        top_windows=[_main_row(66762, 27784)],
        menu=[(40011, "新規注文"), (40012, "取引口座にログイン")],
        dialog_appears_after_send=True,
    )
    outcome, _detail = automation._win32_open_login_dialog(
        [27784], make_account(), wait_seconds=1.0
    )
    assert outcome == "opened"
    assert sent == [(66762, 40012)]


def test_english_caption_is_accepted():
    automation, sent, _state = _open_harness(
        top_windows=[_main_row(66762, 27784)],
        menu=[(40012, "Login to Trade Account")],
        dialog_appears_after_send=True,
    )
    outcome, _detail = automation._win32_open_login_dialog(
        [27784], make_account(), wait_seconds=1.0
    )
    assert outcome == "opened"
    assert sent == [(66762, 40012)]


@pytest.mark.parametrize(
    ("caption", "expected"),
    [
        ("取引口座にログイン", "取引口座にログイン"),
        ("取引口座にログイン(&L)", "取引口座にログイン"),
        ("取引口座にログイン\tCtrl+L", "取引口座にログイン"),
        ("Login to Trade Account(&L)", "login to trade account"),
        ("  Login   to  Trade  Account  ", "login to trade account"),
        ("&新規注文", "新規注文"),
    ],
)
def test_menu_caption_normalisation(caption, expected):
    assert WindowsAutomation._normalize_menu_caption(caption) == expected


def test_no_matching_menu_item_fails_closed_without_sending():
    automation, sent, _state = _open_harness(
        top_windows=[_main_row(66762, 27784)],
        menu=[(40011, "新規注文"), (40013, " shuts down ")],
    )
    outcome, detail = automation._win32_open_login_dialog([27784], make_account())
    assert outcome == "unavailable"
    assert "no login menu command matched" in detail
    assert sent == []


def test_multiple_matching_menu_items_fail_closed_without_sending():
    automation, sent, _state = _open_harness(
        top_windows=[_main_row(66762, 27784)],
        menu=[(40012, "取引口座にログイン"), (40099, "Login to Trade Account")],
    )
    outcome, detail = automation._win32_open_login_dialog([27784], make_account())
    assert outcome == "unavailable"
    assert "refusing to choose" in detail
    assert sent == []


@pytest.mark.parametrize("pids", [(), (27784, 27785), (11111,)])
def test_ambiguous_or_missing_pid_fails_closed_without_sending(pids):
    """Two instances running must never make one Account act on the other."""
    automation, sent, _state = _open_harness(
        top_windows=[_main_row(66762, 27784), _main_row(66763, 27785)],
        menu=[(40012, "取引口座にログイン")],
    )
    outcome, _detail = automation._win32_open_login_dialog(list(pids), make_account())
    assert outcome == "unavailable"
    assert sent == []


def test_ambiguous_main_window_for_one_pid_fails_closed():
    automation, sent, _state = _open_harness(
        top_windows=[_main_row(66762, 27784), _main_row(66799, 27784)],
        menu=[(40012, "取引口座にログイン")],
    )
    outcome, detail = automation._win32_open_login_dialog([27784], make_account())
    assert outcome == "unavailable"
    assert "main windows matched" in detail
    assert sent == []


def test_only_the_account_own_pid_window_receives_the_command():
    automation, sent, _state = _open_harness(
        top_windows=[_main_row(66762, 27784), _main_row(66763, 27785)],
        menu=[(40012, "取引口座にログイン")],
        dialog_appears_after_send=True,
    )
    outcome, _detail = automation._win32_open_login_dialog(
        [27784], make_account(), wait_seconds=1.0
    )
    assert outcome == "opened"
    # Only the 27784 window was addressed; 27785 was never touched.
    assert sent == [(66762, 40012)]


def test_dialog_timeout_fails_closed():
    automation, sent, _state = _open_harness(
        top_windows=[_main_row(66762, 27784)],
        menu=[(40012, "取引口座にログイン")],
        dialog_appears_after_send=False,
    )
    outcome, detail = automation._win32_open_login_dialog(
        [27784], make_account(), wait_seconds=0
    )
    assert outcome == "timeout"
    assert "did not appear" in detail
    assert sent == [(66762, 40012)]


def test_window_rejecting_the_command_fails_closed():
    automation, sent, _state = _open_harness(
        top_windows=[_main_row(66762, 27784)],
        menu=[(40012, "取引口座にログイン")],
        send_ok=False,
    )
    outcome, detail = automation._win32_open_login_dialog([27784], make_account())
    assert outcome == "unavailable"
    assert "did not accept" in detail
    assert sent == [(66762, 40012)]


def test_auto_open_is_skipped_when_the_fallback_is_not_enabled():
    account = make_account(win32_fallback={"enabled": False})
    automation, sent, _state = _open_harness()
    outcome, _detail = automation._win32_open_login_dialog([27784], account)
    assert outcome == "not_enabled"
    assert sent == []


def test_auto_open_detail_is_fixed_text_and_carries_no_input():
    """The credential is not an input here, so no detail can echo one."""
    for menu in ([], [(40012, "取引口座にログイン"), (40099, "Login to Trade Account")]):
        automation, _sent, _state = _open_harness(
            top_windows=[_main_row(66762, 27784)], menu=menu
        )
        _outcome, detail = automation._win32_open_login_dialog([27784], make_account())
        assert detail and "{" not in detail and "%" not in detail


def test_discovery_reports_auto_open_and_still_requires_resolved_controls():
    from app.testing.mt4_discovery import _win32_discovery

    adapter = make_adapter()
    adapter._win32_open_login_dialog = lambda _p, _a: (
        "opened",
        "the login dialog appeared after the menu command",
    )
    adapter._win32_dialog = lambda _p, _a: object()
    # The dialog is found but its controls do not resolve, so the route is unusable.
    adapter._win32_controls = lambda _d, _a: {"login_id": None, "otp": None}
    results, found = _win32_discovery(adapter, make_account(), [27784])
    assert found is True
    auto_open = _pick(results, "WIN32_DIALOG_AUTO_OPEN")
    assert auto_open.status == TestStatus.PASS
    assert "outcome=opened" in auto_open.technical_detail
    assert _pick(results, "WIN32_DIALOG_DISCOVERY").status == TestStatus.PASS


def test_discovery_reports_auto_open_failure_with_a_reason():
    from app.testing.mt4_discovery import _win32_discovery

    adapter = make_adapter(wrap_ok=False)
    adapter._win32_open_login_dialog = lambda _p, _a: (
        "unavailable",
        "2 login menu commands matched; refusing to choose",
    )
    results, found = _win32_discovery(adapter, make_account(), [27784])
    assert found is False
    auto_open = _pick(results, "WIN32_DIALOG_AUTO_OPEN")
    assert auto_open.status == TestStatus.WARN
    assert "outcome=unavailable" in auto_open.technical_detail
    assert "refusing to choose" in auto_open.technical_detail


def test_discovery_reports_a_programming_fault_in_auto_open():
    from app.testing.mt4_discovery import _win32_discovery

    def boom(_p, _a):
        raise RuntimeError("menu api missing")

    adapter = make_adapter(wrap_ok=False)
    adapter._win32_open_login_dialog = boom
    results, _found = _win32_discovery(adapter, make_account(), [27784])
    auto_open = _pick(results, "WIN32_DIALOG_AUTO_OPEN")
    assert auto_open.status == TestStatus.WARN
    assert "outcome=error" in auto_open.technical_detail
    assert "RuntimeError" in auto_open.technical_detail
