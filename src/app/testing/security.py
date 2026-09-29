"""Secret-safe scanning and redaction helpers for acceptance reports."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from app.config.paths import AppPaths
from app.models.domain import SecretsConfig
from app.security.redaction import redact_text
from app.testing.models import TestCategory, TestClass, TestResult, TestSeverity, TestStatus


def secret_candidates(secrets: SecretsConfig, *, otp: str | None = None) -> tuple[str, ...]:
    values: list[str] = []
    if otp:
        values.append(otp)
    for value in (
        secrets.slack_app_token,
        secrets.slack_bot_token,
        secrets.web_admin_token,
        secrets.dedup_hmac_key,
    ):
        if value is not None:
            secret = value.get_secret_value()
            if secret:
                values.append(secret)
    return tuple(sorted(set(values), key=len, reverse=True))


# A credential may legitimately be only one or two characters long, but a substring
# scan cannot treat a value that short as a secret: it occurs by chance in almost any
# text, and redacting it would destroy the output. Value matching is therefore
# limited to candidates of at least four characters. Short credentials are covered
# by the structural guarantees instead: the value is never interpolated into a
# message, error, evidence field or log line, and the command patterns in
# app.security.redaction redact the whole remainder of a /mt4 command regardless of
# its shape. Shortening this bound is not a safe option.
_MIN_SUBSTRING_SECRET_LENGTH = 4


def contains_secret(text: str, candidates: Iterable[str]) -> bool:
    return any(
        len(candidate) >= _MIN_SUBSTRING_SECRET_LENGTH and candidate in text
        for candidate in candidates
    )


def sanitize_text(text: str, candidates: Iterable[str]) -> str:
    return redact_text(
        text,
        tuple(
            candidate
            for candidate in candidates
            if len(candidate) >= _MIN_SUBSTRING_SECRET_LENGTH
        ),
    )


def _result(
    identifier: str,
    status: TestStatus,
    message: str,
    detail: str = "",
    *,
    severity: TestSeverity = TestSeverity.MEDIUM,
) -> TestResult:
    return TestResult(
        id=identifier,
        category=TestCategory.SECURITY,
        name="Secret leakage scan",
        status=status,
        severity=severity,
        test_class=TestClass.SAFE,
        message=message,
        technical_detail=detail,
        evidence={"secret_leak_detected": status == TestStatus.FAIL},
    )


def scan_text(
    label: str,
    text: str,
    candidates: Iterable[str],
    *,
    identifier: str | None = None,
) -> TestResult:
    leak = contains_secret(text, candidates)
    name = identifier or f"SEC_{label.upper().replace(' ', '_')}"
    return _result(
        name,
        TestStatus.FAIL if leak else TestStatus.PASS,
        f"{label} contains no tracked secret."
        if not leak
        else f"{label} contains a tracked secret.",
        "Only a boolean leak flag is stored; secret values are never recorded.",
        severity=TestSeverity.CRITICAL if leak else TestSeverity.LOW,
    )


def scan_paths(
    paths: AppPaths,
    candidates: Iterable[str],
    *,
    extra_paths: Iterable[Path] = (),
) -> list[TestResult]:
    results: list[TestResult] = []
    targets: list[tuple[str, Path]] = [
        ("Agent log", paths.log_dir / "agent.log"),
        ("History", paths.history_file),
    ]
    targets.extend(("Diagnostic", path) for path in extra_paths)
    for label, path in targets:
        try:
            text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        except OSError as exc:
            results.append(
                _result(
                    f"SEC_{label.upper().replace(' ', '_')}",
                    TestStatus.WARN,
                    f"{label} could not be read for the leak scan.",
                    type(exc).__name__,
                    severity=TestSeverity.MEDIUM,
                )
            )
            continue
        results.append(
            scan_text(label, text, candidates, identifier=f"SEC_{label.upper().replace(' ', '_')}")
        )
    return results


def sanitize_payload(value: Any, candidates: Iterable[str]) -> Any:
    """Recursively redact strings in a JSON-compatible payload."""
    candidates = tuple(candidate for candidate in candidates if len(candidate) >= 4)
    if isinstance(value, str):
        return sanitize_text(value, candidates)
    if isinstance(value, list):
        return [sanitize_payload(item, candidates) for item in value]
    if isinstance(value, tuple):
        return [sanitize_payload(item, candidates) for item in value]
    if isinstance(value, dict):
        return {str(key): sanitize_payload(item, candidates) for key, item in value.items()}
    return value


def safe_report_excerpt(text: str, candidates: Iterable[str], limit: int = 400) -> str:
    return sanitize_text(text, candidates)[:limit]


__all__ = [
    "contains_secret",
    "safe_report_excerpt",
    "sanitize_payload",
    "sanitize_text",
    "scan_paths",
    "scan_text",
    "secret_candidates",
]
