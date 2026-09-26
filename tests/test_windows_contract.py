from __future__ import annotations

from types import SimpleNamespace

import pytest

import app.mt4.windows_automation as windows_module
from app.models.domain import AccountConfig, AutomationResult, ErrorCategory, LoginStatus
from app.mt4.windows_automation import (
    WindowsAutomation,
    WindowsAutomationError,
    _windows_login_worker,
)
from app.security.redaction import redact_text


class FakeControl:
    def __init__(self, runtime_id: int, name: str, automation_id: str = ""):
        self.element_info = SimpleNamespace(
            runtime_id=runtime_id,
            process_id=1,
            name=name,
            automation_id=automation_id,
        )


class FakeWindow:
    pass


class FakeMainWindow(FakeWindow):
    def __init__(self, title="Rakuten authenticated"):
        self.title = title
        self.element_info = SimpleNamespace(process_id=1, runtime_id=9)

    def window_text(self):
        return self.title


def make_account(**changes):
    values = {
        "display_name": "A",
        "alias": "A",
        "login_id": "local-id",
        "server": "demo",
        "terminal_path": "/tmp/terminal.exe",
        "control_ids": {"login_id": "login", "otp": "otp"},
    }
    values.update(changes)
    return AccountConfig.model_validate(values)


def make_adapter(controls):
    adapter = object.__new__(WindowsAutomation)
    adapter._visible_controls = lambda window, control_type: controls
    return adapter


def test_windows_regex_rejects_nested_quantifiers():
    with pytest.raises(ValueError, match="safe"):
        make_account(window_title_regex="(a+)+$")


def test_windows_login_controls_fail_closed_without_explicit_distinct_selectors():
    adapter = make_adapter([FakeControl(1, "Edit1"), FakeControl(2, "Edit2")])
    with pytest.raises(WindowsAutomationError, match="not found"):
        adapter._find_login_controls(FakeWindow(), make_account(control_ids={}))


def test_windows_login_controls_reject_same_underlying_control():
    control = FakeControl(1, "Password", "same")
    adapter = make_adapter([control])
    with pytest.raises(WindowsAutomationError, match="same control"):
        adapter._find_login_controls(
            FakeWindow(), make_account(control_ids={"login_id": "same", "otp": "same"})
        )


def test_windows_login_controls_accept_distinct_explicit_selectors():
    controls = [FakeControl(1, "Login", "login"), FakeControl(2, "Password", "otp")]
    adapter = make_adapter(controls)
    assert adapter._find_login_controls(FakeWindow(), make_account()) == (controls[0], controls[1])


def test_windows_process_matching_applies_profile_before_returning_pids():
    class FakeProcess:
        def __init__(self, info):
            self.info = info

    class FakePsutil:
        Error = OSError

        def process_iter(self, _attrs):
            return [
                FakeProcess(
                    {
                        "pid": 11,
                        "exe": "/tmp/terminal.exe",
                        "name": "terminal.exe",
                        "cwd": "/tmp/profile-a",
                    }
                ),
                FakeProcess(
                    {
                        "pid": 22,
                        "exe": "/tmp/terminal.exe",
                        "name": "terminal.exe",
                        "cwd": "/tmp/profile-b",
                    }
                ),
            ]

    adapter = object.__new__(WindowsAutomation)
    adapter._psutil = FakePsutil()
    account = make_account(terminal_path="/tmp/terminal.exe", profile_path="/tmp/profile-a")
    assert adapter._process_ids(account) == [11]


def test_window_key_falls_back_to_native_handle_attribute():
    adapter = object.__new__(WindowsAutomation)
    window = SimpleNamespace(element_info=SimpleNamespace(process_id=4, runtime_id=None, handle=42))
    assert adapter._window_key(window) == "handle:42"


def test_windows_process_enumeration_failure_fails_closed():
    class BrokenPsutil:
        Error = OSError

        def process_iter(self, _attrs):
            raise OSError("access denied")

    adapter = object.__new__(WindowsAutomation)
    adapter._psutil = BrokenPsutil()
    with pytest.raises(WindowsAutomationError, match="refusing to launch"):
        adapter._process_ids(make_account())


def test_windows_individual_process_inspection_failure_fails_closed():
    class UnreadableProcess:
        @property
        def info(self):
            raise OSError("access denied")

    class Psutil:
        Error = OSError

        def process_iter(self, _attrs):
            return [UnreadableProcess()]

    adapter = object.__new__(WindowsAutomation)
    adapter._psutil = Psutil()
    with pytest.raises(WindowsAutomationError, match="refusing to launch"):
        adapter._process_ids(make_account())


def test_windows_profile_unreadable_fails_instead_of_launching_duplicate():
    class FakeProcess:
        def __init__(self, info):
            self.info = info

    class FakePsutil:
        Error = OSError

        def process_iter(self, _attrs):
            return [
                FakeProcess(
                    {
                        "pid": 11,
                        "exe": "/tmp/terminal.exe",
                        "name": "terminal.exe",
                        "cwd": None,
                    }
                )
            ]

    adapter = object.__new__(WindowsAutomation)
    adapter._psutil = FakePsutil()
    account = make_account(terminal_path="/tmp/terminal.exe", profile_path="/tmp/profile-a")
    assert adapter._process_ids(account) == []
    assert adapter._executable_is_running(account) is True


