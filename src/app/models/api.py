from __future__ import annotations

from pydantic import Field, SecretStr, field_validator

from app.models.domain import APIModel, AutomationMode


class AccountEnabledUpdate(APIModel):
    enabled: bool


class GroupOrderUpdate(APIModel):
    account_ids: list[str] = Field(min_length=0, max_length=1000)


class GeneralSettingsUpdate(APIModel):
    automation_mode: AutomationMode


class SlackSettingsUpdate(APIModel):
    enabled: bool = True
    allowed_slack_user_ids: list[str] = Field(default_factory=list, max_length=1000)
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


class ApiMessage(APIModel):
    message: str


class ValidationCheck(APIModel):
    name: str
    passed: bool
    detail: str


class ValidationResult(APIModel):
    valid: bool
    checks: list[ValidationCheck]
