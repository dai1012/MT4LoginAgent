"""Windows acceptance test result models.

The test runner deliberately uses one small, serializable result model everywhere.
No raw credentials, OTPs, or provider exception text are stored in these models.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TestStatus(StrEnum):
    __test__ = False
    PASS = "PASS"
    FAIL = "FAIL"
    WARN = "WARN"
    SKIP = "SKIP"
    MANUAL = "MANUAL"
    NOT_RUN = "NOT_RUN"


class TestSeverity(StrEnum):
    __test__ = False
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class TestCategory(StrEnum):
    __test__ = False
    ENVIRONMENT = "environment"
    SECURITY = "security"
    SLACK = "slack"
    MT4_DISCOVERY = "mt4_discovery"
    UIA = "uia"
    REAL_LOGIN = "real_login"
    GROUP = "group"
    REPORT = "report"


class TestClass(StrEnum):
    __test__ = False
    SAFE = "SAFE"
    REAL_LOGIN = "REAL_LOGIN"
    MANUAL = "MANUAL"
    DESTRUCTIVE = "DESTRUCTIVE"


class TestPhase(StrEnum):
    __test__ = False
    ENVIRONMENT = "environment"
    DISCOVERY = "discovery"
    SLACK = "slack"
    REAL_LOGIN = "real_login"
    GROUP = "group"


class TestResult(BaseModel):
    """One safe, serializable acceptance-test observation."""

    __test__ = False
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(min_length=1, max_length=120)
    category: TestCategory
    name: str = Field(min_length=1, max_length=200)
    status: TestStatus
    severity: TestSeverity = TestSeverity.INFO
    test_class: TestClass = TestClass.SAFE
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    duration_ms: int = Field(default=0, ge=0)
    message: str = Field(default="", max_length=1000)
    technical_detail: str = Field(default="", max_length=4000)
    suggested_action: str = Field(default="", max_length=1000)
    requires_manual: bool = False
    evidence: dict[str, Any] = Field(default_factory=dict)

    def safe_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class DetectedControl(BaseModel):
    """Sanitized UIA candidate metadata; never contains a control value."""

    model_config = ConfigDict(extra="forbid")

    field: str = Field(min_length=1, max_length=40)
    automation_id: str = Field(default="", max_length=512)
    name: str = Field(default="", max_length=512)
    control_type: str = Field(default="", max_length=80)
    class_name: str = Field(default="", max_length=256)
    is_password: bool = False
    patterns: list[str] = Field(default_factory=list, max_length=20)
    confidence: str = Field(default="NEEDS_CONFIRMATION", max_length=40)


class TestReport(BaseModel):
    __test__ = False
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "windows-acceptance-report/v1"
    report_id: str
    started_at: datetime
    finished_at: datetime
    platform: str
    windows_version: str = ""
    python_version: str
    python_architecture: str
    agent_version: str
    git_commit: str = ""
    overall: str = "PARTIAL"
    results: list[TestResult] = Field(default_factory=list, max_length=500)
    detected_controls: list[DetectedControl] = Field(default_factory=list, max_length=500)
    security_scan: dict[str, bool] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list, max_length=100)

    def counts(self) -> dict[str, int]:
        counts = {status.value: 0 for status in TestStatus}
        for result in self.results:
            counts[result.status.value] += 1
        return counts

    def recompute_overall(self) -> str:
        counts = self.counts()
        if counts[TestStatus.FAIL.value]:
            return "FAIL"
        if (
            counts[TestStatus.WARN.value]
            or counts[TestStatus.MANUAL.value]
            or counts[TestStatus.NOT_RUN.value]
        ):
            return "PARTIAL"
        if self.results:
            return "PASS"
        return "NOT_RUN"

    def safe_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def now_utc() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "DetectedControl",
    "TestCategory",
    "TestClass",
    "TestPhase",
    "TestReport",
    "TestResult",
    "TestSeverity",
    "TestStatus",
    "now_utc",
]
