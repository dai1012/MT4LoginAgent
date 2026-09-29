from __future__ import annotations

import logging
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from contextvars import ContextVar, Token

_SENSITIVE_CONTEXT: ContextVar[tuple[str, ...]] = ContextVar("sensitive_context", default=())
_SLACK_TOKEN_RE = re.compile(r"\b(?:xox[baprs]-|xapp-)[A-Za-z0-9-]+\b", re.IGNORECASE)
# These must stay in step with models.domain.OTP_PATTERN. A real Rakuten
# credential mixed letters and digits and was 15 characters long, so a 4-10
# digit pattern would fail to redact a live value that redact_text cannot
# otherwise see by value.
_OTP_TOKEN = r"[A-Za-z0-9]{4,32}"
_COMMAND_RE = re.compile(rf"(?i)(/mt4\s+)([A-Za-z0-9_.-]+)(\s+)({_OTP_TOKEN})\b")
_SLACK_TEXT_RE = re.compile(
    rf"(?i)([\"']?text[\"']?\s*[:=]\s*[\"']?)([A-Za-z0-9_.-]+)(\s+)({_OTP_TOKEN})(?=[\"']?[,}}\s])"
)
_KEY_VALUE_RE = re.compile(
    r"(?i)([\"']?)\b(otp|one[-_ ]?time(?:\s+password)?|password|app[_ -]?token|bot[_ -]?token)\b"
    r"([\"']?)(\s*[:=]\s*)([^\s,;}]+)"
)
_LIVE_SECRETS: OrderedDict[str, tuple[float, int]] = OrderedDict()
_LIVE_SECRETS_LOCK = threading.RLock()
_LIVE_SECRET_TTL_SECONDS = 300.0
_MAX_LIVE_SECRETS = 256


def _register_live_secret(value: str) -> None:
    if not value:
        return
    now = time.monotonic()
    with _LIVE_SECRETS_LOCK:
        previous = _LIVE_SECRETS.get(value)
        count = previous[1] + 1 if previous else 1
        _LIVE_SECRETS[value] = (now, count)
        _LIVE_SECRETS.move_to_end(value)
        while len(_LIVE_SECRETS) > _MAX_LIVE_SECRETS:
            _LIVE_SECRETS.popitem(last=False)


def _release_live_secret(value: str) -> None:
    with _LIVE_SECRETS_LOCK:
        previous = _LIVE_SECRETS.get(value)
        if previous is None:
            return
        if previous[1] <= 1:
            _LIVE_SECRETS.pop(value, None)
        else:
            _LIVE_SECRETS[value] = (previous[0], previous[1] - 1)


def _live_secret_candidates() -> tuple[str, ...]:
    now = time.monotonic()
    with _LIVE_SECRETS_LOCK:
        expired = [
            key
            for key, (timestamp, _count) in _LIVE_SECRETS.items()
            if now - timestamp >= _LIVE_SECRET_TTL_SECONDS
        ]
        for key in expired:
            _LIVE_SECRETS.pop(key, None)
        return tuple(_LIVE_SECRETS)


@contextmanager
def sensitive_values(*values: str | None) -> Iterator[None]:
    cleaned = tuple(dict.fromkeys(value for value in values if value))
    if not cleaned:
        yield
        return
    current = _SENSITIVE_CONTEXT.get()
    combined = tuple(dict.fromkeys((*current, *cleaned)))
    for value in cleaned:
        _register_live_secret(value)
    token: Token[tuple[str, ...]] = _SENSITIVE_CONTEXT.set(combined)
    try:
        yield
    finally:
        # A cancelled task may close its generator in a different context.
        with suppress(ValueError):
            _SENSITIVE_CONTEXT.reset(token)
        # Keep the bounded registry entry until its TTL expires so records emitted
        # just after a task's context closes are still redacted.


def redact_text(value: object, extra_values: tuple[str, ...] = ()) -> str:
    text = str(value)
    candidates = sorted(
        {
            candidate
            for candidate in (*_SENSITIVE_CONTEXT.get(), *_live_secret_candidates(), *extra_values)
            if isinstance(candidate, str) and len(candidate) >= 4
        },
        key=len,
        reverse=True,
    )
    for candidate in candidates:
        text = text.replace(candidate, "<redacted>")
    text = _COMMAND_RE.sub(r"\1\2\3<redacted>", text)
    text = _SLACK_TEXT_RE.sub(r"\1\2\3<redacted>", text)
    text = _KEY_VALUE_RE.sub(r"\1\2\3\4<redacted>", text)
    text = _SLACK_TOKEN_RE.sub("<redacted-slack-token>", text)
    return text


def safe_exception(exc: BaseException, extra_values: tuple[str, ...] = ()) -> str:
    return redact_text(f"{type(exc).__name__}: {exc}", extra_values)


class RedactingFilter(logging.Filter):
    """Redacts known secrets and common secret-bearing text from all log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            rendered = record.getMessage()
        except Exception:
            rendered = "<unrenderable log message>"
        record.msg = redact_text(rendered)
        record.args = ()
        # Never let formatter tracebacks or stack dumps bypass the redaction filter.
        record.exc_info = None
        if record.stack_info:
            record.stack_info = redact_text(record.stack_info)
        return True


def install_redaction_filter() -> None:
    """Install redaction in a fresh spawned interpreter without touching the log file."""
    redactor = RedactingFilter()
    root = logging.getLogger()
    if not any(isinstance(item, RedactingFilter) for item in root.filters):
        root.addFilter(redactor)
    for handler in root.handlers:
        if not any(isinstance(item, RedactingFilter) for item in handler.filters):
            handler.addFilter(redactor)
    last_resort = getattr(logging, "lastResort", None)
    if last_resort is not None and not any(
        isinstance(item, RedactingFilter) for item in last_resort.filters
    ):
        last_resort.addFilter(redactor)
