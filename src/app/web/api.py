from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.accounts.validator import validate_account_configuration
from app.models.api import (
    AccountEnabledUpdate,
    GeneralSettingsUpdate,
    GroupOrderUpdate,
    SlackSettingsUpdate,
)
from app.models.domain import AccountCreate, AccountPatch, GroupCreate, GroupPatch
from app.models.errors import (
    AppError,
    ConfigurationError,
    ConflictError,
    DomainValidationError,
    NotFoundError,
)
from app.runtime import AgentRuntime
from app.security.redaction import redact_text

logger = logging.getLogger(__name__)


def create_api_router(runtime: AgentRuntime) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "rakuten-mt4-login-agent"}

    @router.get("/runtime")
    async def runtime_status() -> dict[str, object]:
        return runtime.status()

    @router.get("/dashboard")
    async def dashboard() -> dict[str, object]:
        return await runtime.dashboard()

    # Accounts
    @router.get("/accounts")
    async def list_accounts() -> list[dict[str, object]]:
        return [item.model_dump(mode="json") for item in runtime.accounts.list()]

    @router.post("/accounts", status_code=201)
    async def create_account(payload: AccountCreate) -> dict[str, object]:
        return runtime.accounts.create(payload).model_dump(mode="json")

    @router.get("/accounts/{account_id}")
    async def get_account(account_id: str) -> dict[str, object]:
        return runtime.accounts.get(account_id).model_dump(mode="json")

    @router.put("/accounts/{account_id}")
    async def update_account(account_id: str, payload: AccountPatch) -> dict[str, object]:
        return runtime.accounts.update(account_id, payload).model_dump(mode="json")

    @router.delete("/accounts/{account_id}")
    async def delete_account(account_id: str) -> dict[str, str]:
        removed = runtime.accounts.delete(account_id)
        return {"message": f"Account {removed.alias} deleted"}

    @router.post("/accounts/{account_id}/enabled")
    async def set_account_enabled(
        account_id: str, payload: AccountEnabledUpdate
    ) -> dict[str, object]:
        return runtime.accounts.set_enabled(account_id, payload.enabled).model_dump(mode="json")

    @router.post("/accounts/{account_id}/validate")
    async def validate_account(account_id: str) -> dict[str, object]:
        account = runtime.accounts.get(account_id)
        return validate_account_configuration(account).model_dump(mode="json")

    # Groups
    @router.get("/groups")
    async def list_groups() -> list[dict[str, object]]:
        output: list[dict[str, object]] = []
        for group in runtime.groups.list():
            item = group.model_dump(mode="json")
            try:
                members = runtime.groups.accounts_for(group)
                item["missing_accounts"] = []
            except DomainValidationError as exc:
                members = []
                item["missing_accounts"] = [str(exc)]
            item["accounts"] = [
                {
                    "id": account.id,
                    "alias": account.alias,
                    "display_name": account.display_name,
                    "enabled": account.enabled,
                }
                for account in members
            ]
            output.append(item)
        return output

    @router.post("/groups", status_code=201)
    async def create_group(payload: GroupCreate) -> dict[str, object]:
        return runtime.groups.create(payload).model_dump(mode="json")

    @router.get("/groups/{group_id}")
    async def get_group(group_id: str) -> dict[str, object]:
        return runtime.groups.get(group_id).model_dump(mode="json")

    @router.put("/groups/{group_id}")
    async def update_group(group_id: str, payload: GroupPatch) -> dict[str, object]:
        return runtime.groups.update(group_id, payload).model_dump(mode="json")

    @router.delete("/groups/{group_id}")
    async def delete_group(group_id: str) -> dict[str, str]:
        removed = runtime.groups.delete(group_id)
        return {"message": f"Group {removed.name} deleted"}

    @router.post("/groups/{group_id}/accounts/{account_id}")
    async def add_group_account(group_id: str, account_id: str) -> dict[str, object]:
        return runtime.groups.add_account(group_id, account_id).model_dump(mode="json")

    @router.delete("/groups/{group_id}/accounts/{account_id}")
    async def remove_group_account(group_id: str, account_id: str) -> dict[str, object]:
        return runtime.groups.remove_account(group_id, account_id).model_dump(mode="json")

    @router.put("/groups/{group_id}/order")
    async def reorder_group(group_id: str, payload: GroupOrderUpdate) -> dict[str, object]:
        return runtime.groups.reorder(group_id, payload.account_ids).model_dump(mode="json")

    # Slack and general settings
    @router.get("/slack")
    async def get_slack_settings():
        public = runtime.settings.public_slack_settings()
        state, connected, last_error = runtime.slack.public_status()
        return public.model_copy(
            update={
                "connected": connected,
                "connection_state": state,
                "last_error": last_error,
            }
        ).model_dump(mode="json")

    @router.put("/slack")
    async def update_slack_settings(payload: SlackSettingsUpdate):
        runtime.settings.update(
            enabled=payload.enabled,
            allowed_slack_user_ids=payload.allowed_slack_user_ids,
            app_token=payload.app_token,
            bot_token=payload.bot_token,
            clear_app_token=payload.clear_app_token,
            clear_bot_token=payload.clear_bot_token,
        )
        await runtime.slack.restart()
        return await get_slack_settings()

    @router.post("/slack/test")
    async def test_slack_connection() -> dict[str, object]:
        return await runtime.slack.test_connection()

    @router.get("/settings")
    async def get_general_settings() -> dict[str, object]:
        return runtime.settings.get().model_dump(mode="json")

    @router.put("/settings")
    async def update_general_settings(payload: GeneralSettingsUpdate) -> dict[str, object]:
        raise HTTPException(
            status_code=405,
            detail=(
                "General settings are read-only here; edit settings.json and restart "
                "the Agent for port/automation changes"
            ),
        )

    # History intentionally exposes only the schema below; there is no OTP field.
    @router.get("/history")
    async def list_history(limit: int = 100) -> list[dict[str, object]]:
        bounded = min(max(limit, 1), 500)
        return [item.model_dump(mode="json") for item in runtime.history.recent(bounded)]

    return router


def register_exception_handlers(app) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
        if isinstance(exc, NotFoundError):
            status = 404
        elif isinstance(exc, ConflictError):
            status = 409
        elif isinstance(exc, (DomainValidationError, ConfigurationError)):
            status = 422
        else:
            status = 400
        return JSONResponse(status_code=status, content={"detail": redact_text(str(exc))})


def install_web_routes(app, runtime: AgentRuntime) -> None:
    """Attach the HTML shell; kept separate for simple embedding and tests."""
    template = Path(__file__).parent / "templates" / "index.html"
    app.mount(
        "/static",
        StaticFiles(directory=Path(__file__).parent / "static"),
        name="static",
    )

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(template, media_type="text/html")

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> JSONResponse:
        return JSONResponse(status_code=204, content=None)
