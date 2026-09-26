"""Sanitized JSON and HTML report generation for the Windows acceptance runner."""

from __future__ import annotations

import html
import json
import os
import secrets as secrets_module
import tempfile
from collections.abc import Iterable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config.paths import AppPaths
from app.testing.models import TestCategory, TestReport
from app.testing.security import sanitize_payload, sanitize_text

_CATEGORY_TITLES = {
    TestCategory.ENVIRONMENT: "Environment Tests",
    TestCategory.SECURITY: "Security Tests",
    TestCategory.SLACK: "Slack Tests",
    TestCategory.MT4_DISCOVERY: "MT4 Discovery",
    TestCategory.UIA: "UIA Controls",
    TestCategory.REAL_LOGIN: "Real Login",
    TestCategory.GROUP: "Group Login",
    TestCategory.REPORT: "Report",
}


def new_report_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S-") + secrets_module.token_hex(2)


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        with suppress(OSError):
            os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except Exception:
        with suppress(OSError):
            Path(temporary).unlink(missing_ok=True)
        raise


def render_html(report: TestReport, candidates: Iterable[str]) -> str:
    counts = report.counts()
    safe = sanitize_payload(report.safe_dict(), candidates)
    # The safe payload is only used for text fields that are rendered below.
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in safe["results"]:
        groups.setdefault(item["category"], []).append(item)
    sections = []
    for category, title in _CATEGORY_TITLES.items():
        items = groups.get(category.value, [])
        if not items:
            continue
        rows = []
        for item in items:
            rows.append(
                "<tr>"
                f"<td><code>{html.escape(item['id'])}</code></td>"
                f"<td>{html.escape(item['name'])}</td>"
                f'<td class="{html.escape(str(item["status"]).lower())}">'
                f"{html.escape(str(item['status']))}</td>"
                f"<td>{html.escape(item.get('message', ''))}</td>"
                f"<td>{html.escape(item.get('technical_detail', ''))}</td>"
                f"<td>{html.escape(item.get('suggested_action', ''))}</td>"
                "</tr>"
            )
        sections.append(
            f"<section><h2>{html.escape(title)}</h2>"
            "<table><thead><tr><th>ID</th><th>Test</th><th>Status</th>"
            "<th>Observed</th><th>Detail</th><th>Next action</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table></section>"
        )
    controls = safe.get("detected_controls", [])
    control_text = "<p>No UIA controls discovered.</p>"
    if controls:
        control_text = (
            "<pre>" + html.escape(json.dumps(controls, ensure_ascii=False, indent=2)) + "</pre>"
        )
    environment = json.dumps(
        {
            key: safe[key]
            for key in (
                "windows_version",
                "python_version",
                "python_architecture",
                "agent_version",
                "git_commit",
                "started_at",
                "finished_at",
            )
        },
        ensure_ascii=False,
        indent=2,
    )
    security = json.dumps(safe.get("security_scan", {}), ensure_ascii=False, indent=2)
    failure_items = [item for item in safe["results"] if item.get("status") == "FAIL"]
    if failure_items:
        failure_rows = "".join(
            "<tr>"
            f"<td><code>{html.escape(item['id'])}</code></td>"
            f"<td>{html.escape(item.get('name', ''))}</td>"
            f"<td>{html.escape(item.get('technical_detail', ''))}</td>"
            f"<td>{html.escape(item.get('message', ''))}</td>"
            f"<td>{html.escape(item.get('suggested_action', ''))}</td>"
            "</tr>"
            for item in failure_items
        )
        failures = (
            "<table><thead><tr><th>Test</th><th>Name</th><th>Expected/Detail</th>"
            f"<th>Observed</th><th>Next action</th></tr></thead>"
            f"<tbody>{failure_rows}</tbody></table>"
        )
    else:
        failures = "<p>No FAIL results.</p>"
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>Windows Acceptance Test Report</title>
<style>
body{{font-family:system-ui,sans-serif;background:#0b1020;color:#e8eefc;margin:32px;}}
h1,h2{{color:#7dd3fc}}table{{border-collapse:collapse;width:100%;margin:12px 0 28px;}}
th,td{{border:1px solid #334155;padding:7px;text-align:left;vertical-align:top;font-size:14px;}}
th{{background:#16223a}}code,pre{{white-space:pre-wrap;word-break:break-word}}
.pass{{color:#6ee7b7}}.fail{{color:#fda4af}}.warn{{color:#fcd34d}}.manual{{color:#c4b5fd}}.not_run{{color:#94a3b8}}
.cards{{display:flex;gap:12px;flex-wrap:wrap}}
.card{{background:#16223a;padding:12px 18px;border-radius:8px;}}
</style></head><body>
<h1>Windows Acceptance Test</h1>
<div class="cards">
<div class="card"><b>Overall</b><br>{html.escape(safe["overall"])}</div>
<div class="card"><b>Report</b><br>{html.escape(safe["report_id"])}</div>
<div class="card"><b>Platform</b><br>{html.escape(safe["platform"])}</div>
<div class="card"><b>Agent</b><br>{html.escape(safe["agent_version"])}</div>
</div>
<h2>Summary</h2><pre>{html.escape(json.dumps(counts, ensure_ascii=False, indent=2))}</pre>
<h2>Environment</h2>
<pre>{html.escape(environment)}</pre>
{"".join(sections)}
<h2>UIA Controls</h2>{control_text}
<h2>Failures</h2>{failures}
<h2>Failures and Manual Actions</h2>
<p>No OTP, Slack token, local admin token, HMAC key, or raw provider value is
stored in this report.</p>
<pre>{html.escape(security)}</pre>
</body></html>"""


def write_report(
    paths: AppPaths,
    report: TestReport,
    candidates: Iterable[str],
    *,
    uia_payload: dict[str, Any] | None = None,
) -> Path:
    directory = paths.reports_dir / report.report_id
    directory.mkdir(parents=True, exist_ok=True)
    with suppress(OSError):
        os.chmod(directory, 0o700)
    safe_dict = sanitize_payload(report.safe_dict(), candidates)
    json_text = json.dumps(safe_dict, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    _atomic_write(directory / "report.json", json_text)
    _atomic_write(directory / "report.html", render_html(report, candidates))
    if uia_payload is not None:
        safe_uia = sanitize_payload(uia_payload, candidates)
        _atomic_write(
            directory / "uia-tree-sanitized.json",
            json.dumps(safe_uia, ensure_ascii=False, indent=2) + "\n",
        )
    log_path = paths.log_dir / "agent.log"
    if log_path.exists():
        try:
            log_text = sanitize_text(
                log_path.read_text(encoding="utf-8", errors="replace"), candidates
            )
        except OSError:
            log_text = "Log could not be read for the sanitized report copy.\n"
    else:
        log_text = "No agent log was present at report time.\n"
    _atomic_write(directory / "logs-sanitized.txt", log_text)
    return directory / "report.html"


def read_report_json(directory: Path) -> dict[str, Any]:
    return json.loads((directory / "report.json").read_text(encoding="utf-8"))


__all__ = ["new_report_id", "read_report_json", "render_html", "write_report"]
