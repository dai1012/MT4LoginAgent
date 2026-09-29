from __future__ import annotations


def _page(client) -> str:
    response = client.get("/")
    assert response.status_code == 200
    return response.text


def _script(client) -> str:
    response = client.get("/static/app.js")
    assert response.status_code == 200
    return response.text


def _win32_section(script: str) -> str:
    start = script.index("WIN32_REQUIRED_KEYS")
    end = script.index("async function viewReport")
    return script[start:end]


def test_detect_win32_button_sits_next_to_detect_and_states_read_only(client):
    page = _page(client)
    assert 'data-action="run-test-discovery"' in page
    assert ">Detect</button>" in page
    assert 'data-action="run-win32-inspect"' in page
    assert ">Detect Win32</button>" in page
    assert 'id="win32-inspect-btn"' in page
    before = page.split('id="win32-inspect-btn"')[0][-400:]
    assert "disabled" in before or 'id="win32-inspect-btn" disabled' in page
    assert 'id="win32-inspector"' in page
    assert "Detect Win32 only reads the login dialog and changes nothing by itself." in page
    assert "Nothing is saved until a human applies." in page
    assert "The inspection never reads the credential field." in page
    # Existing Detect behaviour must survive untouched.
    assert 'id="test-account"' in page
    assert 'id="test-controls"' in page


def test_win32_inspect_sends_account_id_only(client):
    script = _script(client)
    assert "async function runWin32Inspect" in script
    assert '"/test/windows/win32-inspect"' in script
    assert "JSON.stringify({ account_id: accountId })" in script
    start = script.index("async function runWin32Inspect")
    end = script.index("async function applyWin32Settings")
    body = script[start:end]
    assert "test-otp" not in body
    assert "account-login-id" not in body
    assert "account-terminal-path" not in body
    assert "password" not in body.lower()
    assert "secret" not in body.lower()
    # Only human-facing copy may mention a login id; no value may be read.
    assert "login_id" not in body or "login id" in body.lower()


def test_win32_table_shows_eight_fields_with_confidence_markers(client):
    script = _script(client)
    for key in (
        "dialog_class",
        "anchors",
        "login_id_combo",
        "login_id_edit",
        "otp",
        "server_combo",
        "server_edit",
        "login_button",
    ):
        assert f'"{key}"' in script, key
    assert "function renderWin32Inspector" in script
    assert "<th>Field</th><th>Suggested value</th><th>Confidence</th>" in script
    assert "HIGH" in script
    assert "NEEDS_CONFIRMATION" in script
    assert "value is not confirmed" in script


def test_win32_appliable_false_lists_missing_and_offers_no_apply(client):
    script = _script(client)
    assert "Not appliable:" in script
    assert "report.missing" in script or "const missing = report.missing" in script
    assert "No Apply button is offered." in script
    # Apply button exists only behind the appliable gate.
    assert "const applyBtn = appliable ?" in script
    assert 'data-action="apply-win32-settings"' in script
    assert ">Apply detected Win32 settings</button>" in script
    # Refusing to apply without a fresh appliable suggestion.
    assert "state.win32.appliable" in script
    assert "state.win32AccountId !== accountId" in script


def test_win32_titles_are_copy_suggestions_and_captions_are_read_only(client):
    script = _script(client)
    section = _win32_section(script)
    assert "Suggested titles (copy into the Account form, NOT auto-applied)" in section
    assert (
        "These titles are suggestions to copy into the Account form, NOT auto-applied."
        in section
    )
    assert "window_title_regex" in section
    assert "success_window_title_regex" in section
    assert "menu_captions" in section
    assert "Menu captions (read-only reference)" in section
    assert "These are this build's own menu captions" in section
    assert "a different broker's wording will differ" in section
    assert "does not auto-extend its caption list" in section


def test_win32_apply_sends_reviewed_suggestion_and_reports_narrow_write(client):
    script = _script(client)
    assert "async function applyWin32Settings" in script
    assert '"/test/windows/win32-apply"' in script
    assert "suggested: state.win32.suggested || {}" in script
    assert "confidence: state.win32.confidence || {}" in script
    assert "JSON.stringify({ account_id: accountId, suggested:" in script
    section = _win32_section(script)
    assert "Only the Win32 fallback fields were written." in section
    assert "were NOT touched" in section
    for untouched in ("Alias", "login id", "server", "terminal path", "working directory"):
        assert untouched in section, untouched
    assert "Please refresh the Account form" in section


def test_win32_shows_outcome_and_detail_verbatim_when_not_inspected(client):
    script = _script(client)
    assert 'if (outcome !== "inspected")' in script
    section = _win32_section(script)
    assert "${esc(outcome)}" in section
    assert "${esc(detail)}" in section


def test_win32_section_never_touches_credentials_or_identity_values(client):
    script = _script(client)
    section = _win32_section(script)
    for banned in (
        "test-otp",
        "account-login-id",
        "account-terminal-path",
        "SecretStr",
        "get_secret_value",
    ):
        assert banned not in section, banned
    assert "never reads the credential field" in section.lower()
