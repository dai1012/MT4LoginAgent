"""Catalog-level multi-instance collision guard tests (service-level only)."""

from __future__ import annotations

import pytest

from app.accounts.instance_guard import (
    canonicalize_path,
    find_instance_collision,
    instance_fingerprint,
)
from app.models.domain import AccountConfig, AccountCreate, AccountPatch
from app.models.errors import DomainValidationError
from tests.support import account_values

TERMINAL_A = r"C:\MT4\InstA\terminal.exe"
PROFILE_A = r"C:\MT4\InstA\cwd"
TERMINAL_B = r"C:\MT4\InstB\terminal.exe"
PROFILE_B = r"C:\MT4\InstB\cwd"


def make_create(alias: str, terminal: str, profile: str | None, **overrides) -> AccountCreate:
    return AccountCreate.model_validate(
        account_values(alias=alias, terminal_path=terminal, profile_path=profile, **overrides)
    )


def test_duplicate_instance_rejected_on_create(runtime, account_payload):
    runtime.accounts.create(make_create("A", TERMINAL_A, PROFILE_A))
    with pytest.raises(DomainValidationError, match="[Cc]ollision"):
        runtime.accounts.create(
            make_create("B", TERMINAL_A, PROFILE_A, login_id="OTHER-LOGIN")
        )


def test_duplicate_instance_rejected_on_update(runtime):
    a = runtime.accounts.create(make_create("A", TERMINAL_A, PROFILE_A))
    b = runtime.accounts.create(make_create("B", TERMINAL_B, PROFILE_B))
    assert a.id != b.id
    with pytest.raises(DomainValidationError, match="[Cc]ollision"):
        runtime.accounts.update(
            b.id,
            AccountPatch.model_validate(
                {"terminal_path": TERMINAL_A, "profile_path": PROFILE_A}
            ),
        )
    # Failed update changed nothing.
    assert runtime.accounts.get(b.id).terminal_path == TERMINAL_B


def test_disabled_duplicate_created_then_enable_fails_until_paths_fixed(runtime):
    runtime.accounts.create(make_create("A", TERMINAL_A, PROFILE_A))
    draft = runtime.accounts.create(
        make_create("B", TERMINAL_A, PROFILE_A, enabled=False, login_id="OTHER-LOGIN")
    )
    assert draft.enabled is False
    with pytest.raises(DomainValidationError, match="[Cc]ollision"):
        runtime.accounts.set_enabled(draft.id, True)
    runtime.accounts.update(
        draft.id,
        AccountPatch.model_validate({"terminal_path": TERMINAL_B, "profile_path": PROFILE_B}),
    )
    assert runtime.accounts.set_enabled(draft.id, True).enabled is True


@pytest.mark.parametrize(
    "terminal_variant,profile_variant",
    [
        # Case-different spelling of both paths.
        (r"c:\mt4\insta\TERMINAL.EXE", r"c:\mt4\insta\CWD"),
        # Forward-slash spelling.
        ("C:/MT4/InstA/terminal.exe", "C:/MT4/InstA/cwd"),
        # Mixed separators plus trailing separator on the cwd.
        (r"C:/MT4\InstA/terminal.exe", r"C:\MT4\InstA\cwd\\"),
        # Doubled separators.
        (r"C:\\MT4\\InstA\\terminal.exe", r"C:\\MT4\\InstA\\cwd"),
        # Dot-segment spelling of the same directory.
        (r"C:\MT4\InstA\.\terminal.exe", r"C:\MT4\InstA\.\cwd"),
    ],
)
def test_equivalent_spellings_still_collide(runtime, terminal_variant, profile_variant):
    runtime.accounts.create(make_create("A", TERMINAL_A, PROFILE_A))
    with pytest.raises(DomainValidationError, match="[Cc]ollision"):
        runtime.accounts.create(make_create("B", terminal_variant, profile_variant))


def test_different_profile_and_terminal_both_succeed(runtime):
    a = runtime.accounts.create(make_create("A", TERMINAL_A, PROFILE_A))
    b = runtime.accounts.create(make_create("B", TERMINAL_B, PROFILE_B))
    assert a.enabled and b.enabled
    assert runtime.accounts.get(a.id).alias == "A"
    assert runtime.accounts.get(b.id).alias == "B"


