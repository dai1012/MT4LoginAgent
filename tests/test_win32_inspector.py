"""Win32 dialog inspection for a broker or MT4 build that has never been adapted.

The inspector proposes a configuration from control metadata so a human can apply it.
It must never read an editable control's caption, must never guess when a shape is
ambiguous, and must not write anything itself.
"""

from __future__ import annotations

import pytest

from app.mt4.windows_automation import WindowsAutomation
from tests.support import account_values, instance_paths


class FakeInfo:
    def __init__(self, class_name: str, control_id):
        self.class_name = class_name
        self.control_id = control_id


class FakeControl:
    def __init__(self, class_name: str, control_id=None, text: str = "", children=()):
        self.element_info = FakeInfo(class_name, control_id)
        self._text = text
        self._children = list(children)

    def window_text(self) -> str:
        return self._text

    def descendants(self):
        return list(self._children)


def rakuten_dialog(title: str = "Rakuten MetaTrader 4"):
    """The shape measured on Rakuten MT4: two ComboBoxes each owning an Edit, a
    free-standing Edit for the credential, and a login Button."""
    login_edit = FakeControl("Edit", 1001, "")
    login_combo = FakeControl("ComboBox", 1181, "", [login_edit])
    server_edit = FakeControl("Edit", 1001, "")
    server_combo = FakeControl("ComboBox", 1293, "", [server_edit])
    otp = FakeControl("Edit", 1220, "")
    return FakeControl(
        "#32770",
        1,
        title,
        [
            FakeControl("Static", 1000, "ログインID :"),
            login_combo,
            FakeControl("Static", 1002, "パスワード :"),
            otp,
            FakeControl("Static", 1003, "サーバー :"),
            server_combo,
            FakeControl("Button", 1, "ログイン"),
            FakeControl("Button", 2, "キャンセル"),
        ],
    )


REQUIRED = (
    "dialog_class",
    "anchors",
    "login_id_combo",
    "login_id_edit",
    "otp",
    "server_combo",
    "server_edit",
    "login_button",
)


def build_account(**overrides):
    from app.models.domain import AccountConfig

    return AccountConfig.model_validate(
        {**account_values(), **instance_paths("A"), **overrides}
    )


def make_inspector(dialog, *, pids=(27784,), top_windows=None, open_result=("unavailable", "")):
    automation = object.__new__(WindowsAutomation)
    sent: list[tuple] = []

    def fake_enum():
        return list(
            top_windows
            if top_windows is not None
            else [(332886, 27784, "#32770", "Rakuten MetaTrader 4")]
        )

    def fake_wrap(handle):
        return dialog if dialog is not None and handle == 332886 else None

    def fake_open(_pids, _account, **_kwargs):
        sent.append(("open",))
        return open_result

    def fake_main_window(_pids, _account):
        return 1185817, "one MT4 main window matched"

    def fake_menu_commands(_hwnd):
        return [("File", 1), ("取引口座にログイン", 1023)]

    automation._enum_top_windows = fake_enum
    automation._win32_wrap = fake_wrap
    automation._win32_open_login_dialog = fake_open
    automation._win32_main_window = fake_main_window
    automation._win32_menu_commands = fake_menu_commands
    return automation, sent


# --- the happy path ---------------------------------------------------------------


def test_inspect_proposes_every_field_with_high_confidence():
    dialog = rakuten_dialog()
    automation, _sent = make_inspector(
        dialog,
        top_windows=[
            (332886, 27784, "#32770", "Rakuten MetaTrader 4"),
            (1185817, 27784, "MetaQuotes::MetaTrader::4.00", "RakutenSecurities-Demo"),
        ],
    )
    report = automation._win32_inspect([27784], build_account())

    assert report["outcome"] == "inspected"
    assert report["suggested"]["login_id_combo"] == 1181
    assert report["suggested"]["login_id_edit"] == 1001
    assert report["suggested"]["otp"] == 1220
    assert report["suggested"]["server_combo"] == 1293
    assert report["suggested"]["server_edit"] == 1001
    assert report["suggested"]["login_button"] == 1
    assert report["suggested"]["dialog_class"] == "#32770"
    assert report["suggested"]["anchors"] == ["ログインID :", "パスワード :"]
    for key in REQUIRED:
        assert report["confidence"][key] == "HIGH", key


