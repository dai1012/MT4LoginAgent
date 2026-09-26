from __future__ import annotations

import threading
from collections.abc import Callable
from functools import wraps
from typing import ParamSpec, TypeVar, cast

P = ParamSpec("P")
R = TypeVar("R")


def catalog_locked(method: Callable[P, R]) -> Callable[P, R]:
    """Serialize catalog mutations and resolution across the shared data files."""

    @wraps(method)
    def wrapper(self, *args: P.args, **kwargs: P.kwargs) -> R:
        with self.catalog_lock:  # type: ignore[attr-defined]
            return method(self, *args, **kwargs)

    return cast(Callable[P, R], wrapper)


__all__ = ["catalog_locked", "threading"]
