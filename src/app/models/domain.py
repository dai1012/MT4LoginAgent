from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

ALIAS_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
SLACK_USER_ID_PATTERN = re.compile(r"^[UW][A-Z0-9]{2,31}$", re.IGNORECASE)
OTP_PATTERN = re.compile(r"^[0-9]{4,10}$")
_NESTED_QUANTIFIER_RE = re.compile(r"\([^)]*(?:[+*?{][^)]*)\)[+*?{]")
_ALTERNATION_QUANTIFIER_RE = re.compile(r"\([^)]*\|[^)]*\)[+*?{]")
_ADJACENT_UNBOUNDED_RE = re.compile(r"(?:\.\*|\.\+|\w\*|\w\+)\s*(?:\.\*|\.\+|\w\*|\w\+)")


def is_safe_window_regex(pattern: str | None) -> bool:
    if not pattern:
        return True
    if (
        len(pattern) > 256
        or _NESTED_QUANTIFIER_RE.search(pattern)
        or _ALTERNATION_QUANTIFIER_RE.search(pattern)
        or _ADJACENT_UNBOUNDED_RE.search(pattern)
    ):
        return False
    try:
        re.compile(pattern)
    except re.error:
        return False
    return True


def is_anchored_regex(pattern: str | None) -> bool:
    if not pattern:
        return False
    candidate = pattern.strip()
    while candidate.startswith("(?") and ")" in candidate:
        flag_end = candidate.find(")")
        prefix = candidate[: flag_end + 1]
        if prefix in {"(?i)", "(?m)", "(?s)", "(?im)", "(?is)", "(?ms)"}:
            candidate = candidate[flag_end + 1 :]
        else:
            break
    return candidate.startswith("^") and candidate.endswith("$")


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid4())


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AutomationMode(StrEnum):
    AUTO = "auto"
    MOCK = "mock"
    WINDOWS = "windows"


class MockOutcome(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    TIMEOUT = "timeout"


class LoginStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"


class HistoryStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"


class ErrorCategory(StrEnum):
    NONE = "none"
    ACCOUNT_DISABLED = "account_disabled"
    ACCOUNT_NOT_FOUND = "account_not_found"
    BROKER_OFFLINE = "broker_offline"
    MAINTENANCE = "maintenance"
    ALREADY_RUNNING_NO_LOGIN_WINDOW = "already_running_no_login_window"
    AMBIGUOUS_PROCESS = "ambiguous_process"
    INTERNAL_ERROR = "internal_error"
    INSTANCE_UNVERIFIABLE = "instance_unverifiable"
    INVALID_ACCOUNT_CONFIG = "invalid_account_config"
    LOGIN_REJECTED = "login_rejected"
    MT4_LAUNCH_FAILED = "mt4_launch_failed"
    OTP_EXPIRED = "otp_expired"
    CANCELLED = "cancelled"
    TARGET_CHANGED = "target_changed"
    TERMINAL_NOT_FOUND = "terminal_not_found"
    TIMEOUT = "timeout"
    UI_CONTROL_NOT_FOUND = "ui_control_not_found"
    UI_AUTOMATION_ERROR = "ui_automation_error"
    UI_VERIFICATION_UNVERIFIED = "ui_verification_unverified"


class AccountConfig(APIModel):
    id: str = Field(default_factory=new_id, min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=100)
    alias: str = Field(min_length=1, max_length=64)
    login_id: str = Field(min_length=1, max_length=128)
    server: str = Field(min_length=1, max_length=128)
    terminal_path: str = Field(min_length=1, max_length=1024)
    process_name: str | None = Field(default=None, max_length=128)
    profile_path: str | None = Field(default=None, max_length=1024)
    launch_arguments: list[str] = Field(default_factory=list, max_length=20)
    window_title_regex: str | None = Field(default=None, max_length=256)
    success_window_title_regex: str | None = Field(default=None, max_length=256)
    control_ids: dict[str, str] = Field(default_factory=dict)
    control_titles: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True
    save_login_info: bool = False
    launch_timeout_seconds: float = Field(default=30.0, ge=5.0, le=180.0)
    login_timeout_seconds: float = Field(default=45.0, ge=5.0, le=600.0)
    mock_outcome: MockOutcome = MockOutcome.SUCCESS
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("alias")
    @classmethod
    def validate_alias(cls, value: str) -> str:
        value = value.strip()
        if not ALIAS_PATTERN.fullmatch(value):
            raise ValueError(
                "alias must start with an alphanumeric and contain only letters, "
                "digits, '.', '_' or '-'"
            )
        if re.fullmatch(r"[0-9]{4,10}", value):
            raise ValueError("alias cannot be a 4-10 digit OTP-like value")
        return value

    @field_validator("control_ids", "control_titles")
    @classmethod
    def validate_control_maps(cls, value: dict[str, str]) -> dict[str, str]:
        allowed = {"login_id", "otp", "server", "save_login", "login_button"}
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"unsupported control keys: {', '.join(sorted(unknown))}")
        for key, item in value.items():
            if not item.strip():
                raise ValueError(f"selector for {key} cannot be empty")
        return {key: item.strip() for key, item in value.items()}

    @model_validator(mode="after")
    def normalize_values(self) -> AccountConfig:
        self.login_id = self.login_id.strip()
        self.server = self.server.strip()
        self.terminal_path = self.terminal_path.strip()
        if any(len(item) > 256 for item in self.launch_arguments):
            raise ValueError("launch argument values cannot exceed 256 characters")
        for mapping in (self.control_ids, self.control_titles):
            if any(len(value) > 256 for value in mapping.values()):
                raise ValueError("UIA selector values cannot exceed 256 characters")
        if not is_safe_window_regex(self.window_title_regex):
            raise ValueError("window_title_regex is not a safe valid regular expression")
        if not is_safe_window_regex(self.success_window_title_regex):
            raise ValueError("success_window_title_regex is not a safe valid regular expression")
        return self


