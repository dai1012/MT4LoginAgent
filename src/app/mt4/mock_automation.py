from __future__ import annotations

import asyncio

from pydantic import SecretStr

from app.models.domain import (
    AccountConfig,
    AccountRuntimeStatus,
    AutomationResult,
    ErrorCategory,
    LoginStatus,
    utc_now,
)


class MockAutomation:
    """Deterministic non-Windows implementation for development and contract tests."""

    mode = "mock"

    async def login(self, account: AccountConfig, otp: SecretStr) -> AutomationResult:
        # Yield once to model the real asynchronous UI boundary without retaining the value.
        await asyncio.sleep(0)
        started = utc_now()
        if account.mock_outcome.value == "timeout":
            return AutomationResult(
                status=LoginStatus.TIMEOUT,
                category=ErrorCategory.TIMEOUT,
                message="Mock login timed out (WINDOWS_REAL_TEST_REQUIRED)",
                started_at=started,
                verification="mock_timeout",
            )
        if account.mock_outcome.value == "failure":
            return AutomationResult(
                status=LoginStatus.FAILED,
                category=ErrorCategory.LOGIN_REJECTED,
                message="Mock login was rejected",
                started_at=started,
                verification="mock_rejected",
            )
        return AutomationResult(
            status=LoginStatus.SUCCESS,
            category=ErrorCategory.NONE,
            message="Mock login succeeded",
            started_at=started,
            verification="mock_success",
        )

    async def account_status(self, account: AccountConfig) -> AccountRuntimeStatus:
        return AccountRuntimeStatus(
            account_id=account.id,
            alias=account.alias,
            enabled=account.enabled,
            configuration_valid=True,
            process_state="mock_ready",
            issues=[],
        )

    async def close(self) -> None:
        return None