def test_process_name_alone_does_not_distinguish(runtime):
    runtime.accounts.create(make_create("A", TERMINAL_A, PROFILE_A, process_name="terminal.exe"))
    with pytest.raises(DomainValidationError, match="[Cc]ollision"):
        runtime.accounts.create(
            make_create("B", TERMINAL_A, PROFILE_A, process_name="other-terminal.exe")
        )


def test_conflict_message_names_alias_and_hides_secrets(runtime):
    runtime.accounts.create(
        make_create(
            "A",
            TERMINAL_A,
            PROFILE_A,
            login_id="SECRET-LOGIN-AAA",
            server="SecretServer-AAA",
        )
    )
    with pytest.raises(DomainValidationError) as excinfo:
        runtime.accounts.create(
            make_create(
                "B",
                TERMINAL_A,
                PROFILE_A,
                login_id="SECRET-LOGIN-BBB",
                server="SecretServer-BBB",
            )
        )
    message = str(excinfo.value)
    assert "'A'" in message  # names the conflicting account's alias
    for secret_like in (
        "SECRET-LOGIN-AAA",
        "SECRET-LOGIN-BBB",
        "SecretServer-AAA",
        "SecretServer-BBB",
        TERMINAL_A,
        PROFILE_A,
    ):
        assert secret_like not in message
    for token_word in ("password", "token", "credential"):
        assert token_word not in message.casefold()
    assert "terminal installation folder" in message.casefold()
    assert "cwd" in message.casefold()


def test_self_update_of_unrelated_field_does_not_conflict(runtime):
    account = runtime.accounts.create(make_create("A", TERMINAL_A, PROFILE_A))
    updated = runtime.accounts.update(
        account.id, AccountPatch.model_validate({"display_name": "Renamed"})
    )
    assert updated.display_name == "Renamed"


def test_single_account_enable_cycle_unaffected(runtime):
    account = runtime.accounts.create(make_create("A", TERMINAL_A, PROFILE_A))
    assert runtime.accounts.set_enabled(account.id, False).enabled is False
    assert runtime.accounts.set_enabled(account.id, True).enabled is True


def test_disabling_never_raises(runtime):
    a = runtime.accounts.create(make_create("A", TERMINAL_A, PROFILE_A))
    b = runtime.accounts.create(make_create("B", TERMINAL_B, PROFILE_B))
    assert runtime.accounts.set_enabled(b.id, False).enabled is False
    assert runtime.accounts.set_enabled(a.id, False).enabled is False


def test_blank_terminal_path_is_skipped_by_guard():
    assert canonicalize_path(None) == ""
    assert canonicalize_path("") == ""
    assert canonicalize_path("   ") == ""
    blank = AccountConfig.model_construct(
        id="blank",
        alias="BLANK",
        terminal_path="   ",
        profile_path=PROFILE_A,
        enabled=True,
    )
    assert instance_fingerprint(blank) is None
    other = AccountConfig.model_construct(
        id="other",
        alias="OTHER",
        terminal_path=TERMINAL_A,
        profile_path=PROFILE_A,
        enabled=True,
    )
    assert find_instance_collision(blank, [other]) is None
    assert find_instance_collision(other, [blank]) is None


def test_canonicalization_is_case_and_separator_insensitive():
    assert canonicalize_path(r"C:\MT4\InstA\terminal.exe") == canonicalize_path(
        "c:/mt4/insta/TERMINAL.EXE"
    )
    assert canonicalize_path(r"C:\MT4\InstA\cwd") == canonicalize_path(
        r"C:\MT4\InstA\cwd\\"
    )


def test_web_create_duplicate_returns_422_without_secret(client, account_payload):
    first = client.post(
        "/api/accounts",
        json={
            **account_payload,
            "alias": "A",
            "terminal_path": TERMINAL_A,
            "profile_path": PROFILE_A,
        },
    )
    assert first.status_code == 201
    second = client.post(
        "/api/accounts",
        json={
            **account_payload,
            "alias": "B",
            "login_id": "WEB-SECRET-LOGIN",
            "terminal_path": TERMINAL_A,
            "profile_path": PROFILE_A,
        },
    )
    assert second.status_code == 422
    detail = second.json()["detail"]
    assert "A" in detail
    assert "WEB-SECRET-LOGIN" not in detail
    assert TERMINAL_A not in detail
