from __future__ import annotations


def test_windows_test_api_exposes_safe_phase_and_manual_guards(client, account_payload):
    status = client.get("/api/test/windows")
    assert status.status_code == 200
    assert status.json()["windows_available"] is False
    phase = client.post("/api/test/windows/phase", json={"phase": "environment"})
    assert phase.status_code == 200
    assert any(item["status"] == "NOT_RUN" for item in phase.json()["results"])
    invalid = client.post("/api/test/windows/phase", json={"phase": "not-a-phase"})
    assert invalid.status_code == 422
    account = client.post("/api/accounts", json=account_payload).json()
    real = client.post(
        "/api/test/windows/real-login",
        json={
            "account_id": account["id"],
            "otp": "123456",
            "confirmed": False,
            "broker_confirmed": True,
        },
    )
    assert real.status_code == 200
    assert "123456" not in real.text
    assert real.json()["results"][0]["status"] in {"MANUAL", "NOT_RUN"}
    missing_broker = client.post(
        "/api/test/windows/real-login",
        json={"account_id": account["id"], "otp": "123456", "confirmed": True},
    )
    assert missing_broker.status_code == 422
    missing = client.post(
        "/api/test/windows/phase",
        json={"phase": "discovery", "account_id": "does-not-exist"},
    )
    assert missing.status_code == 404


def test_windows_test_report_endpoints_are_protected_and_bounded(client, account_payload):
    assert client.get("/api/test/windows/report/report.html").status_code == 404
    assert client.get("/api/test/windows/report").status_code == 200
    account = client.post("/api/accounts", json=account_payload).json()
    discovery = client.post(
        "/api/test/windows/phase",
        json={"phase": "discovery", "account_id": account["id"]},
    )
    assert discovery.status_code == 200
    assert discovery.json()["results"][0]["status"] == "NOT_RUN"
    assert client.get("/api/test/windows/report/report.html").status_code == 200
    assert client.get("/api/test/windows/report/../secrets.json").status_code in {404, 400}
    reports = client.get("/api/test/windows/report").json()
    assert reports
    report_id = reports[0]["report_id"]
    assert client.post(f"/api/test/windows/report/{report_id}/select").status_code == 200
    assert client.get("/api/test/windows/report/report.html").status_code == 200
    html = client.get("/api/test/windows/report/report.html")
    assert "Windows Acceptance Test" in html.text
    payload = client.get("/api/test/windows/report/report.json")
    assert payload.status_code == 200
    assert payload.json()["schema_version"] == "windows-acceptance-report/v1"
