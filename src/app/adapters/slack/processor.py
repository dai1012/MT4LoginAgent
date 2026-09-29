from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any

from app.adapters.slack.commands import (
    CommandError,
    CommandKind,
    parse_slash_command,
    to_login_request,
)
from app.adapters.slack.dedup import DuplicateGuard, make_event_key
from app.models.domain import LoginExecutionItem, LoginStatus
from app.models.errors import AppError, NotFoundError
from app.mt4.login_service import LoginService
from app.security.redaction import redact_text, safe_exception, sensitive_values

logger = logging.getLogger(__name__)
Ack = Callable[..., Awaitable[Any]]
Say = Callable[..., Awaitable[Any]]

# One message for "no such target" and "exists but not yours". Any difference would
# turn the reply into an oracle for enumerating which aliases exist.
_TARGET_UNAVAILABLE = (
    "Target unavailable. 该目标不存在或未分配给你；"
    "请在 Web Admin → Slack → Account bindings 中为你的 User ID 绑定 alias。"
)


class SlackCommandProcessor:
    """Framework-neutral command policy; Bolt only supplies ack/say adapters."""

    def __init__(
        self,
        login_service: LoginService,
        is_authorized: Callable[[str], bool],
        status_provider: Callable[[], dict[str, Any]],
        dedup_guard: DuplicateGuard | None = None,
        aliases_for: Callable[[str], frozenset[str]] | None = None,
        post_private: Callable[[str, str, str], Awaitable[None]] | None = None,
    ) -> None:
        self.login_service = login_service
        self.is_authorized = is_authorized
        self.status_provider = status_provider
        self.dedup_guard = dedup_guard or DuplicateGuard()
        # Both collaborators default to the least permissive behaviour, so a wiring
        # mistake can only ever refuse access, never grant it or broadcast a result.
        self.aliases_for = aliases_for or (lambda _sender_id: frozenset())

        async def _no_private_post(channel: str, _user: str, _text: str) -> None:
            logger.error(
                "Slack private delivery is not wired; dropping a completion message "
                "(channel_present=%s)",
                bool(channel),
            )

        self.post_private: Callable[[str, str, str], Awaitable[None]] = (
            post_private or _no_private_post
        )

    def _authorized_for(self, resolved: Any, allowed: frozenset[str]) -> bool:
        """Whether this Slack user may operate the resolved target.

        Group is paused as a feature, but the command still accepts a group name, so
        the boundary is enforced here: every member must be bound to the user. The
        failure never names the member that was missing.
        """
        if not allowed:
            return False
        if resolved.kind == "group":
            return all(account.alias in allowed for account in resolved.accounts)
        return resolved.name in allowed

    async def handle(self, *, ack: Ack, body: dict[str, Any], say: Say) -> None:
        if not isinstance(body, dict):
            await ack(text="Slack 请求格式无效。")
            return
        sender_id = str(body.get("user_id") or "").strip()
        if not self.is_authorized(sender_id):
            if sender_id:
                await ack(
                    text=(
                        f"未授权：你的 Slack User ID 是 `{sender_id}`。"
                        "请把它加入 Web Admin → Slack → Allowed Slack User IDs 后重试。"
                    )
                )
            else:
                await ack(text="未授权：Slack 请求没有可识别的 User ID。")
            return

        try:
            command = parse_slash_command(str(body.get("text") or ""))
        except CommandError as exc:
            await ack(text=str(exc))
            return

        if command.kind == CommandKind.STATUS:
            await ack(text=self._status_text(sender_id))
            return

        try:
            request = to_login_request(sender_id, command)
            resolved = self.login_service.validate_target(request.target)
        except NotFoundError:
            # A target that does not exist and one this user may not touch must be
            # indistinguishable, otherwise the reply becomes an alias oracle.
            await ack(text=_TARGET_UNAVAILABLE)
            return
        except (CommandError, AppError) as exc:
            await ack(text=self._safe_error(exc))
            return

        # Authorisation happens before dedup registration, before the queue and
        # before anything is typed into MT4, so a denied request can neither consume
        # a credential nor occupy a login slot.
        if not self._authorized_for(resolved, self.aliases_for(sender_id)):
            await ack(text=_TARGET_UNAVAILABLE)
            return

        event_key = make_event_key(body, self.dedup_guard.key)
        if self.dedup_guard.is_duplicate(event_key):
            await ack(text="检测到重复的 Slack 请求，已忽略；不会再次执行 MT4 登录。")
            return

        if not self.login_service.can_accept(request.target):
            self.dedup_guard.forget(event_key)
            await ack(text="目标账号已有登录任务或队列已满，请等待当前任务完成后再试。")
            return

        # Acknowledge before creating the background task so completion can never race
        # ahead of the required "processing" response.
        safe_target_name = redact_text(resolved.name, (command.otp,))
        ack_sent = False
        try:
            await ack(
                text=(
                    f"已接收 `{safe_target_name}` 的登录请求，状态：处理中。"
                    "完成后会回复每个账号的结果。"
                )
            )
            ack_sent = True
            with sensitive_values(command.otp):
                self.login_service.submit(
                    request,
                    self._completion_callback(
                        channel=str(body.get("channel_id") or ""),
                        sender_id=sender_id,
                    ),
                )
        except Exception as exc:
            self.dedup_guard.forget(event_key)
            logger.error("Slack command processing failed (%s)", type(exc).__name__)
            if ack_sent:
                with suppress(Exception):
                    await self.post_private(
                        str(body.get("channel_id") or ""),
                        sender_id,
                        "请求已接收，但加入登录队列失败，请检查 Agent 状态。",
                    )

    def _completion_callback(self, *, channel: str, sender_id: str):
        # Capture only the destination identity. Never retain the raw Slack payload
        # (which contains the credential) in the background task closure.
        async def callback(items: list[LoginExecutionItem]) -> None:
            if not items:
                return
            # Delivery is private to the requester. say() is deliberately not used:
            # it posts into the originating channel, which in a shared channel would
            # show one person's login result to everyone.
            try:
                await self.post_private(
                    channel, sender_id, "\n".join(self._format_item(i) for i in items)
                )
            except Exception as exc:
                logger.error("Slack completion message failed (%s)", type(exc).__name__)

        return callback

    @staticmethod
    def _format_item(item: LoginExecutionItem) -> str:
        mock_marker = " [MOCK/非真实登录]" if item.verification.startswith("mock_") else ""
        if item.status == LoginStatus.SUCCESS:
            return f"✅ `{item.account_alias}` 登录成功{mock_marker}（{item.duration_ms} ms）"
        icon = "⏱️" if item.status == LoginStatus.TIMEOUT else "❌"
        category = item.error_category.value
        return (
            f"{icon} `{item.account_alias}` 登录失败{mock_marker}：{item.message} "
            f"[{category}]（{item.duration_ms} ms）"
        )

    def _status_text(self, sender_id: str) -> str:
        """Status scoped to the caller.

        Global counters such as the total account count or the number of queued jobs
        describe other people's work, so they are not reported here. Only the
        caller's own assigned aliases appear.
        """
        status = self.status_provider()
        slack_state = "Connected" if status.get("slack_connected") else "Disconnected"
        header = (
            "Rakuten MT4 Remote Login Agent V1\n"
            f"Agent: {status.get('agent_status', 'unknown')}\n"
            f"Slack: {slack_state}\n"
            f"Platform: {status.get('platform', 'unknown')}\n"
            f"Automation: {status.get('automation_mode', 'unknown')}\n"
        )
        aliases = sorted(self.aliases_for(sender_id))
        if not aliases:
            return header + "Accounts: No accounts assigned."
        return header + f"Accounts: {', '.join(aliases)}"

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        if isinstance(exc, AppError):
            return redact_text(str(exc))
        return safe_exception(exc)
