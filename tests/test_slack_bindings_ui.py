from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent


def _script(client) -> str:
    response = client.get("/static/app.js")
    assert response.status_code == 200
    return response.text


def _bindings_body(script: str) -> str:
    start = script.index("function currentBindingSelection")
    end = script.index("async function loadSlack")
    return script[start:end]


def test_bindings_editor_markup_and_copy(client):
    page = client.get("/")
    assert page.status_code == 200
    assert 'id="slack-bindings"' in page.text
    # Editor lives inside the Slack settings form.
    form_start = page.text.index('id="slack-form"')
    form_end = page.text.index("</form>", form_start)
    assert 'id="slack-bindings"' in page.text[form_start:form_end]
    # Explanatory copy: allowed decides who, bindings decide which aliases,
    # and the empty state is fail-closed.
    for fragment in (
        "Allowed 决定谁能用 Agent",
        "Account 绑定决定这个人能操作哪些 alias",
        "no accounts assigned",
        "fail-closed，不是全部账号",
    ):
        assert fragment in page.text, fragment


def test_bindings_render_per_user_checkboxes_with_empty_state(client):
    body = _bindings_body(_script(client))
    assert "function renderSlackBindings" in body
    assert "function collectSlackBindings" in body
    # One row per allowed User ID, aliases as checkboxes in the page's own style.
    assert 'data-bind-user="${esc(u)}"' in body
    assert '<label class="checkbox"><input type="checkbox" data-bind-user=' in body
    # Aliases come from the loaded catalog, never typed by hand.
    assert "state.accounts.map((a) => a.alias)" in body
    # Zero selected aliases is visibly marked.
    assert "no accounts assigned — 该用户不能操作任何账号" in body
    # Orphan aliases from the server are shown, not silently dropped.
    assert "未知 alias，保存会被后端拒绝" in body
    # Typing a new User ID re-renders rows without losing checkbox state.
    script = _script(client)
    assert (
        '$("#slack-users").addEventListener("input", '
        "() => renderSlackBindings(state.slackBindings));"
    ) in script


def test_submit_sends_whole_map_including_empty_lists(client):
    body = _bindings_body(_script(client))
    # Every allowed user gets a key, even with nothing assigned: keys are
    # pre-seeded empty and only appended to, never omitted.
    assert "users.forEach((u) => { map[u] = []; });" in body
    script = _script(client)
    assert "slack_user_account_bindings: collectSlackBindings()" in script
    # The map travels both directions on the existing Slack endpoints.
    assert "state.slackBindings = s.slack_user_account_bindings || {};" in script
    assert 'await api("/slack", { method: "PUT"' in script


def test_backend_validation_errors_use_the_existing_error_display(client):
    script = _script(client)
    save_start = script.index("async function saveSlack")
    save_end = script.index("async function handleAction")
    save_body = script[save_start:save_end]
    assert "slack_user_account_bindings: collectSlackBindings()" in save_body
    # Backend rejection (e.g. an orphan alias) surfaces through the same
    # error toast as every other save failure; nothing swallows it.
    assert "} catch (error) { toast(error.message, true); }" in save_body


def test_bindings_show_no_secrets(client):
    body = _bindings_body(_script(client))
    for banned in ("login_id", "login-id", "terminal_path", "password", "token", "secret"):
        assert banned not in body.lower(), banned
    # Only aliases are rendered, escaped like the rest of the page.
    assert "<code>${esc(al)}</code>" in body


def test_docs_carry_the_two_user_worked_example(client):
    del client  # docs are read from disk, not served
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    guide = (REPO_ROOT / "docs" / "WINDOWS-TEST-GUIDE.md").read_text(encoding="utf-8")
    for doc in (readme, guide):
        assert "U_A -> [A]" in doc
        assert "U_B -> [B]" in doc
        assert "成员目录" in doc
        assert "保持独立" in doc


def test_manifest_usage_hint_names_credential(client):
    del client  # manifest is read from disk, not served
    manifest = yaml.safe_load(
        (REPO_ROOT / "slack-app-manifest.yaml").read_text(encoding="utf-8")
    )
    [command] = manifest["features"]["slash_commands"]
    assert command["command"] == "/mt4"
    assert command["usage_hint"] == "/mt4 <alias-or-group> <credential> | /mt4 status"
