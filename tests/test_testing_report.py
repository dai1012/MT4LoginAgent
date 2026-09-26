from __future__ import annotations

import json

from app.config.paths import AppPaths
from app.testing.models import TestCategory, TestReport, TestResult, TestStatus
from app.testing.report import new_report_id, read_report_json, write_report


def test_report_writes_sanitized_json_html_uia_and_log(tmp_path):
    paths = AppPaths(tmp_path / "data")
    paths.ensure()
    paths.log_dir.joinpath("agent.log").write_text(
        "OTP 123456 and xoxb-secret-value", encoding="utf-8"
    )
    result = TestResult(
        id="SEC_SCAN",
        category=TestCategory.SECURITY,
        name="Secret scan",
        status=TestStatus.PASS,
        message="OTP 123456 was not stored",
        technical_detail="xoxb-secret-value",
    )
    report = TestReport(
        report_id=new_report_id(),
        started_at=result.started_at,
        finished_at=result.started_at,
        platform="macOS",
        python_version="3.11",
        python_architecture="arm64",
        agent_version="1.0.0",
        results=[result],
        security_scan={"otp_leakage_scan": True},
    )
    html_path = write_report(
        paths,
        report,
        ("123456", "xoxb-secret-value"),
        uia_payload={"window": {"title": "OTP 123456"}},
    )
    directory = html_path.parent
    assert (directory / "report.json").is_file()
    assert (directory / "report.html").is_file()
    assert (directory / "uia-tree-sanitized.json").is_file()
    assert (directory / "logs-sanitized.txt").is_file()
    all_text = "\n".join(
        path.read_text(encoding="utf-8") for path in directory.iterdir() if path.is_file()
    )
    assert "123456" not in all_text
    assert "xoxb-secret-value" not in all_text
    payload = read_report_json(directory)
    assert payload["schema_version"] == "windows-acceptance-report/v1"
    assert payload["security_scan"] == {"otp_leakage_scan": True}
    assert payload["results"][0]["status"] == "PASS"
    assert json.dumps(payload)
