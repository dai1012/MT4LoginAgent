from __future__ import annotations

from app.testing.models import (
    TestCategory,
    TestReport,
    TestResult,
    TestSeverity,
    TestStatus,
)


def test_test_result_and_report_serialization_and_overall():
    results = [
        TestResult(
            id="ENV_ONE",
            category=TestCategory.ENVIRONMENT,
            name="Environment",
            status=TestStatus.PASS,
            severity=TestSeverity.LOW,
        ),
        TestResult(
            id="ENV_TWO",
            category=TestCategory.ENVIRONMENT,
            name="Warning",
            status=TestStatus.WARN,
            severity=TestSeverity.MEDIUM,
        ),
    ]
    report = TestReport(
        report_id="test-report",
        started_at=results[0].started_at,
        finished_at=results[0].started_at,
        platform="macOS",
        python_version="3.11",
        python_architecture="arm64",
        agent_version="1.0.0",
        results=results,
    )
    assert report.counts()[TestStatus.PASS.value] == 1
    assert report.recompute_overall() == "PARTIAL"
    payload = report.safe_dict()
    assert payload["results"][0]["status"] == "PASS"
    assert "test-report" in payload["report_id"]
