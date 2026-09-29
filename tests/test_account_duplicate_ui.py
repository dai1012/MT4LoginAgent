from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _script(client) -> str:
    response = client.get("/static/app.js")
    assert response.status_code == 200
    return response.text


def _duplicate_body(script: str) -> str:
    start = script.index("function duplicateAccountForm")
    end = script.index("function parseJson")
    return script[start:end]


def test_duplicate_button_sits_with_existing_row_actions(client):
    script = _script(client)
    assert "function renderAccounts" in script
    assert 'data-action="duplicate-account"' in script
    assert ">复制</button>" in script
    # Existing row actions must survive untouched.
    for action in ("edit-account", "validate-account", "toggle-account", "delete-account"):
        assert f'data-action="{action}"' in script
    assert "duplicateAccountForm(state.accounts.find((a) => a.id === button.dataset.id));" in script


def test_duplicate_prefill_copies_broker_fields_and_clears_identity(client):
    body = _duplicate_body(_script(client))
    # Copied from the source account.
    for fragment in (
        "$(\"#account-server\").value = source.server",
        "$(\"#account-process-name\").value = source.process_name",
        "$(\"#account-window-regex\").value = source.window_title_regex",
        "JSON.stringify(source.launch_arguments || [])",
        "JSON.stringify(source.control_ids || {}",
        "JSON.stringify(source.control_titles || {}",
        "$(\"#account-save-login\").checked = !!source.save_login_info",
        "loadWin32Fallback(source.win32_fallback",
    ):
        assert fragment in body, fragment
    # Whole win32_fallback object goes through one loader (all six control ids).
    assert "JSON.parse(JSON.stringify(source.win32_fallback))" in body
    # Identity fields must not carry over.
    assert "$(\"#account-alias\").value = \"\";" in body
    assert "$(\"#account-login-id\").value = \"\";" in body
    assert "副本" in body  # display name is suffixed so the copy is obvious
    assert "$(\"#account-enabled\").checked = false;" in body
    # Paths are pre-filled to save typing, not cleared.
    assert "$(\"#account-terminal-path\").value = source.terminal_path" in body
    assert "$(\"#account-profile-path\").value = source.profile_path" in body
    # Duplicating mode is visible in the heading.
    assert "`复制 ${source.alias} 为新 Account`" in body


def test_duplicate_only_creates_and_never_touches_the_source(client):
    body = _duplicate_body(_script(client))
    assert "$(\"#account-id\").value = \"\";" in body
    script = _script(client)
    # saveAccount still decides create-vs-update on the hidden id field, so an
    # emptied id can only POST a new account, never PUT the source.
    assert "const id = $(\"#account-id\").value; if (id)" in script
    assert 'await api("/accounts", { method: "POST"' in script
    # Leaving the editor (new/cancel/edit paths) hides the duplicate warning again.
    assert "$(\"#account-duplicate-warning\").classList.add(\"hidden\")" in script
    assert "$(\"#account-duplicate-warning\").classList.remove(\"hidden\")" in body


def test_success_regex_with_login_id_is_scrubbed(client):
    body = _duplicate_body(_script(client))
    assert "function successRegexContainsLoginId" in _script(client)
    assert "String(regex ?? \"\").includes(id)" in _script(client)
    assert "successRegexContainsLoginId(source.success_window_title_regex, source.login_id)" in body
    assert "$(\"#account-success-window-regex\").value = \"\";" in body
    assert "不要写 Login ID" in body  # inline hint: use a stable broker/server pattern
    # Without the login id inside, the regex is copied as-is and no hint shows.
    expected = (
        '$("#account-success-window-regex").value = '
        'source.success_window_title_regex || "";'
    )
    assert expected in body
    page = client.get("/")
    assert page.status_code == 200
    assert 'id="account-success-regex-hint"' in page.text


def test_command_preview_shows_alias_only_and_prompts_when_empty(client):
    page = client.get("/")
    assert page.status_code == 200
    assert 'id="account-command-preview"' in page.text
    assert '<small class="muted" id="account-command-preview">' in page.text  # read-only line
    assert "凭据只填密码 / 一次性 OTP，不能包含 Login ID" in page.text
    script = _script(client)
    assert "function updateCommandPreview" in script
    assert "/mt4 ${esc(alias)} &lt;" in script  # literal `/mt4 <alias> <credential>` shape
    assert "先填写 Alias 再保存" in script  # empty-alias prompt
    assert "$(\"#account-alias\").addEventListener(\"input\", updateCommandPreview);" in script
    # The preview may only ever read the alias field: no login id, no password.
    start = script.index("function updateCommandPreview")
    end = script.index("function duplicateAccountForm")
    preview_body = script[start:end]
    assert "account-login-id" not in preview_body
    assert "login_id" not in preview_body
    assert "password" not in preview_body.lower()


def test_multi_instance_warning_uses_existing_styles(client):
    page = client.get("/")
    assert page.status_code == 200
    assert 'id="account-duplicate-warning" class="warning-box hidden"' in page.text
    assert "独立的 terminal 安装目录和独立的 working directory" in page.text
    assert "改好这两个路径后再启用新账号" in page.text


def test_docs_state_alias_target_credential_rule_and_duplicate(client):
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    guide = (REPO_ROOT / "docs" / "WINDOWS-TEST-GUIDE.md").read_text(encoding="utf-8")
    for doc in (readme, guide):
        assert "alias" in doc
        assert "绝不能包含 Login ID" in doc or "不能包含 Login ID" in doc
        assert "复制" in doc
        assert "独立的 terminal 安装目录" in doc
        assert "独立的 working directory" in doc
