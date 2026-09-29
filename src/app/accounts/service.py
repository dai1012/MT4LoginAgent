from __future__ import annotations

import logging

from pydantic import ValidationError

from app.accounts.instance_guard import find_instance_collision
from app.accounts.locking import catalog_locked
from app.accounts.validator import validate_account_configuration
from app.config.repositories import AccountRepository, GroupRepository
from app.models.domain import (
    AccountConfig,
    AccountCreate,
    AccountPatch,
    utc_now,
)
from app.models.errors import ConflictError, DomainValidationError, NotFoundError

logger = logging.getLogger(__name__)
MAX_ACCOUNTS = 1000


class AccountService:
    def __init__(
        self,
        accounts: AccountRepository,
        groups: GroupRepository,
        catalog_lock: object | None = None,
    ) -> None:
        self.accounts = accounts
        self.groups = groups
        self.catalog_lock = catalog_lock or accounts._lock

    def list(self) -> list[AccountConfig]:
        return sorted(self.accounts.all(), key=lambda item: item.alias.casefold())

    def get(self, account_id: str) -> AccountConfig:
        account = self.accounts.get(account_id)
        if account is None:
            raise NotFoundError("Account not found")
        return account

    @catalog_locked
    def create(self, payload: AccountCreate) -> AccountConfig:
        account = self._validated_account(payload.model_dump())
        self._ensure_valid(account)
        with self.accounts.transaction() as repository:
            items = repository.all()
            if len(items) >= MAX_ACCOUNTS:
                raise DomainValidationError("Account limit reached")
            if any(item.alias.casefold() == account.alias.casefold() for item in items):
                raise ConflictError(f"Alias '{account.alias}' already exists")
            if self.groups.find_by_name(account.alias) is not None:
                raise ConflictError(f"'{account.alias}' is already a Group name")
            self._ensure_no_instance_collision(account, items)
            repository.replace_all([*items, account])
        return account

    @catalog_locked
    def update(self, account_id: str, payload: AccountPatch) -> AccountConfig:
        with self.accounts.transaction() as repository:
            items = repository.all()
            index = self._index(items, account_id)
            changes = payload.model_dump(exclude_unset=True)
            updated = self._validated_account(
                {**items[index].model_dump(), **changes, "updated_at": utc_now()}
            )
            self._ensure_valid(updated)
            if any(
                item.id != account_id and item.alias.casefold() == updated.alias.casefold()
                for item in items
            ):
                raise ConflictError(f"Alias '{updated.alias}' already exists")
            if self.groups.find_by_name(updated.alias) is not None:
                raise ConflictError(f"'{updated.alias}' is already a Group name")
            self._ensure_no_instance_collision(updated, items, exclude_id=account_id)
            items[index] = updated
            repository.replace_all(items)
        return updated

    @catalog_locked
    def delete(self, account_id: str) -> AccountConfig:
        with self.accounts.transaction() as accounts_repository:
            items = accounts_repository.all()
            index = self._index(items, account_id)
            removed = items.pop(index)
            original_items = items[:]
            original_items.insert(index, removed)
            accounts_repository.replace_all(items)
            try:
                with self.groups.transaction() as groups_repository:
                    groups = groups_repository.all()
                    for group in groups:
                        if account_id in group.account_ids:
                            group.account_ids.remove(account_id)
                            group.updated_at = utc_now()
                    groups_repository.replace_all(groups)
            except Exception:
                try:
                    accounts_repository.replace_all(original_items)
                except Exception as rollback_error:
                    logger.error(
                        "Account delete rollback failed (%s)", type(rollback_error).__name__
                    )
                raise
        return removed

    @catalog_locked
    def set_enabled(self, account_id: str, enabled: bool) -> AccountConfig:
        with self.accounts.transaction() as repository:
            items = repository.all()
            index = self._index(items, account_id)
            updated = items[index].model_copy(update={"enabled": enabled, "updated_at": utc_now()})
            # Turning an Account off never raises; enabling joins the guard.
            if enabled:
                self._ensure_no_instance_collision(updated, items, exclude_id=account_id)
            items[index] = updated
            repository.replace_all(items)
            return updated

    @staticmethod
    def _ensure_valid(account: AccountConfig) -> None:
        result = validate_account_configuration(account)
        if not result.valid:
            failed = "; ".join(
                f"{check.name}: {check.detail}" for check in result.checks if not check.passed
            )
            raise DomainValidationError(f"Account configuration is invalid: {failed}")

    @staticmethod
    def _validated_account(values: dict[str, object]) -> AccountConfig:
        try:
            return AccountConfig.model_validate(values)
        except ValidationError as exc:
            details = "; ".join(
                f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
                for error in exc.errors()
            )
            raise DomainValidationError(f"Invalid account configuration: {details}") from exc

    @staticmethod
    def _ensure_no_instance_collision(
        candidate: AccountConfig,
        others: list[AccountConfig],
        *,
        exclude_id: str | None = None,
    ) -> None:
        other = find_instance_collision(candidate, others, exclude_id=exclude_id)
        if other is None:
            return
        raise DomainValidationError(
            f"MT4 instance collision: account '{candidate.alias}' resolves to the same "
            f"terminal instance as enabled account '{other.alias}' (id '{other.id}'). "
            "Each concurrently running MT4 Account needs its own terminal "
            "installation folder and its own process working directory (cwd), per "
            "MetaTrader multi-instance guidance; install a separate terminal copy "
            "and configure a separate cwd for this Account before enabling it."
        )

    @staticmethod
    def _index(items: list[AccountConfig], account_id: str) -> int:
        for index, item in enumerate(items):
            if item.id == account_id:
                return index
        raise NotFoundError("Account not found")


__all__ = ["AccountService", "ConflictError", "DomainValidationError"]
