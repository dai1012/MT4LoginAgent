"""Catalog-level multi-instance collision guard tests (service-level only).

The service path validates that a terminal installation and a working directory
actually exist on Windows, so these tests build real directories under ``tmp_path``
instead of spelling out paths that only look like Windows ones. The pure
canonicalisation tests below keep Windows spellings on purpose: ``canonicalize_path``
uses ``ntpath`` semantics deliberately, so it is platform independent and a Windows
path is the interesting input on every operating system.
"""

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

# Windows spellings stay in the canonicalisation tests: they are the input under test,
# not a stand-in for a directory that should exist.
WINDOWS_TERMINAL = r"C:\MT4\InstA\terminal.exe"
WINDOWS_PROFILE = r"C:\MT4\InstA\cwd"


def make_create(alias: str, terminal: str, profile: str | None, **overrides) -> AccountCreate:
    return AccountCreate.model_validate(
        account_values(alias=alias, terminal_path=terminal, profile_path=profile, **overrides)
    )


@pytest.fixture
def instances(tmp_path):
    """Two real, independent MT4 installations with real working directories.

    Windows validates that the terminal file and the cwd exist, so a fake path is
    rejected there. Real directories keep the collision assertions meaningful on every
    platform instead of only on the ones where validation is skipped.
    """
    made = {}
    for name in ("A", "B"):
        root = tmp_path / name
        profile = root / "cwd"
        profile.mkdir(parents=True)
        terminal = root / "terminal.exe"
        terminal.write_bytes(b"MZ")
        made[name] = (str(terminal), str(profile))
    return made


def equivalent_spellings(path: str, *, is_directory: bool) -> list[str]:
    """Spellings of one real path that must still canonicalise to the same value.

    Every variant addresses the same file or directory: only case, separator style and
    redundant segments differ, which is exactly what the guard has to fold together. A
    trailing separator is only equivalent for a directory, never for a file.
    """
    normalised = path.replace("\\", "/").rstrip("/")
    parent, _, name = normalised.rpartition("/")
    variants = [
        normalised.upper(),
        normalised.replace("/", "\\"),
        f"{parent}//{name}",
        f"{parent}/./{name}",
    ]
    if is_directory:
        variants.append(f"{normalised}/")
    return variants


# (a) A second Account pointing at the same installation and cwd is refused.


def test_duplicate_instance_rejected_on_create(runtime, instances):
    terminal_a, profile_a = instances["A"]
    terminal_b, profile_b = instances["B"]
    runtime.accounts.create(make_create("A", terminal_a, profile_a))
    with pytest.raises(DomainValidationError, match="[Cc]ollision"):
        runtime.accounts.create(
            make_create("B", terminal_a, profile_a, login_id="OTHER-LOGIN")
        )
    # A genuinely separate installation is still accepted.
    assert runtime.accounts.create(
        make_create("C", terminal_b, profile_b)
    ).alias == "C"


def test_duplicate_instance_rejected_on_update(runtime, instances):
    terminal_a, profile_a = instances["A"]
    terminal_b, profile_b = instances["B"]
    a = runtime.accounts.create(make_create("A", terminal_a, profile_a))
    b = runtime.accounts.create(make_create("B", terminal_b, profile_b))
    assert a.id != b.id
    with pytest.raises(DomainValidationError, match="[Cc]ollision"):
        runtime.accounts.update(
            b.id,
            AccountPatch.model_validate(
                {"terminal_path": terminal_a, "profile_path": profile_a}
            ),
        )
    # Failed update changed nothing.
    assert runtime.accounts.get(b.id).terminal_path == terminal_b


def test_disabled_duplicate_created_then_enable_fails_until_paths_fixed(runtime, instances):
    terminal_a, profile_a = instances["A"]
    terminal_b, profile_b = instances["B"]
    runtime.accounts.create(make_create("A", terminal_a, profile_a))
    draft = runtime.accounts.create(
        make_create("B", terminal_a, profile_a, enabled=False, login_id="OTHER-LOGIN")
    )
    assert draft.enabled is False
    with pytest.raises(DomainValidationError, match="[Cc]ollision"):
        runtime.accounts.set_enabled(draft.id, True)
    runtime.accounts.update(
        draft.id,
        AccountPatch.model_validate({"terminal_path": terminal_b, "profile_path": profile_b}),
    )
    assert runtime.accounts.set_enabled(draft.id, True).enabled is True


def test_equivalent_spellings_still_collide(runtime, instances):
    terminal_a, profile_a = instances["A"]
    runtime.accounts.create(make_create("A", terminal_a, profile_a))
    terminal_spellings = equivalent_spellings(terminal_a, is_directory=False)
    profile_spellings = equivalent_spellings(profile_a, is_directory=True)
    # The guard must fold these together without a file ever being touched.
    assert len({canonicalize_path(item) for item in terminal_spellings}) == 1
    assert len({canonicalize_path(item) for item in profile_spellings}) == 1
    for terminal_variant in terminal_spellings:
        for profile_variant in profile_spellings:
            with pytest.raises(DomainValidationError, match="[Cc]ollision"):
                runtime.accounts.create(
                    make_create(
                        "B", terminal_variant, profile_variant, login_id="OTHER-LOGIN"
                    )
                )


