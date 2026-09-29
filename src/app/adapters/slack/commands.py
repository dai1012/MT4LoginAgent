from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from app.models.domain import LoginRequest, check_credential

TARGET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
MAX_COMMAND_TEXT_LENGTH = 256


class CommandKind(StrEnum):
    STATUS = "status"
    LOGIN = "login"


class CommandError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ParsedCommand:
    kind: CommandKind
    target: str | None = None
    otp: str | None = field(default=None, repr=False)


def parse_slash_command(text: str) -> ParsedCommand:
    if len(text or "") > MAX_COMMAND_TEXT_LENGTH:
        raise CommandError("命令过长")
    value = (text or "").strip()
    if value.casefold().startswith("/mt4"):
        remainder = value[4:]
        if remainder and not remainder[0].isspace():
            raise CommandError("未知 Slack 命令")
        value = remainder.strip()
    if not value:
        raise CommandError("用法：/mt4 <alias-or-group> <credential> 或 /mt4 status")
    # The target is the first token; the credential is everything after it. A fixed
    # Demo password may legitimately contain spaces, so the remainder is not split
    # again on whitespace. The command text was already stripped above, so only the
    # separator between target and credential is consumed.
    parts = value.split(None, 1)
    if len(parts) == 1:
        if parts[0].casefold() == "status":
            return ParsedCommand(CommandKind.STATUS)
        raise CommandError("用法：/mt4 <alias-or-group> <credential> 或 /mt4 status")
    target, credential = parts
    if not TARGET_RE.fullmatch(target):
        raise CommandError("alias 或 group 名称格式无效")
    try:
        check_credential(credential)
    except ValueError:
        # The rejected value is deliberately not echoed: it is a secret.
        raise CommandError("credential 无效：不能为空、不能含控制字符，且长度有上限") from None
    return ParsedCommand(CommandKind.LOGIN, target=target, otp=credential)


def is_credential(value: str) -> bool:
    """True when the value is usable as an opaque login credential."""
    try:
        check_credential(value)
    except ValueError:
        return False
    return True


def to_login_request(sender_id: str, command: ParsedCommand) -> LoginRequest:
    if command.kind != CommandKind.LOGIN or not command.target or not command.otp:
        raise CommandError("登录命令缺少必要参数")
    # The parser's value is held only in the in-memory request; it is never persisted.
    from pydantic import SecretStr

    return LoginRequest(
        sender_id=sender_id,
        target=command.target,
        otp=SecretStr(command.otp),
        source="slack",
    )
