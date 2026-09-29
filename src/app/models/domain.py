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

# Logical controls the Win32 dialog fallback must be able to address. login_id and
# server both live inside editable ComboBoxes whose child Edit shares one native
# control id, so each has an explicit parent anchor key as well.
_WIN32_FALLBACK_KEYS = (
    "login_id_combo",
    "login_id_edit",
    "otp",
    "server_combo",
    "server_edit",
    "login_button",
)
SLACK_USER_ID_PATTERN = re.compile(r"^[UW][A-Z0-9]{2,31}$", re.IGNORECASE)
# The broker issues a one-time code, while a Demo account is driven with a fixed
# password. Neither has a documented format, and a password may legitimately
# contain symbols, spaces or non-ASCII characters. So this value is treated as an
# opaque secret rather than a formatted code: only its size is bounded and the
# characters that would break a log line or a protocol frame are refused.
#
# The field is still called ``otp`` throughout the schema and the internals so that
# stored configuration and existing callers keep working. User-facing wording says
# "credential / password or OTP".
CREDENTIAL_MAX_LENGTH = 128
_CREDENTIAL_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def check_credential(value: str) -> str:
    """Validate an opaque login credential without assuming any format.

    Printable ASCII including symbols and spaces is allowed, as is Unicode. Only a
    value that is empty, longer than the bound, or carrying a control character is
    rejected; a control character would allow a newline to forge a log line or a
    NUL to truncate a string in a downstream consumer.

    128 is chosen because a Slack command is capped at 256 characters in total, so a
    credential of this length still leaves room for the command prefix and a
    64-character alias. It is far above any plausible broker credential and it
    bounds how much a single mistyped value can inject into one log line.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError("credential must not be empty")
    if len(value) > CREDENTIAL_MAX_LENGTH:
        raise ValueError(f"credential must be at most {CREDENTIAL_MAX_LENGTH} characters")
    if _CREDENTIAL_CONTROL_RE.search(value):
        raise ValueError(
            "credential must not contain NUL, CR, LF or other control characters"
        )
    return value
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


class Win32FallbackConfig(APIModel):
    """Explicit opt-in Win32 dialog fallback.

    Some brokers render the login form as a plain Win32 ``#32770`` dialog that UI
    Automation never exposes (verified on Rakuten MT4), so the UIA selector route
    cannot resolve it at all. This configuration addresses that dialog by native
    control id instead. It is never enabled automatically: an Account must opt in
    and must supply ids that were verified on that machine.
    """

    enabled: bool = False
    dialog_class: str = Field(default="#32770", min_length=1, max_length=128)
    anchors: list[str] = Field(default_factory=list, max_length=10)
    control_ids: dict[str, int] = Field(default_factory=dict)

    @field_validator("control_ids")
    @classmethod
    def validate_control_ids(cls, value: dict[str, int]) -> dict[str, int]:
        unknown = set(value) - set(_WIN32_FALLBACK_KEYS)
        if unknown:
            raise ValueError(f"unsupported Win32 control keys: {', '.join(sorted(unknown))}")
        for item in value.values():
            if not 0 < item <= 65535:
                raise ValueError("Win32 control ids must be within 1..65535")
        return dict(value)

    @model_validator(mode="after")
    def require_ids_when_enabled(self) -> Win32FallbackConfig:
        if self.enabled:
            missing = {"login_id_edit", "otp", "login_button"} - set(self.control_ids)
            if missing:
                raise ValueError(
                    "win32_fallback requires control_ids for: " + ", ".join(sorted(missing))
                )
        return self

    def key(self, name: str) -> int | None:
        return self.control_ids.get(name)


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
    win32_fallback: Win32FallbackConfig = Field(default_factory=Win32FallbackConfig)
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
    win32_fallback: Win32FallbackConfig = Field(default_factory=Win32FallbackConfig)
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
    win32_fallback: Win32FallbackConfig | None = None
    enabled: bool | None = None
    save_login_info: bool | None = None
    launch_timeout_seconds: float | None = Field(default=None, ge=5.0, le=180.0)
    login_timeout_seconds: float | None = Field(default=None, ge=5.0, le=600.0)
    mock_outcome: MockOutcome | None = None


class AccountGroup(APIModel):
    id: str = Field(default_factory=new_id, min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=64)
    account_ids: list[str] = Field(default_factory=list, max_length=1000)
    shared_otp_confirmed: bool = False
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
    shared_otp_confirmed: bool = False
    enabled: bool = True


class GroupPatch(APIModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    account_ids: list[str] | None = Field(default=None, max_length=1000)
    shared_otp_confirmed: bool | None = None
    enabled: bool | None = None


# Bounds for the Agent-side stale-request cutoff. This is not broker OTP validity:
# the Agent cannot know when Rakuten issued the OTP.
OTP_STALE_CUTOFF_MIN_SECONDS = 30
OTP_STALE_CUTOFF_MAX_SECONDS = 300
# The previous schema allowed otp_max_age_seconds up to 900. settings.json files
# written under it are migrated at the repository boundary, never reinterpreted.
LEGACY_OTP_MAX_AGE_MAX_SECONDS = 900


class AppSettings(APIModel):
    allowed_slack_user_ids: list[str] = Field(default_factory=list, max_length=1000)
    slack_enabled: bool = True
    automation_mode: AutomationMode = AutomationMode.AUTO
    web_port: int = Field(default=8765, ge=1024, le=65535)
    # Agent-side stale-request cutoff only; the broker's OTP issuance/validity
    # is not knowable from a Slack delivery timestamp.
    otp_max_age_seconds: int = Field(
        default=120,
        ge=OTP_STALE_CUTOFF_MIN_SECONDS,
        le=OTP_STALE_CUTOFF_MAX_SECONDS,
        description="Agent stale-request cutoff; not broker OTP validity",
    )

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
        # The field keeps its historical name; the value is an opaque credential.
        check_credential(self.otp.get_secret_value())


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
