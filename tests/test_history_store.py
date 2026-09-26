from __future__ import annotations

from app.history.store import HistoryStore
from app.models.domain import ErrorCategory, HistoryRecord, HistoryStatus


def test_history_recent_reads_latest_records_from_large_jsonl(tmp_path):
    store = HistoryStore(tmp_path / "history.jsonl")
    for index in range(200):
        store.append(
            HistoryRecord(
                sender="U1",
                target="A",
                account_alias=f"A{index}",
                status=HistoryStatus.SUCCESS,
                error_category=ErrorCategory.NONE,
                duration_ms=index,
            )
        )
    recent = store.recent(3)
    assert [record.account_alias for record in recent] == ["A199", "A198", "A197"]
