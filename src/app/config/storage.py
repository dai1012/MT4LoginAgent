from __future__ import annotations

import json
import os
import tempfile
import threading
from collections.abc import Callable
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

from app.models.errors import ConfigurationError

T = TypeVar("T", bound=BaseModel)


class AtomicJsonStorage:
    """Small thread-safe JSON store using same-directory atomic replacement."""

    def __init__(self, path: Path, *, private: bool = False) -> None:
        self.path = path
        self.private = private
        self.lock = threading.RLock()

    def load(self, default_factory: Callable[[], Any]) -> Any:
        with self.lock:
            if not self.path.exists():
                return default_factory()
            try:
                with self.path.open("r", encoding="utf-8") as handle:
                    return json.load(handle)
            except (OSError, json.JSONDecodeError) as exc:
                raise ConfigurationError(f"Cannot read local data file: {self.path.name}") from exc

    def save(self, value: Any) -> None:
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            mode = 0o600
            fd, temporary_name = tempfile.mkstemp(
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                dir=self.path.parent,
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                    json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                with suppress(OSError):
                    os.chmod(temporary_name, mode)
                os.replace(temporary_name, self.path)
                self._fsync_parent_directory()
            except Exception:
                with suppress(OSError):
                    Path(temporary_name).unlink(missing_ok=True)
                raise

    def _fsync_parent_directory(self) -> None:
        directory_flag = getattr(os, "O_DIRECTORY", None)
        if directory_flag is None:
            return
        try:
            descriptor = os.open(self.path.parent, directory_flag)
        except OSError:
            return
        try:
            os.fsync(descriptor)
        except OSError:
            pass
        finally:
            os.close(descriptor)

    @contextmanager
    def transaction(self):
        with self.lock:
            yield


class JsonRepository(Generic[T]):
    def __init__(self, storage: AtomicJsonStorage, model: type[T]) -> None:
        self.storage = storage
        self.model = model

    def all(self) -> list[T]:
        raw = self.storage.load(list)
        if not isinstance(raw, list):
            raise ConfigurationError(f"Invalid list data in {self.storage.path.name}")
        try:
            return [self.model.model_validate(item) for item in raw]
        except Exception as exc:
            raise ConfigurationError(f"Invalid records in {self.storage.path.name}") from exc

    def replace_all(self, items: list[T]) -> None:
        self.storage.save([item.model_dump(mode="json") for item in items])

    def get(self, item_id: str) -> T | None:
        return next((item for item in self.all() if item.id == item_id), None)
