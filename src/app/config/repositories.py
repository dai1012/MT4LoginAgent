from __future__ import annotations

import logging
import threading

from pydantic import SecretStr, ValidationError

from app.config.paths import AppPaths
from app.config.storage import AtomicJsonStorage
from app.models.domain import (
    LEGACY_OTP_MAX_AGE_MAX_SECONDS,
    OTP_STALE_CUTOFF_MAX_SECONDS,
    AccountConfig,
    AccountGroup,
    AppSettings,
    SecretsConfig,
)
from app.models.errors import ConfigurationError

logger = logging.getLogger(__name__)


def migrate_legacy_settings(raw: object) -> object:
    """Accept settings.json written before the stale-cutoff cap was tightened.

    ``otp_max_age_seconds`` used to allow up to 900. It has always been an
    Agent-side cutoff rather than broker OTP validity, so a legacy value above the
    current cap is clamped down with a warning instead of blocking startup. Values
    outside the previous schema's own range are left untouched and still fail
    validation loudly, so real typos are never silently rewritten.
    """
    if not isinstance(raw, dict):
        return raw
    value = raw.get("otp_max_age_seconds")
    if isinstance(value, bool) or not isinstance(value, int):
        return raw
    if not OTP_STALE_CUTOFF_MAX_SECONDS < value <= LEGACY_OTP_MAX_AGE_MAX_SECONDS:
        return raw
    logger.warning(
        "settings.json: otp_max_age_seconds=%s was valid under the previous schema but "
        "exceeds the Agent stale-request cutoff cap of %s; clamping to %s. This is an "
        "Agent-side cutoff, not Rakuten broker OTP validity.",
        value,
        OTP_STALE_CUTOFF_MAX_SECONDS,
        OTP_STALE_CUTOFF_MAX_SECONDS,
    )
    return {**raw, "otp_max_age_seconds": OTP_STALE_CUTOFF_MAX_SECONDS}


class AccountRepository:
    def __init__(self, paths: AppPaths, lock: threading.RLock | None = None) -> None:
        self.storage = AtomicJsonStorage(paths.accounts_file, private=True)
        self._items: list[AccountConfig] | None = None
        self._lock = lock or threading.RLock()

    def all(self) -> list[AccountConfig]:
        with self._lock:
            if self._items is None:
                raw = self.storage.load(list)
                if not isinstance(raw, list):
                    raise ConfigurationError("accounts.json must contain a list")
                try:
                    self._items = [AccountConfig.model_validate(item) for item in raw]
                except Exception as exc:
                    raise ConfigurationError("Invalid account data in accounts.json") from exc
            return [item.model_copy(deep=True) for item in self._items]

    def replace_all(self, items: list[AccountConfig]) -> None:
        with self._lock:
            self.storage.save([item.model_dump(mode="json") for item in items])
            self._items = [item.model_copy(deep=True) for item in items]

    def get(self, account_id: str) -> AccountConfig | None:
        return next((item for item in self.all() if item.id == account_id), None)

    def find_by_alias(self, alias: str) -> AccountConfig | None:
        folded = alias.casefold()
        return next((item for item in self.all() if item.alias.casefold() == folded), None)

    def transaction(self):
        return _RepositoryTransaction(self)


class GroupRepository:
    def __init__(self, paths: AppPaths, lock: threading.RLock | None = None) -> None:
        self.storage = AtomicJsonStorage(paths.groups_file, private=True)
        self._items: list[AccountGroup] | None = None
        self._lock = lock or threading.RLock()

    def all(self) -> list[AccountGroup]:
        with self._lock:
            if self._items is None:
                raw = self.storage.load(list)
                if not isinstance(raw, list):
                    raise ConfigurationError("groups.json must contain a list")
                try:
                    self._items = [AccountGroup.model_validate(item) for item in raw]
                except Exception as exc:
                    raise ConfigurationError("Invalid group data in groups.json") from exc
            return [item.model_copy(deep=True) for item in self._items]

    def replace_all(self, items: list[AccountGroup]) -> None:
        with self._lock:
            self.storage.save([item.model_dump(mode="json") for item in items])
            self._items = [item.model_copy(deep=True) for item in items]

    def get(self, group_id: str) -> AccountGroup | None:
        return next((item for item in self.all() if item.id == group_id), None)

    def find_by_name(self, name: str) -> AccountGroup | None:
        folded = name.casefold()
        return next((item for item in self.all() if item.name.casefold() == folded), None)

    def transaction(self):
        return _RepositoryTransaction(self)


