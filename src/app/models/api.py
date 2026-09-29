from __future__ import annotations

from typing import Any

from pydantic import Field, SecretStr, field_validator

from app.models.domain import APIModel, AutomationMode, check_credential


class AccountEnabledUpdate(APIModel):
    enabled: bool


class GroupOrderUpdate(APIModel):
    account_ids: list[str] = Field(min_length=0, max_length=1000)


class GeneralSettingsUpdate(APIModel):
    automation_mode: AutomationMode


class Win32InspectRequest(APIModel):
    account_id: str = Field(min_length=1, max_length=64)


class Win32ApplyRequest(APIModel):
    account_id: str = Field(min_length=1, max_length=64)
    # The reviewed suggestion, exactly as the inspection returned it. Applying is
    # refused unless every required field carries HIGH confidence.
    suggested: dict[str, Any] = Field(default_factory=dict)
    confidence: dict[str, str] = Field(default_factory=dict)


class SlackSettingsUpdate(APIModel):
    enabled: bool = True
    allowed_slack_user_ids: list[str] = Field(default_factory=list, max_length=1000)
    # Which Account aliases each Slack User may operate. Omitted means "leave as is"
    # so an older client that does not send the field cannot wipe every binding.
    slack_user_account_bindings: dict[str, list[str]] | None = None
    app_token: SecretStr | None = None
    bot_token: SecretStr | None = None
    clear_app_token: bool = False
    clear_bot_token: bool = False

    @field_validator("app_token", "bot_token")
    @classmethod
    def blank_is_none(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None or not value.get_secret_value().strip():
            return None
        return value


class TestPhaseRequest(APIModel):
    phase: str = Field(min_length=1, max_length=40)
    account_id: str | None = Field(default=None, max_length=64)


class RealLoginRequest(APIModel):
    account_id: str = Field(min_length=1, max_length=64)
    # Opaque credential; the field keeps its historical name for compatibility.
    otp: SecretStr | None = None
    confirmed: bool = False
    broker_confirmed: bool = False
    full_slack: bool = False

    @field_validator("otp")
    @classmethod
    def validate_credential(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            check_credential(value.get_secret_value())
        return value


class TestSelectorApplyRequest(APIModel):
    account_id: str = Field(min_length=1, max_length=64)
    confirmed: bool = False


class GroupTestRequest(APIModel):
    group_name: str = Field(min_length=1, max_length=64)
    otp: SecretStr
    confirmed: bool = False
    broker_confirmed: bool = False

    @field_validator("otp")
    @classmethod
    def validate_credential(cls, value: SecretStr) -> SecretStr:
        check_credential(value.get_secret_value())
        return value


class SlackAwaitRequest(APIModel):
    session_id: str = Field(min_length=8, max_length=80)
    timeout_seconds: int = Field(default=120, ge=5, le=300)


class ApiMessage(APIModel):
    message: str


class ValidationCheck(APIModel):
    name: str
    passed: bool
    detail: str


class ValidationResult(APIModel):
    valid: bool
    checks: list[ValidationCheck]
