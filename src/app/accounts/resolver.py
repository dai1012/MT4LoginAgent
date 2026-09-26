from __future__ import annotations

from dataclasses import dataclass

from app.accounts.groups import GroupService
from app.config.repositories import AccountRepository
from app.models.domain import AccountConfig
from app.models.errors import DomainValidationError, NotFoundError


@dataclass(frozen=True, slots=True)
class ResolvedTarget:
    kind: str
    name: str
    target_id: str
    accounts: tuple[AccountConfig, ...]


class TargetResolver:
    def __init__(
        self, accounts: AccountRepository, groups: GroupService, catalog_lock=None
    ) -> None:
        self.accounts = accounts
        self.groups = groups
        self.catalog_lock = catalog_lock or accounts._lock

    def get_account(self, account_id: str) -> AccountConfig | None:
        with self.catalog_lock:
            return self.accounts.get(account_id)

    def resolve(self, target: str) -> ResolvedTarget:
        with self.catalog_lock:
            normalized = target.strip()
            if not normalized:
                raise DomainValidationError("Target is required")
            account = self.accounts.find_by_alias(normalized)
            group = self.groups.groups.find_by_name(normalized)
            if account is not None and group is not None:
                raise DomainValidationError(
                    f"Target '{normalized}' is both an account alias and a group name; rename one"
                )
            if account is not None:
                return ResolvedTarget("account", account.alias, account.id, (account,))
            if group is not None:
                if not group.enabled:
                    raise DomainValidationError(f"Group '{group.name}' is disabled")
                accounts = self.groups.accounts_for(group)
                if not accounts:
                    raise DomainValidationError(f"Group '{group.name}' has no accounts")
                return ResolvedTarget("group", group.name, group.id, tuple(accounts))
            raise NotFoundError(f"Alias or group '{normalized}' does not exist")