def test_different_profile_and_terminal_both_succeed(runtime, instances):
    terminal_a, profile_a = instances["A"]
    terminal_b, profile_b = instances["B"]
    a = runtime.accounts.create(make_create("A", terminal_a, profile_a))
    b = runtime.accounts.create(make_create("B", terminal_b, profile_b))
    assert a.enabled and b.enabled
    assert runtime.accounts.get(a.id).alias == "A"
    assert runtime.accounts.get(b.id).alias == "B"


def test_process_name_alone_does_not_distinguish(runtime, instances):
    terminal_a, profile_a = instances["A"]
    runtime.accounts.create(
        make_create("A", terminal_a, profile_a, process_name="terminal.exe")
    )
    with pytest.raises(DomainValidationError, match="[Cc]ollision"):
        runtime.accounts.create(
            make_create("B", terminal_a, profile_a, process_name="other-terminal.exe")
        )


def test_conflict_message_names_alias_and_hides_secrets(runtime, instances):
    terminal_a, profile_a = instances["A"]
    runtime.accounts.create(
        make_create(
            "A",
            terminal_a,
            profile_a,
            login_id="SECRET-LOGIN-AAA",
            server="SecretServer-AAA",
        )
    )
    with pytest.raises(DomainValidationError) as excinfo:
        runtime.accounts.create(
            make_create(
                "B",
                terminal_a,
                profile_a,
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
        terminal_a,
        profile_a,
    ):
        assert secret_like not in message
    for token_word in ("password", "token", "credential"):
        assert token_word not in message.casefold()
    assert "terminal installation folder" in message.casefold()
    assert "cwd" in message.casefold()


def test_self_update_of_unrelated_field_does_not_conflict(runtime, instances):
    terminal_a, profile_a = instances["A"]
    account = runtime.accounts.create(make_create("A", terminal_a, profile_a))
    updated = runtime.accounts.update(
        account.id, AccountPatch.model_validate({"display_name": "Renamed"})
    )
    assert updated.display_name == "Renamed"


def test_single_account_enable_cycle_unaffected(runtime, instances):
    terminal_a, profile_a = instances["A"]
    account = runtime.accounts.create(make_create("A", terminal_a, profile_a))
    assert runtime.accounts.set_enabled(account.id, False).enabled is False
    assert runtime.accounts.set_enabled(account.id, True).enabled is True


def test_disabling_never_raises(runtime, instances):
    terminal_a, profile_a = instances["A"]
    terminal_b, profile_b = instances["B"]
    a = runtime.accounts.create(make_create("A", terminal_a, profile_a))
    b = runtime.accounts.create(make_create("B", terminal_b, profile_b))
    assert runtime.accounts.set_enabled(b.id, False).enabled is False
    assert runtime.accounts.set_enabled(a.id, False).enabled is False


# (b) The guard's own helpers take no filesystem path, so a Windows spelling is the
# interesting input on every operating system.


def test_blank_terminal_path_is_skipped_by_guard():
    assert canonicalize_path(None) == ""
    assert canonicalize_path("") == ""
    assert canonicalize_path("   ") == ""
    blank = AccountConfig.model_construct(
        id="blank",
        alias="BLANK",
        terminal_path="   ",
        profile_path=WINDOWS_PROFILE,
        enabled=True,
    )
    assert instance_fingerprint(blank) is None
    other = AccountConfig.model_construct(
        id="other",
        alias="OTHER",
        terminal_path=WINDOWS_TERMINAL,
        profile_path=WINDOWS_PROFILE,
        enabled=True,
    )
    assert find_instance_collision(blank, [other]) is None
    assert find_instance_collision(other, [blank]) is None


def test_canonicalization_is_case_and_separator_insensitive():
    assert canonicalize_path(WINDOWS_TERMINAL) == canonicalize_path(
        "c:/mt4/insta/TERMINAL.EXE"
    )
    assert canonicalize_path(WINDOWS_PROFILE) == canonicalize_path(
        r"C:\MT4\InstA\cwd\\"
    )
    assert canonicalize_path(r"C:\MT4\InstA\.\terminal.exe") == canonicalize_path(
        WINDOWS_TERMINAL
    )


def test_web_create_duplicate_returns_422_without_secret(client, account_payload, instances):
    terminal_a, profile_a = instances["A"]
    first = client.post(
        "/api/accounts",
        json={
            **account_payload,
            "alias": "A",
            "terminal_path": terminal_a,
            "profile_path": profile_a,
        },
    )
    assert first.status_code == 201
    second = client.post(
        "/api/accounts",
        json={
            **account_payload,
            "alias": "B",
            "login_id": "WEB-SECRET-LOGIN",
            "terminal_path": terminal_a,
            "profile_path": profile_a,
        },
    )
    assert second.status_code == 422
    detail = second.json()["detail"]
    assert "A" in detail
    assert "WEB-SECRET-LOGIN" not in detail
    assert terminal_a not in detail