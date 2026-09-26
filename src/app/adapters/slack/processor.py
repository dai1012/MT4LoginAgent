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
from app.models.errors import AppError
from app.mt4.login_service import LoginService
from app.security.redaction import redact_text, safe_exception, sensitive_values

logger = logging.getLogger(__name__)
Ack = Callable[..., Awaitable[Any]]
Say = Callable[..., Awaitable[Any]]


class SlackCommandProcessor:
    """Framework-neutral command policy; Bolt only supplies ack/say adapters."""

    def __init__(
        self,
        login_service: LoginService,
        is_authorized: Callable[[str], bool],
        status_provider: Callable[[], dict[str, Any]],
        dedup_guard: DuplicateGuard | None = None,
    ) -> None:
        self.login_service = login_service
        self.is_authorized = is_authorized
        self.status_provider = status_provider
        self.dedup_guard = dedup_guard or DuplicateGuard()

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
            await ack(text=self._status_text())
            return

        try:
            request = to_login_request(sender_id, command)
            resolved = self.login_service.validate_target(request.target)
        except (CommandError, AppError) as exc:
            await ack(text=self._safe_error(exc))
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
                    self._completion_callback(say=say, body=body),
                )
        except Exception as exc:
            self.dedup_guard.forget(event_key)
            logger.error("Slack command processing failed (%s)", type(exc).__name__)
            if ack_sent:
                with suppress(Exception):
                    await say(text="请求已接收，但加入登录队列失败，请检查 Agent 状态。")

    def _completion_callback(self, *, say: Say, body: dict[str, Any]):
        # Capture only the optional thread timestamp; never retain the raw Slack payload
        # (which contains the OTP) in the background task closure.
        thread_ts = body.get("thread_ts")

        async def callback(items: list[LoginExecutionItem]) -> None:
            if not items:
                return
            lines = [self._format_item(item) for item in items]
            kwargs: dict[str, Any] = {"text": "\n".join(lines)}
            if thread_ts:
                kwargs["thread_ts"] = thread_ts
            try:
                await say(**kwargs)
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

    def _status_text(self) -> str:
        status = self.status_provider()
        slack_state = "Connected" if status.get("slack_connected") else "Disconnected"
        return (
            "Rakuten MT4 Remote Login Agent V1\n"
            f"Agent: {status.get('agent_status', 'unknown')}\n"
            f"Slack: {slack_state}\n"
            f"Platform: {status.get('platform', 'unknown')}\n"
            f"Automation: {status.get('automation_mode', 'unknown')}\n"
            f"Active jobs: {status.get('active_login_jobs', 0)} "
            f"(queued {status.get('queued_login_jobs', 0)})\n"
            f"Accounts: {status.get('account_count', 0)}"
        )

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        if isinstance(exc, AppError):
            return redact_text(str(exc))
        return safe_exception(exc)
