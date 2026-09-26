from __future__ import annotations

from pathlib import Path

from app.adapters.slack.dedup import DuplicateGuard, make_event_key


def test_event_key_is_digest_only_and_ignores_volatile_response_url():
    first = make_event_key(
        {
            "trigger_id": "trigger-1",
            "team_id": "T1",
            "user_id": "U1",
            "command": "/mt4",
            "text": "A 123456",
            "response_url": "https://first.example",
        }
    )
    second = make_event_key(
        {
            "trigger_id": "trigger-1",
            "team_id": "T1",
            "user_id": "U1",
            "command": "/mt4",
            "text": "A 123456",
            "response_url": "https://second.example",
        }
    )
    assert first == second
    assert "123456" not in first
    assert len(first) == 64


def test_short_dedup_key_is_replaced():
    assert len(DuplicateGuard(key=b"short").key) >= 32


def test_dedup_does_not_block_a_distinct_otp_or_distinct_trigger():
    guard = DuplicateGuard(key=b"dedup-key")
    first = make_event_key({"channel_id": "C1", "user_id": "U1", "text": "A 111111"})
    second = make_event_key({"channel_id": "C1", "user_id": "U1", "text": "A 222222"})
    assert guard.is_duplicate(first) is False
    assert guard.is_duplicate(second) is False
    triggered = make_event_key({"trigger_id": "t2", "text": "A 111111"})
    assert guard.is_duplicate(triggered) is False


def test_duplicate_guard_expires_and_forgets():
    now = [0.0]
    guard = DuplicateGuard(ttl_seconds=10, clock=lambda: now[0])
    assert guard.is_duplicate("key") is False
    assert guard.is_duplicate("key") is True
    now[0] = 11
    assert guard.is_duplicate("key") is False
    assert guard.is_duplicate("key") is True
    guard.forget("key")
    assert guard.is_duplicate("key") is False


def test_duplicate_guard_persists_only_digest_keys(tmp_path):
    state = Path(tmp_path) / "dedup.json"
    now = [1000.0]
    key = b"persisted-dedup-key"
    first = DuplicateGuard(key=key, state_path=state, clock=lambda: now[0])
    assert first.is_duplicate("digest") is False
    second = DuplicateGuard(key=key, state_path=state, clock=lambda: now[0])
    assert second.is_duplicate("digest") is True
    assert "persisted-dedup-key" not in state.read_text(encoding="utf-8")