def test_inspect_uses_the_already_open_dialog_without_sending_a_command():
    dialog = rakuten_dialog()
    automation, sent = make_inspector(dialog)
    report = automation._win32_inspect([27784], build_account())
    assert report["outcome"] == "inspected"
    assert report["detail"] == "the login dialog was already open"
    assert sent == []


# --- fail closed ------------------------------------------------------------------


def test_inspect_refuses_when_more_than_one_process_resolves():
    dialog = rakuten_dialog()
    automation, _sent = make_inspector(dialog)
    report = automation._win32_inspect([27784, 27785], build_account())
    assert report["outcome"] == "unavailable"
    assert "exactly one MT4 process" in report["detail"]
    assert report["suggested"] == {}


def test_inspect_refuses_when_no_process_resolves():
    dialog = rakuten_dialog()
    automation, _sent = make_inspector(dialog)
    report = automation._win32_inspect([], build_account())
    assert report["outcome"] == "unavailable"
    assert "exactly one MT4 process" in report["detail"]


def test_inspect_refuses_when_two_login_shaped_windows_match():
    one = rakuten_dialog()
    two = rakuten_dialog(title="Another Login")
    automation = object.__new__(WindowsAutomation)
    automation._enum_top_windows = lambda: [
        (332886, 27784, "#32770", "Rakuten MetaTrader 4"),
        (332887, 27784, "#32770", "Another Login"),
    ]
    automation._win32_wrap = lambda handle: one if handle == 332886 else two
    automation._win32_open_login_dialog = lambda *a, **k: ("unavailable", "")
    report = automation._win32_inspect([27784], build_account())
    assert report["outcome"] == "unavailable"
    assert "refusing to choose" in report["detail"]


def test_inspect_never_guesses_an_ambiguous_credential_box():
    dialog = FakeControl(
        "#32770",
        1,
        "Rakuten MetaTrader 4",
        [
            FakeControl("ComboBox", 1181, "", [FakeControl("Edit", 1001, "")]),
            FakeControl("ComboBox", 1293, "", [FakeControl("Edit", 1001, "")]),
            FakeControl("Edit", 1220, ""),
            FakeControl("Edit", 1221, ""),
            FakeControl("Button", 1, "ログイン"),
        ],
    )
    automation, _sent = make_inspector(dialog)
    report = automation._win32_inspect([27784], build_account())
    assert report["outcome"] == "inspected"
    assert report["suggested"]["otp"] is None
    assert report["confidence"]["otp"] == "NEEDS_CONFIRMATION"


def test_inspect_flags_a_single_combo_box_as_ambiguous():
    dialog = FakeControl(
        "#32770",
        1,
        "Login",
        [
            FakeControl("ComboBox", 1181, "", [FakeControl("Edit", 1001, "")]),
            FakeControl("Edit", 1220, ""),
            FakeControl("Button", 1, "ログイン"),
        ],
    )
    automation, _sent = make_inspector(dialog)
    report = automation._win32_inspect([27784], build_account())
    assert report["confidence"]["server_combo"] == "NEEDS_CONFIRMATION"
    assert report["confidence"]["login_id_combo"] == "NEEDS_CONFIRMATION"


# --- the auto-open path -----------------------------------------------------------


def test_inspect_opens_the_dialog_through_the_menu_when_it_is_absent():
    dialog = rakuten_dialog()
    present = {"value": False}
    automation = object.__new__(WindowsAutomation)
    sent: list[tuple] = []

    def fake_enum():
        return [
            (1185817, 27784, "MetaQuotes::MetaTrader::4.00", "RakutenSecurities-Demo"),
            (332886, 27784, "#32770", "Rakuten MetaTrader 4"),
        ]

    def fake_wrap(handle):
        if handle == 332886 and present["value"]:
            return dialog
        return None

    def fake_open(_pids, _account, **_kwargs):
        # The dialog only exists once the menu command has been sent.
        sent.append(("open",))
        present["value"] = True
        return ("opened", "the login dialog appeared after the menu command")

    automation._enum_top_windows = fake_enum
    automation._win32_wrap = fake_wrap
    automation._win32_open_login_dialog = fake_open
    report = automation._win32_inspect([27784], build_account())
    assert sent == [("open",)]
    assert report["outcome"] == "inspected"
    assert "opened through the window menu" in report["detail"]


