"""Opaque login credential contract.

A broker issues a one-time code while a Demo account is driven with a fixed
password. Neither has a documented format, so the value is treated as an opaque
secret: only its size is bounded and characters that would forge a log line or
truncate a string downstream are refused.

Every value in this module is a synthetic placeholder. No real credential from any
account is used or recorded here.
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from app.adapters.slack.commands import (
    CommandError,
    is_credential,
    parse_slash_command,
)
from app.models.api import GroupTestRequest, RealLoginRequest
from app.models.domain import CREDENTIAL_MAX_LENGTH, LoginRequest, check_credential
from app.security.redaction import redact_text
from app.testing.security import sanitize_text

# Synthetic placeholders covering the shapes a real credential may take.
NUMERIC = "246813"
MIXED = "TESTONLY1aB2cD3"
SYMBOLS = "!@#$%^&*()_+-=[]{};:,.?/\\|~`"
SPACED = "correct horse battery staple"
UNICODE = "密码測試-β"
SINGLE = "a"


def _request(value: str) -> LoginRequest:
    return LoginRequest(sender_id="U1", target="A", otp=SecretStr(value), source="slack")


# Accepted shapes.


@pytest.mark.parametrize(
    "value", [NUMERIC, MIXED, SYMBOLS, SPACED, UNICODE, SINGLE, "a\u00a0b"]
)
def test_shapes_a_credential_may_take_are_accepted(value):
    assert check_credential(value) == value
    assert _request(value)
    assert is_credential(value)


def test_single_character_credential_is_accepted():
    assert is_credential(SINGLE)
    assert is_credential("7")


def test_maximum_length_is_inclusive_and_one_more_is_rejected():
    boundary = "A" * CREDENTIAL_MAX_LENGTH
    assert check_credential(boundary) == boundary
    with pytest.raises(ValueError):
        check_credential("A" * (CREDENTIAL_MAX_LENGTH + 1))


# Rejected shapes: emptiness and control characters only.


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "\t\t",
        " 　",  # ideographic space only
        "x\x00y",  # NUL
        "x\ny",  # LF
        "x\ry",  # CR
        "x\r\ny",
        "x\ty",  # tab
        "x\x0by",  # vertical tab
        "x\x0cy",  # form feed
        "x\x1by",  # escape
        "x\x7fy",  # DEL
        "x\x85y",  # C1 NEL
        "\xa0",  # a non-breaking space alone is still only whitespace
    ],
)
def test_empty_and_control_character_values_are_rejected(value):
    with pytest.raises(ValueError):
        check_credential(value)
    with pytest.raises(ValueError):
        _request(value)
    assert not is_credential(value)


def test_rejection_messages_never_echo_the_value():
    bad = "secret\x00value"
    with pytest.raises(ValueError) as caught:
        check_credential(bad)
    assert "secret" not in str(caught.value)
    assert "value" not in str(caught.value).replace("credential", "")


def test_internal_spaces_and_symbols_survive_the_model():
    assert _request(SPACED).otp.get_secret_value() == SPACED
    assert _request(SYMBOLS).otp.get_secret_value() == SYMBOLS


# Web entry points apply the same rule.


def test_web_real_login_accepts_an_opaque_credential():
    assert RealLoginRequest(
        account_id="a1", otp=SecretStr(SYMBOLS), confirmed=True
    )
    assert RealLoginRequest(account_id="a1", otp=None, full_slack=True)


def test_web_real_login_rejects_control_characters():
    with pytest.raises(ValidationError):
        RealLoginRequest(account_id="a1", otp=SecretStr("a\nb"), confirmed=True)


def test_web_group_request_accepts_an_opaque_credential():
    assert GroupTestRequest(
        group_name="G", otp=SecretStr(SPACED), confirmed=True, broker_confirmed=True
    )
    with pytest.raises(ValidationError):
        GroupTestRequest(group_name="G", otp=SecretStr("   "))


# Slack parser: the credential is the whole remainder after the target.


@pytest.mark.parametrize("value", [NUMERIC, MIXED, SYMBOLS, SPACED, UNICODE, SINGLE])
def test_slack_parser_preserves_the_remainder_exactly(value):
    parsed = parse_slash_command(f"/mt4 A {value}")
    assert parsed.kind.value == "login"
    assert parsed.target == "A"
    assert parsed.otp == value


def test_slack_parser_keeps_internal_spaces_of_the_credential():
    assert parse_slash_command("/mt4 A one two three").otp == "one two three"
    assert parse_slash_command("/mt4  A  one two").otp == "one two"
    assert parse_slash_command("/mt4 A one two\n").otp == "one two"


def test_slack_parser_handles_group_targets():
    parsed = parse_slash_command(f"/mt4 GROUP1 {SPACED}")
    assert parsed.target == "GROUP1"
    assert parsed.otp == SPACED


def test_status_command_is_unaffected():
    assert parse_slash_command("/mt4 status").kind.value == "status"
    assert parse_slash_command("status").kind.value == "status"
    assert parse_slash_command("/mt4 STATUS").kind.value == "status"


def test_malformed_commands_are_still_rejected_without_echoing_the_value():
    for text in ("", "   ", "/mt4foo 123456", "/mt4", "/mt4 A"):
        with pytest.raises(CommandError):
            parse_slash_command(text)
    # The first token is the target, so an invalid target still fails.
    with pytest.raises(CommandError):
        parse_slash_command("/mt4 bad/alias some value")
    # A leading space before the target leaves no target at all.
    with pytest.raises(CommandError):
        parse_slash_command("   ")
    with pytest.raises(CommandError):
        parse_slash_command("/mt4 A " + "x" * (CREDENTIAL_MAX_LENGTH + 1))


def test_command_error_message_does_not_contain_the_credential():
    with pytest.raises(CommandError) as caught:
        parse_slash_command("/mt4 A " + "z" * (CREDENTIAL_MAX_LENGTH + 1))
    assert "zzzz" not in str(caught.value)


# Redaction audit: a credential of arbitrary shape must not survive.


@pytest.mark.parametrize("value", [NUMERIC, MIXED, SYMBOLS, SPACED, UNICODE])
def test_redaction_hides_a_command_credential_of_any_shape(value):
    for text in (
        f"user sent /mt4 A {value} in Slack",
        f'{{"text": "A {value}"}}',
        f"login failed for /mt4 A {value}",
    ):
        assert value not in redact_text(text), text


def test_redaction_still_leaves_the_status_command_readable():
    assert redact_text("/mt4 status") == "/mt4 status"


def test_redaction_hides_a_long_credential_by_value_after_registration():
    from app.security.redaction import sensitive_values

    with sensitive_values(SPACED):
        assert SPACED not in redact_text(f"the value {SPACED} appears here")
    # A credential of three characters cannot be matched by value without
    # destroying the text; that limit is documented rather than worked around.
    assert sanitize_text("abcdef", ("abc",)) == "abcdef"


def test_secret_scan_and_sanitize_hide_a_credential_of_any_shape():
    for value in (MIXED, SYMBOLS, SPACED, UNICODE):
        text = f"leaked {value} here"
        assert value not in sanitize_text(text, (value,))
