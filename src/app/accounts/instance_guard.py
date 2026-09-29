"""Catalog-level multi-instance collision guard.

The Agent drives one MT4 terminal instance per Account. Two ENABLED Accounts
must never resolve to the same MT4 instance, otherwise the automation would
target the wrong process.

The "instance fingerprint" is derived from the two paths that identify a
running MT4 instance:

- ``terminal_path`` — the terminal installation binary;
- ``profile_path`` — the MT4 process working directory (cwd), which is what
  disambiguates two copies of the same terminal binary.

``process_name`` is deliberately NOT part of the fingerprint: MetaTrader
installations share the same default process name, so it can never
distinguish two instances on its own.

Canonicalization is Windows-equivalent (case-insensitive, slash-insensitive,
separator-normalized) and platform-independent: it uses ``ntpath`` semantics
explicitly so a catalog written on one OS is judged the same on another.
This module intentionally does not import ``app.mt4.windows_automation``
(that module is Windows-only); the small normalizer here is self-contained.
"""

from __future__ import annotations

import ntpath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.domain import AccountConfig


def canonicalize_path(value: str | None) -> str:
    """Normalize a path for Windows-equivalent comparison.

    Returns ``""`` for ``None``/empty/blank input so callers can skip the
    guard instead of crashing on missing data.
    """
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not text:
        return ""
    return ntpath.normpath(text.replace("/", "\\")).casefold()


def instance_fingerprint(account: AccountConfig) -> tuple[str, str] | None:
    """Return the MT4 instance fingerprint, or ``None`` when not guardable.

    An Account with an empty/blank ``terminal_path`` carries no identifiable
    instance, so the guard skips it instead of raising.
    """
    terminal = canonicalize_path(account.terminal_path)
    if not terminal:
        return None
    return (terminal, canonicalize_path(account.profile_path))


def find_instance_collision(
    candidate: AccountConfig,
    others: list[AccountConfig],
    *,
    exclude_id: str | None = None,
) -> AccountConfig | None:
    """Find an ENABLED Account sharing the candidate's instance, if any.

    Only ENABLED Accounts participate on both sides: a disabled draft may
    temporarily share the source's fingerprint so the user can duplicate an
    Account and fix the paths before enabling it. ``exclude_id`` removes the
    account being mutated from the comparison so a self-update of an
    unrelated field never conflicts with itself.
    """
    if not candidate.enabled:
        return None
    fingerprint = instance_fingerprint(candidate)
    if fingerprint is None:
        return None
    for other in others:
        if exclude_id is not None and other.id == exclude_id:
            continue
        if not other.enabled:
            continue
        if instance_fingerprint(other) == fingerprint:
            return other
    return None


__all__ = ["canonicalize_path", "find_instance_collision", "instance_fingerprint"]
