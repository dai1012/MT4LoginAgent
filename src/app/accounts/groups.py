from __future__ import annotations

from pydantic import ValidationError

from app.accounts.locking import catalog_locked
from app.config.repositories import AccountRepository, GroupRepository
from app.models.domain import (
    AccountConfig,
    AccountGroup,
    GroupCreate,
    GroupPatch,
    utc_now,
)
from app.models.errors import ConflictError, DomainValidationError, NotFoundError


class GroupService:
    def __init__(
        self,
        accounts: AccountRepository,
        groups: GroupRepository,
        catalog_lock: object | None = None,
    ) -> None:
        self.accounts = accounts
        self.groups = groups
        self.catalog_lock = catalog_lock or groups._lock

    def list(self) -> list[AccountGroup]:
        return sorted(self.groups.all(), key=lambda item: item.name.casefold())

    def get(self, group_id: str) -> AccountGroup:
        group = self.groups.get(group_id)
        if group is None:
            raise NotFoundError("Group not found")
        return group

    @catalog_locked
    def create(self, payload: GroupCreate) -> AccountGroup:
        self._validate_account_ids(payload.account_ids)
        group = self._validated_group(payload.model_dump())
        with self.groups.transaction() as repository:
            items = repository.all()
            if any(item.name.casefold() == group.name.casefold() for item in items):
                raise ConflictError(f"Group '{group.name}' already exists")
            if self.accounts.find_by_alias(group.name) is not None:
                raise ConflictError(f"'{group.name}' is already an Account alias")
            repository.replace_all([*items, group])
        return group

    @catalog_locked
    def update(self, group_id: str, payload: GroupPatch) -> AccountGroup:
        changes = payload.model_dump(exclude_unset=True)
        if "account_ids" in changes:
            if changes["account_ids"] is None:
                raise DomainValidationError("Group account_ids cannot be null")
            self._validate_account_ids(changes["account_ids"])
        with self.groups.transaction() as repository:
            items = repository.all()
            index = self._index(items, group_id)
            updated = self._validated_group(
                {**items[index].model_dump(), **changes, "updated_at": utc_now()}
            )
            if any(
                item.id != group_id and item.name.casefold() == updated.name.casefold()
                for item in items
            ):
                raise ConflictError(f"Group '{updated.name}' already exists")
            if self.accounts.find_by_alias(updated.name) is not None:
                raise ConflictError(f"'{updated.name}' is already an Account alias")
            items[index] = updated
            repository.replace_all(items)
        return updated

    @catalog_locked
    def delete(self, group_id: str) -> AccountGroup:
        with self.groups.transaction() as repository:
            items = repository.all()
            index = self._index(items, group_id)
            removed = items.pop(index)
            repository.replace_all(items)
        return removed

    @catalog_locked
    def add_account(self, group_id: str, account_id: str) -> AccountGroup:
        if self.accounts.get(account_id) is None:
            raise NotFoundError("Account not found")
        with self.groups.transaction() as repository:
            items = repository.all()
            index = self._index(items, group_id)
            group = items[index]
            if account_id not in group.account_ids:
                group.account_ids.append(account_id)
                group.updated_at = utc_now()
                repository.replace_all(items)
            return group.model_copy(deep=True)

    @catalog_locked
    def remove_account(self, group_id: str, account_id: str) -> AccountGroup:
        with self.groups.transaction() as repository:
            items = repository.all()
            index = self._index(items, group_id)
            group = items[index]
            if account_id in group.account_ids:
                group.account_ids.remove(account_id)
                group.updated_at = utc_now()
                repository.replace_all(items)
            return group.model_copy(deep=True)

    @catalog_locked
    def reorder(self, group_id: str, account_ids: list[str]) -> AccountGroup:
        current = self.get(group_id)
        if len(account_ids) != len(set(account_ids)):
            raise DomainValidationError("Group order contains duplicate accounts")
        if set(account_ids) != set(current.account_ids):
            raise DomainValidationError("Group order must contain the same accounts")
        return self.update(group_id, GroupPatch(account_ids=account_ids))

    @catalog_locked
    def accounts_for(self, group: AccountGroup) -> list[AccountConfig]:
        by_id = {account.id: account for account in self.accounts.all()}
        missing = [account_id for account_id in group.account_ids if account_id not in by_id]
        if missing:
            raise DomainValidationError("Group references an account that no longer exists")
        return [by_id[account_id] for account_id in group.account_ids]

    def _validate_account_ids(self, account_ids: list[str]) -> None:
        if len(account_ids) != len(set(account_ids)):
            raise DomainValidationError("A group cannot contain duplicate accounts")
        known = {account.id for account in self.accounts.all()}
        unknown = [account_id for account_id in account_ids if account_id not in known]
        if unknown:
            raise DomainValidationError("Group contains an unknown account")

    @staticmethod
    def _validated_group(values: dict[str, object]) -> AccountGroup:
        try:
            return AccountGroup.model_validate(values)
        except ValidationError as exc:
            raise DomainValidationError("Invalid group configuration") from exc

    @staticmethod
    def _index(items: list[AccountGroup], group_id: str) -> int:
        for index, item in enumerate(items):
            if item.id == group_id:
                return index
        raise NotFoundError("Group not found")
