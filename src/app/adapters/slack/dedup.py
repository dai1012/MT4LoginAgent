from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.config.storage import AtomicJsonStorage

# Fallback for unit tests/embedders that do not provide a persisted key.
_DEDUP_SECRET = secrets.token_bytes(32)


def make_event_key(body: dict[str, Any], key: bytes | None = None) -> str:
    """Create a non-reversible key for one Slack command delivery.

    Slack retry/reconnect deliveries normally retain ``trigger_id``. When a test or
    alternate transport omits it, the canonical body is HMAC'd; the raw OTP never
    becomes a map key, log field, or serialized payload.
    """
    trigger_id = str(body.get("trigger_id") or "").strip()
    if trigger_id:
        material = "|".join(
            (
                "trigger",
                str(body.get("api_app_id") or ""),
                str(body.get("team_id") or ""),
                str(body.get("user_id") or ""),
                str(body.get("command") or ""),
                trigger_id,
            )
        )
    else:
        material = "|".join(
            (
                "body",
                str(body.get("api_app_id") or ""),
                str(body.get("team_id") or ""),
                str(body.get("channel_id") or ""),
                str(body.get("user_id") or ""),
                str(body.get("command") or ""),
                str(body.get("text") or ""),
            )
        )
    return hmac.new(key or _DEDUP_SECRET, material.encode("utf-8"), hashlib.sha256).hexdigest()


class DuplicateGuard:
    """Small TTL/LRU guard with an OTP-free local digest file."""

    def __init__(
        self,
        *,
        key: bytes | None = None,
        state_path: Path | None = None,
        ttl_seconds: float = 120.0,
        max_entries: int = 2048,
        clock: Callable[[], float] = time.time,
    ) -> None:
        candidate_key = key or _DEDUP_SECRET
        self.key = candidate_key if len(candidate_key) >= 32 else secrets.token_bytes(32)
        self.state_path = state_path
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._clock = clock
        self._entries: OrderedDict[str, float] = OrderedDict()
        self._lock = threading.RLock()
        self._load()

    def is_duplicate(self, key: str) -> bool:
        now = self._clock()
        with self._lock:
            self._prune(now)
            previous = self._entries.get(key)
            if previous is not None and now - previous < self.ttl_seconds:
                self._entries.move_to_end(key)
                return True
            self._entries[key] = now
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)
            self._persist(now)
            return False

    def forget(self, key: str) -> None:
        with self._lock:
            self._entries.pop(key, None)
            self._persist(self._clock())

    def _prune(self, now: float) -> None:
        expired = [
            key for key, timestamp in self._entries.items() if now - timestamp >= self.ttl_seconds
        ]
        for key in expired:
            self._entries.pop(key, None)

    def _load(self) -> None:
        if self.state_path is None or not self.state_path.exists():
            return
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
            now = self._clock()
            for item in (raw if isinstance(raw, list) else [])[: self.max_entries]:
                key = str(item.get("key") or "")
                expires_at = float(item.get("expires_at") or 0)
                if key and expires_at > now:
                    self._entries[key] = expires_at - self.ttl_seconds
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            # A corrupt dedup file must never block startup or leak its contents.
            return

    def _persist(self, now: float) -> None:
        if self.state_path is None:
            return
        payload = [
            {"key": key, "expires_at": timestamp + self.ttl_seconds}
            for key, timestamp in self._entries.items()
        ]
        try:
            AtomicJsonStorage(self.state_path, private=True).save(payload)
        except Exception:
            # Deduplication is a safety optimization; never fail a login on disk IO.
            return
