from __future__ import annotations


def test_windows_test_page_keeps_step_wizard_and_manual_guards(client):
    page = client.get("/")
    assert page.status_code == 200
    assert 'id="test-steps"' in page.text
    assert 'id="test-next-hint"' in page.text

    script = client.get("/static/app.js")
    assert script.status_code == 200
    text = script.text
    for step in ("environment", "discovery", "slack", "real_login", "group"):
        assert f'key: "{step}"' in text

    # The wizard may only gate on prerequisites; it must never drop a manual guard.
    for guard in (
        'if (!$("#test-broker-confirm").checked)',
        'if (!$("#test-real-confirm").checked)',
        'if (!$("#test-group-confirm").checked)',
    ):
        assert guard in text
    assert 'if (!requireSteps("real_login"' in text
    assert 'if (!requireSteps("group"' in text
    assert "WINDOWS_REAL_TEST_REQUIRED" in text


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


def test_account_form_exposes_an_opt_in_win32_fallback_section(client):
    page = client.get("/")
    assert page.status_code == 200
    body = page.text
    for element_id in (
        "account-win32-enabled",
        "account-win32-class",
        "account-win32-anchors",
        "account-win32-id-login-id-combo",
        "account-win32-id-login-id-edit",
        "account-win32-id-otp",
        "account-win32-id-server-combo",
        "account-win32-id-server-edit",
        "account-win32-id-login-button",
    ):
        assert f'id="{element_id}"' in body, element_id
    script = client.get("/static/app.js").text
    assert "collectWin32Fallback" in script
    assert "loadWin32Fallback" in script
    assert "win32_fallback: collectWin32Fallback()" in script
    # The field is opt-in and must never be pre-selected.
    assert "$(\"#account-win32-enabled\").checked = false" in script


def test_profile_path_help_no_longer_claims_it_is_the_mt4_data_folder(client):
    body = client.get("/").text
    assert "MT4 process working directory (cwd)" in body
    assert "NOT the MT4 Data Folder" in body
    assert "Open Data Folder</textarea>" not in body


def test_step_state_uses_real_phase_status_and_the_discovery_route(client):
    script = client.get("/static/app.js").text
    assert 'data.phase_status' in script
    assert "data.discovery_route" in script
    assert "未解析出可用路径" in script
    # Completion alone must not imply PASS.
    assert 'step.done ? badge("PASS")' not in script
    # The step bar must state which route Real Login will take.
    assert "Win32 dialog fallback（UIA Automation ID 不需要）" in script


def test_apply_selectors_button_is_conditional(client):
    script = client.get("/static/app.js").text
    assert "const canApply = allHigh && route === \"uia\";" in script
    assert 'canApply ? `<button class="button secondary"' in script
    assert 'data-action="apply-test-selectors"' in script
    assert "不需要应用 UIA Automation ID" in script


def test_manual_confirmations_and_backend_gates_survive_the_hardening(client):
    script = client.get("/static/app.js").text
    for guard in (
        'if (!$("#test-broker-confirm").checked)',
        'if (!$("#test-real-confirm").checked)',
        'if (!$("#test-group-confirm").checked)',
        'if (!requireSteps("real_login"',
        'if (!requireSteps("group"',
    ):
        assert guard in script, guard
    assert "if (!requireSteps(\"real_login\", `) " not in script
    # The wizard must never auto-send an OTP.
    assert '$("#test-otp").value' in script
    # The runner never sends an OTP on its own; the user types it and confirms.
    assert 'type: "text"' not in script
    assert 'id="test-otp" type="password"' in client.get("/").text


def test_history_is_refreshed_after_actions_that_append_to_it(client):
    script = client.get("/static/app.js").text
    assert "async function loadHistory()" in script
    for toast_text in (
        "Real login test 已完成，结果见报告",
        "Full Slack E2E 结果已记录",
        "Group test 已完成，结果见报告",
    ):
        marker = f'toast("{toast_text}"); await loadHistory();'
        assert marker in script, toast_text


def test_history_tab_activation_refetches_on_demand(client):
    script = client.get("/static/app.js").text
    assert 'if (tab.dataset.tab === "history") loadHistory();' in script
    assert 'if (link.dataset.tabLink === "history") loadHistory();' in script
    assert 'else if (action === "refresh-history") { await loadHistory(); }' in script


def test_history_refresh_is_on_demand_and_not_polled(client):
    script = client.get("/static/app.js").text
    assert "function loadHistory" in script
    # The page already had a visibility-based dashboard refresh. History must stay
    # on-demand: no interval or timer may reference the loader.
    for banned in ("setHistoryInterval", "historyPoll", "setInterval", "setTimeout"):
        for line in script.splitlines():
            if banned in line:
                assert "loadHistory" not in line, f"{banned} polls history: {line}"
    assert script.count("function loadHistory") == 1


def test_initial_page_load_uses_the_shared_history_loader(client):
    script = client.get("/static/app.js").text
    assert "await loadHistory(); } catch" in script
    assert 'renderHistory(await api("/history?limit=500"), "#history-table");' in script