# --- metadata only, never a caption ------------------------------------------------


def test_inspect_never_returns_an_editable_controls_caption():
    secret_edit = FakeControl("Edit", 1220, "S3CRETVALUE")
    dialog = FakeControl(
        "#32770",
        1,
        "Rakuten MetaTrader 4",
        [
            FakeControl("ComboBox", 1181, "", [FakeControl("Edit", 1001, "LOGINIDVALUE")]),
            FakeControl("ComboBox", 1293, "", [FakeControl("Edit", 1001, "")]),
            secret_edit,
            FakeControl("Button", 1, "ログイン"),
        ],
    )
    automation, _sent = make_inspector(dialog)
    report = automation._win32_inspect([27784], build_account())
    rendered = repr(report)
    assert "S3CRETVALUE" not in rendered
    assert "LOGINIDVALUE" not in rendered


# --- title suggestions ------------------------------------------------------------


def test_login_title_suggestion_is_anchored_and_escaped():
    dialog = rakuten_dialog(title="Login (Demo) [1]")
    automation, _sent = make_inspector(dialog)
    report = automation._win32_inspect([27784], build_account())
    suggested = report["titles"]["window_title_regex"]
    assert suggested is not None
    assert suggested.startswith("^") and suggested.endswith("$")
    # The parentheses and bracket must be escaped, not treated as a group.
    assert "\\(" in suggested and "\\[" in suggested


def test_success_title_suggestion_generalises_the_login_id():
    dialog = rakuten_dialog()
    automation, _sent = make_inspector(
        dialog,
        top_windows=[
            (332886, 27784, "#32770", "Rakuten MetaTrader 4"),
            (
                1185817,
                27784,
                "MetaQuotes::MetaTrader::4.00",
                "RakutenSecurities-Demo - 1234567 - Rakuten Securities, Inc.",
            ),
        ],
    )
    report = automation._win32_inspect(
        [27784], build_account(login_id="1234567")
    )
    suggested = report["titles"]["success_window_title_regex"]
    assert suggested is not None
    # The account specific literal must not be baked into the suggestion.
    assert "1234567" not in suggested
    assert ".*" in suggested
    # re.escape may insert backslashes, so compare against the unescaped form.
    assert "RakutenSecurities-Demo" in suggested.replace("\\", "")


def test_success_title_suggestion_keeps_the_title_when_no_login_id_is_present():
    dialog = rakuten_dialog()
    automation, _sent = make_inspector(
        dialog,
        top_windows=[
            (332886, 27784, "#32770", "Rakuten MetaTrader 4"),
            (1185817, 27784, "MetaQuotes::MetaTrader::4.00", "RakutenSecurities-Demo"),
        ],
    )
    report = automation._win32_inspect([27784], build_account(login_id="absent-id"))
    suggested = report["titles"]["success_window_title_regex"]
    assert "RakutenSecurities-Demo" in suggested.replace("\\", "")


# --- menu captions are reported, never adopted -------------------------------------


def test_menu_captions_are_reported_for_a_different_broker():
    dialog = rakuten_dialog()
    automation = object.__new__(WindowsAutomation)
    automation._enum_top_windows = lambda: [
        (332886, 27784, "#32770", "Some Broker Terminal 5")
    ]
    automation._win32_wrap = lambda handle: dialog
    automation._main_and_menu = None
    automation._win32_open_login_dialog = lambda *a, **k: ("unavailable", "")
    automation._win32_main_window = lambda *a, **k: (1, "ok")
    automation._win32_menu_commands = lambda hwnd: [
        ("ファイル", 100), ("Connect to Live Account", 7001)
    ]
    report = automation._win32_inspect([27784], build_account())
    assert "Connect to Live Account" in report["menu_captions"]
    # Reporting must not change the constant the auto-open matches against.
    from app.mt4.windows_automation import _MENU_LOGIN_CAPTIONS

    assert frozenset(
        {"取引口座にログイン", "login to trade account"}
    ) == _MENU_LOGIN_CAPTIONS