@pytest.mark.asyncio
async def test_isolated_worker_exit_is_not_reported_as_timeout(monkeypatch):
    class FakeReceiver:
        def poll(self, _timeout):
            return False

        def close(self):
            return None

    class FakeSender:
        def close(self):
            return None

    class FakeProcess:
        def __init__(self, **_kwargs):
            self.alive = False

        def start(self):
            self.alive = False

        def is_alive(self):
            return self.alive

        def terminate(self):
            self.alive = False

        def join(self, _timeout=None):
            return None

    class FakeContext:
        def Pipe(self, **_kwargs):
            return FakeReceiver(), FakeSender()

        def Process(self, **kwargs):
            return FakeProcess(**kwargs)

    monkeypatch.setattr(windows_module.multiprocessing, "get_context", lambda _name: FakeContext())
    adapter = object.__new__(WindowsAutomation)
    result = await adapter._login_isolated(make_account(), "123456")
    assert result.status.value == "failed"
    assert result.category.value == "ui_automation_error"


def test_masked_password_otp_setter_is_accepted_without_plaintext_readback():
    class MaskedField:
        def set_edit_text(self, value):
            self.value = value

        def get_value(self):
            return ""

    field = MaskedField()
    adapter = object.__new__(WindowsAutomation)
    adapter._set_text(field, "123456", verify_exact=False)
    assert field.value == "123456"


def test_windows_input_fallbacks_are_fail_closed():
    class NoValuePattern:
        def set_edit_text(self, _value):
            raise RuntimeError("no ValuePattern")

    class NoInvoke:
        def invoke(self):
            raise RuntimeError("no InvokePattern")

    adapter = object.__new__(WindowsAutomation)
    with pytest.raises(WindowsAutomationError, match="could not be filled"):
        adapter._set_text(NoValuePattern(), "value")
    with pytest.raises(WindowsAutomationError, match="InvokePattern"):
        adapter._invoke(NoInvoke())


def test_windows_success_requires_new_or_changed_authenticated_window(monkeypatch):
    ticks = iter([0.0, 0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8])
    monkeypatch.setattr(windows_module.time, "monotonic", lambda: next(ticks, 100.0))
    monkeypatch.setattr(windows_module.time, "sleep", lambda _seconds: None)
    adapter = object.__new__(WindowsAutomation)
    main = FakeMainWindow()
    adapter._windows = lambda _pids: [main]
    login = SimpleNamespace(exists=lambda: False)
    account = make_account(success_window_title_regex="Rakuten")
    before = adapter._success_window_state([1], account)
    status, category, _message, _verification = adapter._wait_for_result(
        [1], login, account, before, {}
    )
    assert status == LoginStatus.FAILED
    assert category == ErrorCategory.UI_VERIFICATION_UNVERIFIED

    ticks = iter([0.0, 0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8])
    monkeypatch.setattr(windows_module.time, "monotonic", lambda: next(ticks, 100.0))
    main.title = "Rakuten authenticated"
    before = {"1:9": "Rakuten old"}
    status, category, _message, verification = adapter._wait_for_result(
        [1], login, account, before, {}
    )
    assert status == LoginStatus.SUCCESS
    assert category == ErrorCategory.NONE
    ticks = iter([0.0, 0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8])
    monkeypatch.setattr(windows_module.time, "monotonic", lambda: next(ticks, 100.0))
    status, category, _message, verification = adapter._wait_for_result([1], login, account, {}, {})
    assert status == LoginStatus.SUCCESS
    assert category == ErrorCategory.NONE
    assert verification == "authenticated_window_transition"


def test_server_selection_uses_selection_pattern_not_click_input():
    class SelectionItem:
        def Select(self):
            return None

    class Combo:
        def __init__(self):
            self.iface_selection_item = SelectionItem()
            self.value = ""

        def get_value(self):
            return self.value

    combo = Combo()
    adapter = object.__new__(WindowsAutomation)
    adapter._find_control = lambda *_args: combo
    account = make_account(server="RakutenServer")
    combo.iface_selection_item.Select = lambda: setattr(combo, "value", "RakutenServer")
    assert adapter._select_server(object(), account) is True
    assert combo.value == "RakutenServer"


def test_save_login_checkbox_uses_toggle_pattern_and_readback(monkeypatch):
    class TogglePattern:
        def __init__(self):
            self.CurrentToggleState = 0

        def Toggle(self):
            self.CurrentToggleState = 1

    class CheckBox:
        def __init__(self):
            self.iface_toggle = TogglePattern()
            self.element_info = SimpleNamespace(automation_id="save", name="Save")

        def get_toggle_state(self):
            return self.iface_toggle.CurrentToggleState

    checkbox = CheckBox()
    adapter = object.__new__(WindowsAutomation)
    adapter._find_control = lambda *_args: checkbox
    account = make_account(save_login_info=True)
    adapter._check_save_login(object(), account)
    assert checkbox.get_toggle_state() == 1

    class UnknownState:
        element_info = SimpleNamespace(automation_id="save", name="Save")

    adapter._find_control = lambda *_args: UnknownState()
    adapter._check_save_login(object(), make_account(save_login_info=False))


def test_worker_redacts_otp_inside_fresh_spawn_context(monkeypatch):
    class FakeAutomation:
        def __init__(self):
            pass

        def _desktop(self):
            return object()

        def _login_sync(self, _account, otp):
            assert "1234" not in redact_text("provider debug " + otp)
            return AutomationResult(status=LoginStatus.SUCCESS, message="ok")

    sent = []

    class Sender:
        def send(self, payload):
            sent.append(payload)

        def close(self):
            pass

    monkeypatch.setattr(windows_module, "WindowsAutomation", FakeAutomation)
    _windows_login_worker(Sender(), make_account(), "1234")
    assert sent[0]["status"] == "success"
