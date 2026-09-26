from __future__ import annotations

from typing import Protocol

from pydantic import SecretStr

from app.models.domain import AccountConfig, AccountRuntimeStatus, AutomationResult


class MT4Automation(Protocol):
    """Replaceable MT4 boundary; channel adapters never import UI code."""

    mode: str

    async def login(self, account: AccountConfig, otp: SecretStr) -> AutomationResult: ...

    async def account_status(self, account: AccountConfig) -> AccountRuntimeStatus: ...

    async def close(self) -> None: ...
