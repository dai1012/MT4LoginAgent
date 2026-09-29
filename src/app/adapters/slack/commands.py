from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from app.models.domain import OTP_PATTERN, LoginRequest

OTP_RE = OTP_PATTERN
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
        raise CommandError("用法：/mt4 <alias-or-group> <OTP> 或 /mt4 status")
    parts = value.split()
    if len(parts) == 1 and parts[0].casefold() == "status":
        return ParsedCommand(CommandKind.STATUS)
    if len(parts) != 2:
        raise CommandError("用法：/mt4 <alias-or-group> <OTP> 或 /mt4 status")
    target, otp = parts
    if not TARGET_RE.fullmatch(target):
        raise CommandError("alias 或 group 名称格式无效")
    if not OTP_RE.fullmatch(otp):
        raise CommandError("OTP 必须是 4 到 32 位 ASCII 字母或数字，且不含空格")
    return ParsedCommand(CommandKind.LOGIN, target=target, otp=otp)


def is_otp(value: str) -> bool:
    return bool(OTP_RE.fullmatch(value))


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
