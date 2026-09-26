from __future__ import annotations

import json
import os
import threading
from contextlib import suppress
from pathlib import Path

from app.models.domain import HistoryRecord


class HistoryStore:
    """Append-only JSONL history with a deliberately narrow, OTP-free schema."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()

    def append(self, record: HistoryRecord) -> HistoryRecord:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(record.to_json_dict(), ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            with suppress(OSError):
                self.path.chmod(0o600)
            descriptor = os.open(
                self.path,
                os.O_WRONLY | os.O_CREAT | os.O_APPEND,
                0o600,
            )
            with os.fdopen(descriptor, "a", encoding="utf-8", newline="\n") as handle:
                handle.write(payload + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            with suppress(OSError):
                self.path.chmod(0o600)
        return record

    def recent(self, limit: int = 100) -> list[HistoryRecord]:
        if limit < 1:
            return []
        with self._lock:
            if not self.path.exists():
                return []
            records: list[HistoryRecord] = []
            for raw_line in self._reverse_lines():
                if not raw_line.strip():
                    continue
                try:
                    records.append(HistoryRecord.model_validate_json(raw_line.decode("utf-8")))
                except (UnicodeDecodeError, ValueError):
                    continue
                if len(records) >= limit:
                    break
            return records

    def _reverse_lines(self, chunk_size: int = 8192):
        with self.path.open("rb") as handle:
            handle.seek(0, 2)
            position = handle.tell()
            remainder = b""
            while position > 0:
                read_size = min(chunk_size, position)
                position -= read_size
                handle.seek(position)
                remainder = handle.read(read_size) + remainder
                parts = remainder.split(b"\n")
                remainder = parts.pop(0)
                for line in reversed(parts):
                    if line:
                        yield line
            if remainder:
                yield remainder
