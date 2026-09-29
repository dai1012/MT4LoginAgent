from __future__ import annotations

import pytest

from app.models.domain import AccountCreate, AccountPatch, GroupCreate, GroupPatch
from app.models.errors import ConflictError, DomainValidationError, NotFoundError


def test_account_crud_and_alias_resolution(runtime, account_payload):
    account = runtime.accounts.create(AccountCreate.model_validate(account_payload))
    assert runtime.accounts.get(account.id).alias == "A"
    assert runtime.resolver.resolve("a").accounts[0].id == account.id

    updated = runtime.accounts.update(account.id, AccountPatch(display_name="Renamed"))
    assert updated.display_name == "Renamed"
    assert runtime.accounts.set_enabled(account.id, False).enabled is False
    assert runtime.accounts.delete(account.id).alias == "A"
    with pytest.raises(NotFoundError):
        runtime.accounts.get(account.id)


def test_otp_like_numeric_alias_is_rejected(runtime, account_payload):
    with pytest.raises(DomainValidationError):
        runtime.accounts.create(
            AccountCreate.model_validate({**account_payload, "alias": "123456"})
        )


def test_account_field_sizes_are_bounded(runtime, account_payload):
    with pytest.raises(DomainValidationError):
        runtime.accounts.create(
            AccountCreate.model_validate({**account_payload, "launch_arguments": ["x" * 257]})
        )
    with pytest.raises(DomainValidationError):
        runtime.accounts.create(
            AccountCreate.model_validate(
                {**account_payload, "control_ids": {"login_id": "x" * 257, "otp": "ok"}}
            )
        )


def test_account_alias_conflict(runtime, account_payload):
    runtime.accounts.create(AccountCreate.model_validate(account_payload))
    with pytest.raises(ConflictError):
        runtime.accounts.create(
            AccountCreate.model_validate({**account_payload, "login_id": "other"})
        )


def test_account_delete_rolls_back_if_group_purge_fails(runtime, account_payload, monkeypatch):
    account = runtime.accounts.create(AccountCreate.model_validate(account_payload))
    runtime.groups.create(GroupCreate(name="GROUP1", account_ids=[account.id]))

    def fail_group_write(_items):
        raise OSError("simulated group write failure")

    monkeypatch.setattr(runtime.group_repository, "replace_all", fail_group_write)
    with pytest.raises(OSError):
        runtime.accounts.delete(account.id)
    assert runtime.accounts.get(account.id).id == account.id


def test_group_crud_order_and_delete_purges_account(runtime, account_payload):
    a = runtime.accounts.create(AccountCreate.model_validate({**account_payload, "alias": "A"}))
    b = runtime.accounts.create(
        AccountCreate.model_validate({**account_payload, "alias": "B", "login_id": "B"})
    )
    group = runtime.groups.create(GroupCreate(name="GROUP1", account_ids=[a.id, b.id]))
    assert group.shared_otp_confirmed is False
    confirmed = runtime.groups.update(group.id, GroupPatch(shared_otp_confirmed=True))
    assert confirmed.shared_otp_confirmed is True
    assert [item.alias for item in runtime.groups.accounts_for(group)] == ["A", "B"]
    reordered = runtime.groups.reorder(group.id, [b.id, a.id])
    assert reordered.account_ids == [b.id, a.id]
    runtime.accounts.delete(a.id)
    assert runtime.groups.get(group.id).account_ids == [b.id]
    assert runtime.groups.delete(group.id).name == "GROUP1"


def test_group_rejects_unknown_and_duplicate_members(runtime, account_payload):
    account = runtime.accounts.create(AccountCreate.model_validate(account_payload))
    with pytest.raises(DomainValidationError):
        runtime.groups.create(GroupCreate(name="G1", account_ids=["missing"]))
    with pytest.raises(DomainValidationError):
        runtime.groups.create(GroupCreate(name="G2", account_ids=[account.id, account.id]))


def test_alias_and_group_name_collision_is_prevented(runtime, account_payload):
    account = runtime.accounts.create(AccountCreate.model_validate(account_payload))
    with pytest.raises(ConflictError, match="already an Account alias"):
        runtime.groups.create(GroupCreate(name="A", account_ids=[account.id]))
    runtime.groups.create(GroupCreate(name="B", account_ids=[account.id]))
    with pytest.raises(ConflictError, match="already a Group name"):
        runtime.accounts.create(
            AccountCreate.model_validate({**account_payload, "alias": "B", "login_id": "other"})
        )