# --- runner level: apply refuses and stays narrow ---------------------------------


@pytest.mark.asyncio
async def test_apply_refuses_when_a_field_is_not_high(runtime):
    from app.models.domain import AccountCreate

    account = runtime.accounts.create(
        AccountCreate.model_validate(
            {**account_values(), **instance_paths("A"), "alias": "A", "display_name": "A"}
        )
    )
    runner = runtime.test_runner
    complete = {
        "dialog_class": "#32770",
        "anchors": ["ログインID :"],
        "login_id_combo": 1181,
        "login_id_edit": 1001,
        "otp": 1220,
        "server_combo": 1293,
        "server_edit": 1001,
        "login_button": 1,
    }
    confidence = {key: "HIGH" for key in REQUIRED}
    confidence["otp"] = "NEEDS_CONFIRMATION"
    outcome = await runner.apply_win32_settings(
        account.id, {"suggested": complete, "confidence": confidence}
    )
    assert outcome["applied"] is False
    assert "otp" in outcome["reason"]


@pytest.mark.asyncio
async def test_apply_writes_only_the_win32_fallback(runtime):
    from app.models.domain import AccountCreate

    created = runtime.accounts.create(
        AccountCreate.model_validate(
            {
                **account_values(),
                **instance_paths("A"),
                "alias": "A",
                "display_name": "A",
                "login_id": "login-a",
                "server": "server-a",
            }
        )
    )
    before = runtime.accounts.get(created.id)
    suggested = {
        "dialog_class": "#32770",
        "anchors": ["ログインID :"],
        "login_id_combo": 1181,
        "login_id_edit": 1001,
        "otp": 1220,
        "server_combo": 1293,
        "server_edit": 1001,
        "login_button": 1,
    }
    outcome = await runtime.test_runner.apply_win32_settings(
        created.id,
        {"suggested": suggested, "confidence": {key: "HIGH" for key in REQUIRED}},
    )
    assert outcome["applied"] is True

    after = runtime.accounts.get(created.id)
    assert after.win32_fallback.enabled is True
    assert after.win32_fallback.dialog_class == "#32770"
    assert after.win32_fallback.control_ids["otp"] == 1220
    # Identity and instance fields must be untouched.
    assert after.alias == before.alias
    assert after.login_id == before.login_id
    assert after.server == before.server
    assert after.terminal_path == before.terminal_path
    assert after.profile_path == before.profile_path
    assert after.window_title_regex == before.window_title_regex


@pytest.mark.asyncio
async def test_apply_rejects_an_out_of_range_control_id(runtime):
    from app.models.domain import AccountCreate

    account = runtime.accounts.create(
        AccountCreate.model_validate(
            {**account_values(), **instance_paths("A"), "alias": "A", "display_name": "A"}
        )
    )
    suggested = {
        "dialog_class": "#32770",
        "anchors": ["x"],
        "login_id_combo": 1181,
        "login_id_edit": 1001,
        "otp": 99999,
        "server_combo": 1293,
        "server_edit": 1001,
        "login_button": 1,
    }
    outcome = await runtime.test_runner.apply_win32_settings(
        account.id,
        {"suggested": suggested, "confidence": {key: "HIGH" for key in REQUIRED}},
    )
    assert outcome["applied"] is False
    assert "otp" in outcome["reason"]


@pytest.mark.asyncio
async def test_inspect_is_unavailable_off_windows(runtime, monkeypatch):
    from app.models.domain import AccountCreate

    account = runtime.accounts.create(
        AccountCreate.model_validate(
            {**account_values(), **instance_paths("A"), "alias": "A", "display_name": "A"}
        )
    )
    report = await runtime.test_runner.inspect_win32(account.id)
    assert report["outcome"] == "unavailable"
    assert "requires Windows" in report["detail"]
    assert report["appliable"] is False