class _RepositoryTransaction:
    def __init__(self, repository: AccountRepository | GroupRepository) -> None:
        self.repository = repository

    def __enter__(self):
        self.repository._lock.acquire()
        return self.repository

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.repository._lock.release()


class SettingsRepository:
    def __init__(self, paths: AppPaths) -> None:
        self.storage = AtomicJsonStorage(paths.settings_file, private=True)
        self._value: AppSettings | None = None
        self._lock = threading.RLock()

    def get(self) -> AppSettings:
        with self._lock:
            if self._value is None:
                raw = self.storage.load(dict)
                try:
                    self._value = AppSettings.model_validate(migrate_legacy_settings(raw))
                except Exception as exc:
                    raise ConfigurationError("Invalid settings.json") from exc
            return self._value.model_copy(deep=True)

    def set(self, settings: AppSettings) -> None:
        with self._lock:
            self.storage.save(settings.model_dump(mode="json"))
            self._value = settings.model_copy(deep=True)

    def update(self, **changes: object) -> AppSettings:
        with self._lock:
            current = self.get()
            updated = current.model_copy(update=changes)
            try:
                validated = AppSettings.model_validate(updated.model_dump())
            except ValidationError as exc:
                raise ConfigurationError("Invalid settings values") from exc
            self.set(validated)
            return validated


class SecretsRepository:
    def __init__(self, paths: AppPaths) -> None:
        self.storage = AtomicJsonStorage(paths.secrets_file, private=True)
        self._value: SecretsConfig | None = None
        self._lock = threading.RLock()

    def get(self) -> SecretsConfig:
        with self._lock:
            if self._value is None:
                raw = self.storage.load(dict)
                try:
                    self._value = SecretsConfig.model_validate(raw)
                except Exception as exc:
                    raise ConfigurationError("Invalid secrets.json") from exc
            return SecretsConfig(
                slack_app_token=self._value.slack_app_token,
                slack_bot_token=self._value.slack_bot_token,
                web_admin_token=self._value.web_admin_token,
                dedup_hmac_key=self._value.dedup_hmac_key,
            )

    def set(self, secrets: SecretsConfig) -> None:
        with self._lock:
            value = {
                "slack_app_token": (
                    secrets.slack_app_token.get_secret_value() if secrets.slack_app_token else None
                ),
                "slack_bot_token": (
                    secrets.slack_bot_token.get_secret_value() if secrets.slack_bot_token else None
                ),
                "web_admin_token": (
                    secrets.web_admin_token.get_secret_value() if secrets.web_admin_token else None
                ),
                "dedup_hmac_key": (
                    secrets.dedup_hmac_key.get_secret_value() if secrets.dedup_hmac_key else None
                ),
            }
            self.storage.save(value)
            self._value = SecretsConfig(
                slack_app_token=SecretStr(value["slack_app_token"])
                if value["slack_app_token"]
                else None,
                slack_bot_token=SecretStr(value["slack_bot_token"])
                if value["slack_bot_token"]
                else None,
                web_admin_token=SecretStr(value["web_admin_token"])
                if value["web_admin_token"]
                else None,
                dedup_hmac_key=SecretStr(value["dedup_hmac_key"])
                if value["dedup_hmac_key"]
                else None,
            )

    def update(self, **changes: object) -> SecretsConfig:
        with self._lock:
            current = self.get()
            updated = current.model_copy(update=changes)
            self.set(updated)
            return self.get()
