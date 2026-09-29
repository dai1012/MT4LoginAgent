from __future__ import annotations

from pydantic import SecretStr

from app.config.repositories import SecretsRepository, SettingsRepository
from app.models.domain import AppSettings, SecretsConfig, SlackPublicSettings


class SettingsService:
    def __init__(self, settings: SettingsRepository, secrets: SecretsRepository) -> None:
        self.settings = settings
        self.secrets = secrets

    def get(self) -> AppSettings:
        return self.settings.get()

    def update(
        self,
        *,
        enabled: bool,
        allowed_slack_user_ids: list[str],
        slack_user_account_bindings: dict[str, list[str]] | None = None,
        app_token: SecretStr | None,
        bot_token: SecretStr | None,
        clear_app_token: bool,
        clear_bot_token: bool,
    ) -> SlackPublicSettings:
        # settings.update() re-validates the whole model, so a None would be written
        # into a dict field and rejected. Only pass the key when it was supplied.
        setting_changes: dict[str, object] = {
            "slack_enabled": enabled,
            "allowed_slack_user_ids": allowed_slack_user_ids,
        }
        if slack_user_account_bindings is not None:
            setting_changes["slack_user_account_bindings"] = slack_user_account_bindings
        settings = self.settings.update(**setting_changes)
        changes: dict[str, SecretStr | None] = {}
        if clear_app_token:
            changes["slack_app_token"] = None
        elif app_token is not None:
            changes["slack_app_token"] = SecretStr(app_token.get_secret_value().strip())
        if clear_bot_token:
            changes["slack_bot_token"] = None
        elif bot_token is not None:
            changes["slack_bot_token"] = SecretStr(bot_token.get_secret_value().strip())
        secret_values = self.secrets.get()
        updated = SecretsConfig(
            slack_app_token=changes.get("slack_app_token", secret_values.slack_app_token),
            slack_bot_token=changes.get("slack_bot_token", secret_values.slack_bot_token),
            web_admin_token=secret_values.web_admin_token,
            dedup_hmac_key=secret_values.dedup_hmac_key,
        )
        self.secrets.set(updated)
        return self.public_slack_settings(settings, updated)

    def set_automation_mode(self, mode: str) -> AppSettings:
        return self.settings.update(automation_mode=mode)

    def public_slack_settings(
        self,
        settings: AppSettings | None = None,
        secrets: SecretsConfig | None = None,
    ) -> SlackPublicSettings:
        current = settings or self.get()
        current_secrets = secrets or self.secrets.get()
        return SlackPublicSettings(
            enabled=current.slack_enabled,
            app_token_configured=current_secrets.app_token_configured,
            bot_token_configured=current_secrets.bot_token_configured,
            allowed_slack_user_ids=current.allowed_slack_user_ids,
            slack_user_account_bindings=dict(current.slack_user_account_bindings),
        )
