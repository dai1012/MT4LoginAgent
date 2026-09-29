from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

from app.adapters.slack.dedup import DuplicateGuard
from app.adapters.slack.processor import SlackCommandProcessor
from app.config.service import SettingsService
from app.models.domain import SecretsConfig
from app.mt4.login_service import LoginService

logger = logging.getLogger(__name__)


class SlackGateway:
    """Optional Slack Socket Mode adapter.

    The gateway owns only Slack transport. The MT4 core is called through LoginService,
    so a future EmailAdapter or TeamsAdapter can reuse the same policy.
    """

    def __init__(
        self,
        settings: SettingsService,
        login_service: LoginService,
        status_provider: Callable[[], dict[str, Any]],
        *,
        dedup_key: bytes | None = None,
        dedup_state_path: Path | None = None,
    ) -> None:
        self.settings = settings
        self.login_service = login_service
        self.status_provider = status_provider
        self._handler: Any | None = None
        self._connected = False
        self._state = "disconnected"
        self._last_error: str | None = None
        self._lock = asyncio.Lock()
        self._monitor_task: asyncio.Task[None] | None = None
        self._supervisor_task: asyncio.Task[None] | None = None
        self._retry_delay = 5.0
        self._disconnected_at: float | None = None
        # Shared across handler generations so a reconnect/restart cannot replay a command.
        self.dedup_guard = DuplicateGuard(
            key=dedup_key,
            state_path=dedup_state_path,
        )

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def state(self) -> str:
        return self._state

    @property
    def last_error(self) -> str | None:
        return self._last_error

    async def _refresh_connection_state(self) -> None:
        handler = self._handler
        if handler is None:
            return
        client = getattr(handler, "client", None)
        checker = getattr(client, "is_connected", None)
        if not callable(checker):
            return
        try:
            result = checker()
            if inspect.isawaitable(result):
                result = await result
            is_connected = bool(result)
        except Exception:
            is_connected = False
        if not is_connected:
            if self._connected:
                self._set_state(False, "disconnected", "Slack Socket Mode transport disconnected")
            if self._disconnected_at is None:
                self._disconnected_at = time.monotonic()
        else:
            self._disconnected_at = None
            if not self._connected and self._state == "disconnected":
                self._set_state(True, "connected", None)

    async def _connection_monitor(self) -> None:
        try:
            while self._handler is not None:
                await asyncio.sleep(2.0)
                await self._refresh_connection_state()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Slack connection monitor failed (%s)", type(exc).__name__)
            self._set_state(False, "error", type(exc).__name__)

    def _ensure_monitor(self) -> None:
        if self._monitor_task is None or self._monitor_task.done():
            self._monitor_task = asyncio.create_task(self._connection_monitor())

    def _ensure_supervisor(self) -> None:
        if self._supervisor_task is None or self._supervisor_task.done():
            self._supervisor_task = asyncio.create_task(self._reconnect_supervisor())

    async def _reconnect_supervisor(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._retry_delay)
                if self._handler is not None:
                    await self._refresh_connection_state()
                    if self._connected:
                        self._retry_delay = 5.0
                        continue
                    if (
                        self._disconnected_at is None
                        or time.monotonic() - self._disconnected_at < 15.0
                    ):
                        continue
                    stale_handler = self._handler
                    self._handler = None
                    await self._close_handler(stale_handler)
                    continue
                settings = self.settings.get()
                secrets = self.settings.secrets.get()
                if not settings.slack_enabled:
                    continue
                if not (secrets.app_token_configured and secrets.bot_token_configured):
                    continue
                connected = await self.start()
                self._retry_delay = 5.0 if connected else min(self._retry_delay * 2, 60.0)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Slack reconnect supervisor failed (%s)", type(exc).__name__)
            self._set_state(False, "error", type(exc).__name__)

    def _is_authorized(self, sender_id: str) -> bool:
        current = self.settings.get()
        return current.slack_enabled and sender_id.casefold() in {
            item.casefold() for item in current.allowed_slack_user_ids
        }

    def _allowed_aliases(self, sender_id: str) -> frozenset[str]:
        """Account aliases this Slack user may operate.

        Deliberately fail-closed: a user in the allowlist with no binding gets an
        empty set and therefore cannot operate anything. Being added to the allowlist
        must never imply access to every existing Account.
        """
        current = self.settings.get()
        key = sender_id.strip().upper()
        if not key:
            return frozenset()
        return frozenset(current.slack_user_account_bindings.get(key, ()))

    def _private_poster(
        self, secrets: SecretsConfig
    ) -> Callable[[str, str, str], Awaitable[None]]:
        """Deliver a login result to the requester only.

        In a channel the result is posted ephemerally, so only the person who ran the
        command sees it. In a bot DM the conversation is already private, and the
        ephemeral API is not a good fit there, so a normal DM message is used. The
        originating channel is never used as a broadcast.
        """
        token = secrets.slack_bot_token.get_secret_value()  # type: ignore[union-attr]

        async def post_private(channel: str, user: str, text: str) -> None:
            if not channel or not user:
                # Without a destination there is no safe way to deliver this.
                logger.error("Slack private delivery has no destination; dropping message")
                return
            from slack_sdk.web.async_client import AsyncWebClient

            client = AsyncWebClient(token=token)
            if channel.startswith("D"):
                await client.chat_postMessage(channel=channel, text=text)
            else:
                await client.chat_postEphemeral(channel=channel, user=user, text=text)

        return post_private

    async def start(self) -> bool:
        self._ensure_supervisor()
        async with self._lock:
            if self._handler is not None:
                await self._refresh_connection_state()
                self._ensure_monitor()
                return self._connected
            settings = self.settings.get()
            secrets = self.settings.secrets.get()
            if not settings.slack_enabled:
                self._set_state(False, "disabled", None)
                return False
            if not secrets.app_token_configured or not secrets.bot_token_configured:
                self._set_state(False, "not_configured", "Slack tokens are not configured")
                return False
            try:
                await self._start_handler(secrets)
            except Exception as exc:
                self._set_state(False, "error", type(exc).__name__)
                logger.error("Slack Socket Mode start failed (%s)", type(exc).__name__)
                return False
            return self._connected

    async def _start_handler(self, secrets: SecretsConfig) -> None:
        # Imports are local so a non-Slack/non-Windows install can still run the Web Admin.
        from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
        from slack_bolt.async_app import AsyncApp

        bot_token = secrets.slack_bot_token.get_secret_value()  # type: ignore[union-attr]
        app_token = secrets.slack_app_token.get_secret_value()  # type: ignore[union-attr]
        slack_app = AsyncApp(token=bot_token)
        processor = SlackCommandProcessor(
            self.login_service,
            self._is_authorized,
            self.status_provider,
            dedup_guard=self.dedup_guard,
            aliases_for=self._allowed_aliases,
            post_private=self._private_poster(secrets),
        )

        @slack_app.command("/mt4")
        async def handle_mt4_command(ack, body, say, **_kwargs):
            await processor.handle(ack=ack, body=body, say=say)

        handler = AsyncSocketModeHandler(slack_app, app_token)
        # start_async() intentionally sleeps forever; the FastAPI lifespan owns the loop.
        # Bound connection setup so a bad token cannot freeze the localhost API.
        try:
            await asyncio.wait_for(handler.connect_async(), timeout=10.0)
        except Exception:
            with suppress(Exception):
                await asyncio.wait_for(handler.close_async(), timeout=5.0)
            raise
        self._handler = handler
        self._set_state(True, "connected", None)
        self._disconnected_at = None
        self._ensure_monitor()

    async def stop(self) -> None:
        async with self._lock:
            supervisor = self._supervisor_task
            self._supervisor_task = None
            if supervisor is not None and not supervisor.done():
                supervisor.cancel()
                with suppress(asyncio.CancelledError):
                    await supervisor
            monitor = self._monitor_task
            self._monitor_task = None
            if monitor is not None and not monitor.done():
                monitor.cancel()
                with suppress(asyncio.CancelledError):
                    await monitor
            handler = self._handler
            self._set_state(False, "disconnected", None)
            self._disconnected_at = None
            if handler is None:
                self._handler = None
                return
            try:
                await self._close_handler(handler)
            finally:
                # Do not orphan a still-running handler without a shared dedup guard.
                self._handler = None

    async def _close_handler(self, handler: Any) -> None:
        try:
            close = getattr(handler, "close_async", None)
            if not callable(close):
                close = getattr(handler, "close", None)
            if callable(close):
                result = close()
                if asyncio.iscoroutine(result):
                    await asyncio.wait_for(result, timeout=5.0)
        except Exception as exc:
            logger.error("Slack Socket Mode close failed (%s)", type(exc).__name__)

    async def restart(self) -> bool:
        await self.stop()
        return await self.start()

    async def test_connection(self) -> dict[str, Any]:
        if self.connected:
            return {
                "ok": True,
                "connected": True,
                "state": self._state,
                "message": "Socket Mode is connected",
            }
        secrets = self.settings.secrets.get()
        if not secrets.app_token_configured:
            return {
                "ok": False,
                "connected": False,
                "state": "not_configured",
                "message": "App-level token is missing",
            }
        if not secrets.bot_token_configured:
            return {
                "ok": False,
                "connected": False,
                "state": "not_configured",
                "message": "Bot token is missing",
            }
        try:
            from slack_sdk import WebClient

            client = WebClient(token=secrets.slack_bot_token.get_secret_value())  # type: ignore[union-attr]
            response = await asyncio.to_thread(client.auth_test)
            if response.get("ok"):
                return {
                    "ok": True,
                    "connected": False,
                    "state": "token_valid",
                    "message": "Bot token is valid; Socket Mode is not connected",
                    "team": response.get("team"),
                    "user": response.get("user_id"),
                }
            return {
                "ok": False,
                "connected": False,
                "state": "token_rejected",
                "message": (
                    f"Slack rejected the bot token: {response.get('error', 'unknown_error')}"
                ),
            }
        except Exception as exc:
            try:
                from slack_sdk.errors import SlackApiError

                if isinstance(exc, SlackApiError):
                    error_code = (exc.response or {}).get("error", "unknown_error")
                    return {
                        "ok": False,
                        "connected": False,
                        "state": "token_rejected",
                        "message": f"Slack rejected the bot token: {error_code}",
                    }
            except ImportError:
                pass
            return {
                "ok": False,
                "connected": False,
                "state": "error",
                "message": "Slack connection test failed",
                "error_type": type(exc).__name__,
            }

    def public_status(self):
        return self._state, self._connected, self._last_error

    def _set_state(self, connected: bool, state: str, error: str | None) -> None:
        self._connected = connected
        self._state = state
        self._last_error = error
