"""Rakuten credential format contract.

A credential observed on a real account mixed ASCII upper/lower case letters and
digits and was 15 characters long. The previous ``^[0-9]{4,10}$`` rule rejected
that shape outright, so the rule was widened rather than pinned to one sample.

Every value in this module is a synthetic placeholder. No real credential from any
account is used or recorded here.
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from app.adapters.slack.commands import CommandError, is_otp, parse_slash_command
from app.models.domain import OTP_PATTERN, LoginRequest
from app.security.redaction import redact_text

# Clearly synthetic 15 character mixed-case alphanumeric placeholder.
MIXED_15 = "TESTONLY1aB2cD3"
# Clearly synthetic purely numeric placeholder, of the kind a fixed Demo
# credential uses.
NUMERIC = "246813"
LONGEST_ALLOWED = "A1b2" * 8  # 32 characters, the ceiling.


def _request(value: str) -> LoginRequest:
    return LoginRequest(
        sender_id="U1", target="A", otp=SecretStr(value), source="slack"
    )


def test_numeric_demo_credential_is_accepted():
    assert _request(NUMERIC)
    assert is_otp(NUMERIC)
    assert parse_slash_command(f"/mt4 A {NUMERIC}").otp == NUMERIC


def test_fifteen_character_mixed_alphanumeric_is_accepted():
    assert len(MIXED_15) == 15
    assert _request(MIXED_15)
    assert is_otp(MIXED_15)
    assert parse_slash_command(f"/mt4 A {MIXED_15}").otp == MIXED_15


def test_upper_and_lower_case_and_digits_all_accepted():
    for value in ("ABCDEFGH", "abcdefgh", "abcd1234", "ABCD1234"):
        assert is_otp(value), value


def test_four_character_floor_still_accepted_so_nothing_valid_regresses():
    assert is_otp("1234")
    assert is_otp("aB3d")


def test_thirty_two_character_ceiling_is_inclusive():
    assert len(LONGEST_ALLOWED) == 32
    assert is_otp(LONGEST_ALLOWED)
    assert not is_otp(LONGEST_ALLOWED + "A")


@pytest.mark.parametrize(
    "value",
    [
        "ABC DEFGH",  # internal whitespace
        "ABC\tDEFG",  # tab
        "ABC DEFG",  # non-breaking space
        "ABCDEFGＨ",  # full-width look-alike
        "ABCDEFGН",  # Cyrillic look-alike
        "ABCДEFGH",  # mixed-script homoglyph
        "ABCDEFG\u202e",  # bidi override
        "123",  # below the floor
        "AB",  # below the floor
        "",  # empty
        "A1b2" * 8 + "A",  # 33, over the ceiling
        "ABCDEFGH!",  # punctuation
        "ABCDEFGH-",  # separator used by aliases, not a credential
        "ABCDEFGH_",
    ],
)
def test_rejected_shapes(value):
    assert not is_otp(value)
    assert not OTP_PATTERN.fullmatch(value)
    with pytest.raises(ValueError):
        _request(value)
    with pytest.raises(CommandError):
        parse_slash_command(f"/mt4 A {value}")


def test_surrounding_whitespace_is_stripped_but_internal_whitespace_is_not():
    """The Slack parser normalises surrounding whitespace; the model still rejects it.

    A trailing space after an OTP is an ordinary typing slip, so the command layer
    trims it. Whitespace inside the value is never accepted, and the model layer
    refuses every value containing whitespace.
    """
    assert parse_slash_command(f"/mt4 A {NUMERIC}  ").otp == NUMERIC
    assert parse_slash_command(f"  /mt4 A {NUMERIC}").otp == NUMERIC
    with pytest.raises(CommandError):
        parse_slash_command(f"/mt4 A AB{NUMERIC[:3]} DEFGH")
    for value in (" ABCDEFGH", "ABCDEFGH ", "ABC\tDEFG"):
        with pytest.raises(ValueError):
            _request(value)


def test_slack_command_shapes_still_behave_as_before():
    assert parse_slash_command("/mt4 status").kind.value == "status"
    with pytest.raises(CommandError):
        parse_slash_command("")
    with pytest.raises(CommandError):
        parse_slash_command("/mt4foo 123456")
    with pytest.raises(CommandError):
        parse_slash_command("A 123456 extra")
    with pytest.raises(CommandError):
        parse_slash_command("A " + "9" * 33)


def test_error_message_no_longer_promises_digits_only():
    with pytest.raises(ValueError) as caught:
        _request("ABCDEFGH!")
    message = str(caught.value)
    assert "4 to 10" not in message
    assert "ASCII alphanumeric" in message


def test_redaction_covers_a_mixed_alphanumeric_credential():
    """The redaction patterns must not keep a narrower rule than the parser."""
    text = f"user sent /mt4 A {MIXED_15} in Slack"
    redacted = redact_text(text)
    assert MIXED_15 not in redacted
    assert "<redacted>" in redacted


def test_redaction_still_covers_a_numeric_credential():
    text = f"user sent /mt4 A {NUMERIC} in Slack"
    assert NUMERIC not in redact_text(text)


def test_redaction_does_not_swallow_the_status_command():
    assert redact_text("/mt4 status") == "/mt4 status"


def test_pattern_is_anchored_so_surrounding_characters_are_rejected():
    surrounded = (
        f"!{MIXED_15}",
        f"{MIXED_15}!",
        f"!{MIXED_15}!",
        f"{MIXED_15}\n",
        f"\n{MIXED_15}",
    )
    for value in surrounded:
        assert not is_otp(value), value
    # A longer all-alphanumeric value is still inside the rule; length alone does
    # not make a value invalid until it crosses the ceiling.
    assert is_otp(f"x{MIXED_15}")