class AccountCreate(APIModel):
    display_name: str = Field(min_length=1, max_length=100)
    alias: str = Field(min_length=1, max_length=64)
    login_id: str = Field(min_length=1, max_length=128)
    server: str = Field(min_length=1, max_length=128)
    terminal_path: str = Field(min_length=1, max_length=1024)
    process_name: str | None = Field(default=None, max_length=128)
    profile_path: str | None = Field(default=None, max_length=1024)
    launch_arguments: list[str] = Field(default_factory=list, max_length=20)
    window_title_regex: str | None = Field(default=None, max_length=256)
    success_window_title_regex: str | None = Field(default=None, max_length=256)
    control_ids: dict[str, str] = Field(default_factory=dict)
    control_titles: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True
    save_login_info: bool = False
    launch_timeout_seconds: float = Field(default=30.0, ge=5.0, le=180.0)
    login_timeout_seconds: float = Field(default=45.0, ge=5.0, le=600.0)
    mock_outcome: MockOutcome = MockOutcome.SUCCESS


class AccountPatch(APIModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    alias: str | None = Field(default=None, min_length=1, max_length=64)
    login_id: str | None = Field(default=None, min_length=1, max_length=128)
    server: str | None = Field(default=None, min_length=1, max_length=128)
    terminal_path: str | None = Field(default=None, min_length=1, max_length=1024)
    process_name: str | None = Field(default=None, max_length=128)
    profile_path: str | None = Field(default=None, max_length=1024)
    launch_arguments: list[str] | None = Field(default=None, max_length=20)
    window_title_regex: str | None = Field(default=None, max_length=256)
    success_window_title_regex: str | None = Field(default=None, max_length=256)
    control_ids: dict[str, str] | None = None
    control_titles: dict[str, str] | None = None
    enabled: bool | None = None
    save_login_info: bool | None = None
    launch_timeout_seconds: float | None = Field(default=None, ge=5.0, le=180.0)
    login_timeout_seconds: float | None = Field(default=None, ge=5.0, le=600.0)
    mock_outcome: MockOutcome | None = None


class AccountGroup(APIModel):
    id: str = Field(default_factory=new_id, min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=64)
    account_ids: list[str] = Field(default_factory=list, max_length=1000)
    enabled: bool = True
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not ALIAS_PATTERN.fullmatch(value):
            raise ValueError("group name has the same character rules as an account alias")
        if re.fullmatch(r"[0-9]{4,10}", value):
            raise ValueError("group name cannot be a 4-10 digit OTP-like value")
        return value

    @model_validator(mode="after")
    def unique_accounts(self) -> AccountGroup:
        if len(self.account_ids) != len(set(self.account_ids)):
            raise ValueError("a group cannot contain the same account more than once")
        return self


class GroupCreate(APIModel):
    name: str = Field(min_length=1, max_length=64)
    account_ids: list[str] = Field(default_factory=list, max_length=1000)
    enabled: bool = True


class GroupPatch(APIModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    account_ids: list[str] | None = Field(default=None, max_length=1000)
    enabled: bool | None = None


class AppSettings(APIModel):
    allowed_slack_user_ids: list[str] = Field(default_factory=list, max_length=1000)
    slack_enabled: bool = True
    automation_mode: AutomationMode = AutomationMode.AUTO
    web_port: int = Field(default=8765, ge=1024, le=65535)
    otp_max_age_seconds: int = Field(default=300, ge=30, le=900)

    @field_validator("allowed_slack_user_ids")
    @classmethod
    def validate_user_ids(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            candidate = value.strip().upper()
            if not SLACK_USER_ID_PATTERN.fullmatch(candidate):
                raise ValueError(f"invalid Slack user ID: {value}")
            if candidate not in normalized:
                normalized.append(candidate)
        return normalized


class SecretsConfig(APIModel):
    slack_app_token: SecretStr | None = None
    slack_bot_token: SecretStr | None = None
    web_admin_token: SecretStr | None = None
    dedup_hmac_key: SecretStr | None = None

    @property
    def app_token_configured(self) -> bool:
        return bool(self.slack_app_token and self.slack_app_token.get_secret_value())

    @property
    def bot_token_configured(self) -> bool:
        return bool(self.slack_bot_token and self.slack_bot_token.get_secret_value())

    @property
    def web_admin_token_configured(self) -> bool:
        return bool(self.web_admin_token and self.web_admin_token.get_secret_value())


class SlackPublicSettings(APIModel):
    enabled: bool
    app_token_configured: bool
    bot_token_configured: bool
    allowed_slack_user_ids: list[str]
    connected: bool = False
    connection_state: str = "disconnected"
    last_error: str | None = None


@dataclass(slots=True)
class LoginRequest:
    sender_id: str
    target: str
    otp: SecretStr = field(repr=False)
    source: str
    received_at: datetime = field(default_factory=utc_now)
    received_monotonic: float | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.received_at.tzinfo is None:
            self.received_at = self.received_at.replace(tzinfo=UTC)
        else:
            self.received_at = self.received_at.astimezone(UTC)
        if self.received_monotonic is None:
            age = max(0.0, (utc_now() - self.received_at).total_seconds())
            self.received_monotonic = time.monotonic() - age
        if not isinstance(self.otp, SecretStr):
            self.otp = SecretStr(str(self.otp))
        value = self.otp.get_secret_value()
        if not value:
            raise ValueError("OTP must not be empty")
        if not OTP_PATTERN.fullmatch(value):
            raise ValueError("OTP must contain 4 to 10 ASCII digits")


class AutomationResult(APIModel):
    status: LoginStatus
    category: ErrorCategory = ErrorCategory.NONE
    message: str = Field(min_length=1, max_length=300)
    started_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime = Field(default_factory=utc_now)
    verification: str = Field(default="not_verified", max_length=100)


class AccountRuntimeStatus(APIModel):
    account_id: str
    alias: str
    enabled: bool
    configuration_valid: bool
    process_state: str
    issues: list[str] = Field(default_factory=list)


class LoginExecutionItem(APIModel):
    target: str
    account_id: str
    account_alias: str
    status: LoginStatus
    error_category: ErrorCategory
    message: str
    verification: str = Field(default="not_verified", max_length=100)
    duration_ms: int = Field(ge=0)
    history_id: str | None = None


class HistoryRecord(APIModel):
    id: str = Field(default_factory=new_id, min_length=1, max_length=64)
    timestamp: datetime = Field(default_factory=utc_now)
    sender: str = Field(min_length=1, max_length=128)
    target: str = Field(min_length=1, max_length=64)
    account_alias: str = Field(min_length=1, max_length=64)
    status: HistoryStatus
    error_category: ErrorCategory = ErrorCategory.NONE
    duration_ms: int = Field(ge=0)

    def to_json_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class RuntimeSnapshot(APIModel):
    agent_status: str
    version: str
    platform: str
    python_version: str
    automation_mode: str
    slack_connected: bool
    slack_state: str
    slack_last_error: str | None = None
    started_at: datetime
    active_login_jobs: int
    queued_login_jobs: int
    account_count: int
    data_dir: str
