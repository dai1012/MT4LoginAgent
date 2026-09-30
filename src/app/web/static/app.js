/* Local Web Admin: no external assets, analytics, or token storage. */
(() => {
  "use strict";
  const state = { accounts: [], groups: [], runtime: null, slackSession: "", testStatus: null, slackBindings: {}, win32: null, win32AccountId: "" };
  const ADMIN_TOKEN_KEY = "mt4-admin-token";
  let adminToken = "";
  try { adminToken = sessionStorage.getItem(ADMIN_TOKEN_KEY) || ""; } catch (_) { adminToken = ""; }
  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => Array.from(document.querySelectorAll(selector));
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));
  const badge = (value) => `<span class="badge ${esc(String(value).toLowerCase())}">${esc(value)}</span>`;
  const date = (value) => value ? new Date(value).toLocaleString() : "—";
  const formatDetail = (detail) => Array.isArray(detail) ? detail.map((item) => `${(item.loc || []).join(".")}: ${item.msg || "invalid"}`).join("; ") : (detail || "Request failed");
  const toast = (message, error = false) => { const el = $("#toast"); el.textContent = message; el.className = `toast show${error ? " error" : ""}`; clearTimeout(toast.timer); toast.timer = setTimeout(() => { el.className = "toast"; }, 4200); };
  async function api(path, options = {}) {
    const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
    if (adminToken) headers["X-Admin-Token"] = adminToken;
    const response = await fetch(`/api${path}`, { ...options, headers });
    let payload = null; try { payload = await response.json(); } catch (_) { /* empty response */ }
    if (response.status === 401) {
      try { sessionStorage.removeItem(ADMIN_TOKEN_KEY); } catch (_) { /* ignore */ }
      adminToken = "";
      updateTokenBar();
    }
    if (!response.ok) throw new Error(formatDetail(payload?.detail || `HTTP ${response.status}`));
    return payload;
  }
  function updateTokenBar() { const bar = $("#admin-token-bar"); if (bar) bar.classList.toggle("hidden", Boolean(adminToken)); }
  function ensureAdminToken() {
    if (adminToken) return true;
    const entered = window.prompt("请输入 start.bat 控制台显示的 Local admin token。token 只保存在当前浏览器标签页。");
    if (!entered) { updateTokenBar(); return false; }
    adminToken = entered.trim();
    try { sessionStorage.setItem(ADMIN_TOKEN_KEY, adminToken); } catch (_) { /* ignore */ }
    updateTokenBar();
    return Boolean(adminToken);
  }
  async function setAdminToken() { if (ensureAdminToken()) { try { await loadCatalog(); toast("Local admin token 已保存到当前标签页"); } catch (error) { toast(error.message, true); } } }
  function showTab(name) { $$(".tab").forEach((el) => el.classList.toggle("active", el.dataset.tab === name)); $$(".page").forEach((el) => el.classList.toggle("active", el.id === name)); }
  function setRuntime(runtime) {
    state.runtime = runtime;
    const pill = $("#runtime-pill"); const online = runtime.agent_status === "running";
    pill.className = `runtime-pill${online ? " online" : ""}`; pill.innerHTML = `<span class="dot"></span><span>${online ? "Agent online" : esc(runtime.agent_status)} · Slack ${runtime.slack_connected ? "connected" : "disconnected"}</span>`;
    $("#runtime-cards").innerHTML = [
      ["Agent", runtime.agent_status, runtime.agent_status === "running" ? "good" : "warn"],
      ["Slack", runtime.slack_connected ? "Connected" : runtime.slack_state, runtime.slack_connected ? "good" : "warn"],
      ["Platform", runtime.platform, ""], ["Automation", runtime.automation_mode, ""],
      ["Active jobs", runtime.active_login_jobs, runtime.active_login_jobs ? "warn" : "good"],
      ["Queued jobs", runtime.queued_login_jobs, ""], ["Accounts", runtime.account_count, ""], ["Data directory", runtime.data_dir, ""],
    ].map(([label, value, cls]) => `<div class="card"><div class="card-label">${esc(label)}</div><div class="card-value ${cls}">${esc(value)}</div></div>`).join("");
  }
  function renderDashboard(data) { setRuntime(data.runtime); renderAccountStatus(data.accounts || []); renderHistory(data.history || [], "#dashboard-history"); }
  function renderAccountStatus(items) {
    $("#dashboard-accounts").innerHTML = items.length ? `<table><thead><tr><th>Alias</th><th>Process</th><th>Config</th><th>Issues</th></tr></thead><tbody>${items.map((a) => `<tr><td><strong>${esc(a.alias)}</strong></td><td>${badge(a.process_state)}</td><td>${a.configuration_valid ? badge("valid") : badge("invalid")}</td><td>${(a.issues || []).map((issue) => esc(issue)).join("<br>") || "—"}</td></tr>`).join("")}</tbody></table>` : `<div class="empty">尚未配置 Account</div>`;
  }
  async function loadHistory() {
    try {
      renderHistory(await api("/history?limit=500"), "#history-table");
    } catch (error) {
      if (error.message !== "Local admin token required") toast(error.message, true);
    }
  }
  function renderHistory(items, target) {
    const node = $(target); if (!node) return;
    node.innerHTML = items.length ? `<table><thead><tr><th>时间</th><th>Sender</th><th>Target / Account</th><th>结果</th><th>分类</th><th>耗时</th></tr></thead><tbody>${items.map((h) => `<tr><td>${esc(date(h.timestamp))}</td><td>${esc(h.sender)}</td><td>${esc(h.target)}<br><span class="muted">${esc(h.account_alias)}</span></td><td>${badge(h.status)}</td><td>${esc(h.error_category)}</td><td>${esc(h.duration_ms)} ms</td></tr>`).join("")}</tbody></table>` : `<div class="empty">暂无登录历史</div>`;
  }
  function renderAccounts() {
    $("#accounts-table").innerHTML = state.accounts.length ? `<table><thead><tr><th>Name</th><th>Alias</th><th>Login ID</th><th>Server</th><th>状态</th><th>操作</th></tr></thead><tbody>${state.accounts.map((a) => `<tr><td>${esc(a.display_name)}</td><td><code>${esc(a.alias)}</code></td><td>${esc(a.login_id)}</td><td>${esc(a.server)}</td><td>${a.enabled ? badge("enabled") : badge("disabled")}</td><td><div class="actions"><button data-action="edit-account" data-id="${esc(a.id)}">编辑</button><button data-action="duplicate-account" data-id="${esc(a.id)}">复制</button><button data-action="validate-account" data-id="${esc(a.id)}">Test</button><button data-action="toggle-account" data-id="${esc(a.id)}" data-enabled="${a.enabled}">${a.enabled ? "Disable" : "Enable"}</button><button class="danger" data-action="delete-account" data-id="${esc(a.id)}">删除</button></div></td></tr>`).join("")}</tbody></table>` : `<div class="empty">尚未配置 Account。使用“新增 Account”开始。</div>`;
  }
  function resetAccountForm() { $("#account-form").reset(); $("#account-id").value = ""; $("#account-editor-title").textContent = "新增 Account"; $("#account-duplicate-warning").classList.add("hidden"); $("#account-success-regex-hint").textContent = ""; $("#account-enabled").checked = true; $("#account-save-login").checked = false; loadWin32Fallback(null); $("#account-win32-enabled").checked = false; updateCommandPreview(); $("#account-editor").classList.add("hidden"); }
  const WIN32_ID_KEYS = ["login_id_combo", "login_id_edit", "otp", "server_combo", "server_edit", "login_button"];
  function win32IdField(key) { return $(`#account-win32-id-${key.replace(/_/g, "-")}`); }
  function loadWin32Fallback(config) {
    const c = config || {};
    $("#account-win32-enabled").checked = !!c.enabled;
    $("#account-win32-class").value = c.dialog_class || "";
    $("#account-win32-anchors").value = (c.anchors || []).join(", ");
    WIN32_ID_KEYS.forEach((key) => { const el = win32IdField(key); if (el) el.value = (c.control_ids && c.control_ids[key]) || ""; });
  }
  function collectWin32Fallback() {
    const controlIds = {};
    WIN32_ID_KEYS.forEach((key) => { const el = win32IdField(key); if (!el || el.value === "") return; const n = Number(el.value); if (Number.isInteger(n) && n > 0) controlIds[key] = n; });
    return { enabled: $("#account-win32-enabled").checked, dialog_class: $("#account-win32-class").value.trim() || "#32770", anchors: $("#account-win32-anchors").value.split(",").map((s) => s.trim()).filter(Boolean), control_ids: controlIds };
  }
  function fillAccountForm(a) { $("#account-editor-title").textContent = `编辑 ${a.alias}`; $("#account-id").value = a.id; $("#account-display-name").value = a.display_name; $("#account-alias").value = a.alias; $("#account-login-id").value = a.login_id; $("#account-server").value = a.server; $("#account-terminal-path").value = a.terminal_path; $("#account-process-name").value = a.process_name || ""; $("#account-profile-path").value = a.profile_path || ""; $("#account-window-regex").value = a.window_title_regex || ""; $("#account-success-window-regex").value = a.success_window_title_regex || ""; $("#account-launch-args").value = JSON.stringify(a.launch_arguments || []); $("#account-control-ids").value = JSON.stringify(a.control_ids || {}, null, 2); $("#account-control-titles").value = JSON.stringify(a.control_titles || {}, null, 2); $("#account-enabled").checked = a.enabled; $("#account-save-login").checked = a.save_login_info; loadWin32Fallback(a.win32_fallback); updateCommandPreview(); $("#account-success-regex-hint").textContent = ""; $("#account-duplicate-warning").classList.add("hidden"); $("#account-editor").classList.remove("hidden"); }
  function successRegexContainsLoginId(regex, loginId) { const id = String(loginId ?? ""); if (!id) return false; return String(regex ?? "").includes(id); }
  function updateCommandPreview() { const node = $("#account-command-preview"); if (!node) return; const alias = $("#account-alias").value.trim(); if (!alias) { node.textContent = "先填写 Alias 再保存，Slack 用 /mt4 <alias> <凭据> 调用。"; return; } node.innerHTML = `/mt4 ${esc(alias)} &lt;凭据&gt;`; }
  function duplicateAccountForm(source) { if (!source) return; $("#account-editor-title").textContent = `复制 ${source.alias} 为新 Account`; $("#account-id").value = ""; $("#account-display-name").value = `${source.display_name || ""} 副本`; $("#account-alias").value = ""; $("#account-login-id").value = ""; $("#account-server").value = source.server || ""; $("#account-terminal-path").value = source.terminal_path || ""; $("#account-process-name").value = source.process_name || ""; $("#account-profile-path").value = source.profile_path || ""; $("#account-window-regex").value = source.window_title_regex || ""; if (successRegexContainsLoginId(source.success_window_title_regex, source.login_id)) { $("#account-success-window-regex").value = ""; $("#account-success-regex-hint").textContent = "原 success 正则包含源账号 Login ID，已清空。请使用锚定 broker / server 的稳定表达式，不要写 Login ID。"; } else { $("#account-success-window-regex").value = source.success_window_title_regex || ""; $("#account-success-regex-hint").textContent = ""; } $("#account-launch-args").value = JSON.stringify(source.launch_arguments || []); $("#account-control-ids").value = JSON.stringify(source.control_ids || {}, null, 2); $("#account-control-titles").value = JSON.stringify(source.control_titles || {}, null, 2); $("#account-enabled").checked = false; $("#account-save-login").checked = !!source.save_login_info; loadWin32Fallback(source.win32_fallback ? JSON.parse(JSON.stringify(source.win32_fallback)) : null); $("#account-duplicate-warning").classList.remove("hidden"); updateCommandPreview(); $("#account-editor").classList.remove("hidden"); }
  function parseJson(value, fallback) { if (!value.trim()) return fallback; try { return JSON.parse(value); } catch (_) { throw new Error("JSON 格式无效"); } }
  async function saveAccount(event) { event.preventDefault(); try { const payload = { display_name: $("#account-display-name").value, alias: $("#account-alias").value, login_id: $("#account-login-id").value, server: $("#account-server").value, terminal_path: $("#account-terminal-path").value, process_name: $("#account-process-name").value || null, profile_path: $("#account-profile-path").value || null, window_title_regex: $("#account-window-regex").value || null, success_window_title_regex: $("#account-success-window-regex").value || null, launch_arguments: parseJson($("#account-launch-args").value, []), control_ids: parseJson($("#account-control-ids").value, {}), control_titles: parseJson($("#account-control-titles").value, {}), enabled: $("#account-enabled").checked, save_login_info: $("#account-save-login").checked, win32_fallback: collectWin32Fallback() }; const id = $("#account-id").value; if (id) await api(`/accounts/${encodeURIComponent(id)}`, { method: "PUT", body: JSON.stringify(payload) }); else await api("/accounts", { method: "POST", body: JSON.stringify(payload) }); resetAccountForm(); await loadCatalog(); toast("Account 已保存"); } catch (error) { toast(error.message, true); } }
  function renderGroups() { const select = $("#group-members"); const selected = new Set(Array.from(select.selectedOptions).map((o) => o.value)); select.innerHTML = state.accounts.map((a) => `<option value="${esc(a.id)}" ${selected.has(a.id) ? "selected" : ""}>${esc(a.alias)} — ${esc(a.display_name)}</option>`).join(""); $("#groups-list").innerHTML = state.groups.length ? state.groups.map((g) => `<article class="panel group-card"><div class="section-heading"><div><h3>${esc(g.name)} ${g.enabled ? badge("enabled") : badge("disabled")}</h3><p class="muted">${g.accounts.length} 个账号 · 按顺序执行 · ${g.shared_otp_confirmed ? "shared OTP 已确认" : "shared OTP 未确认，Group 登录会被阻止"}</p></div><div class="actions"><button data-action="edit-group" data-id="${esc(g.id)}">编辑</button><button class="danger" data-action="delete-group" data-id="${esc(g.id)}">删除</button></div></div><div class="group-members">${g.accounts.length ? g.accounts.map((a, i) => `<span class="member-chip"><strong>${i + 1}. ${esc(a.alias)}</strong>${a.enabled === false ? badge("disabled") : ""}${i > 0 ? `<button title="上移" data-action="group-up" data-id="${esc(g.id)}" data-index="${i}">↑</button>` : ""}${i < g.accounts.length - 1 ? `<button title="下移" data-action="group-down" data-id="${esc(g.id)}" data-index="${i}">↓</button>` : ""}</span>`).join("") : `<span class="muted">没有成员</span>`}</div></article>`).join("") : `<div class="panel empty">尚未配置 Group</div>`; }
  function resetGroupForm() { $("#group-form").reset(); $("#group-id").value = ""; $("#group-enabled").checked = true; $("#group-shared-otp").checked = false; $("#group-editor").classList.add("hidden"); }
  function fillGroupForm(g) { $("#group-editor-title").textContent = `编辑 ${g.name}`; $("#group-id").value = g.id; $("#group-name").value = g.name; $("#group-enabled").checked = g.enabled; $("#group-shared-otp").checked = g.shared_otp_confirmed; const select = $("#group-members"); const byId = new Map(state.accounts.map((a) => [a.id, a])); const ordered = g.account_ids.map((id) => byId.get(id)).filter(Boolean).concat(state.accounts.filter((a) => !g.account_ids.includes(a.id))); select.innerHTML = ordered.map((a) => `<option value="${esc(a.id)}" ${g.account_ids.includes(a.id) ? "selected" : ""}>${esc(a.alias)} — ${esc(a.display_name)}</option>`).join(""); $("#group-editor").classList.remove("hidden"); }
  async function saveGroup(event) { event.preventDefault(); try { const payload = { name: $("#group-name").value, enabled: $("#group-enabled").checked, shared_otp_confirmed: $("#group-shared-otp").checked, account_ids: Array.from($("#group-members").selectedOptions).map((o) => o.value) }; const id = $("#group-id").value; if (id) await api(`/groups/${encodeURIComponent(id)}`, { method: "PUT", body: JSON.stringify(payload) }); else await api("/groups", { method: "POST", body: JSON.stringify(payload) }); resetGroupForm(); await loadCatalog(); toast("Group 已保存"); } catch (error) { toast(error.message, true); } }
  async function moveMember(id, index, direction) { const group = state.groups.find((g) => g.id === id); if (!group) return; const ids = [...group.account_ids]; const target = index + direction; if (target < 0 || target >= ids.length) return; [ids[index], ids[target]] = [ids[target], ids[index]]; await api(`/groups/${encodeURIComponent(id)}/order`, { method: "PUT", body: JSON.stringify({ account_ids: ids }) }); await loadCatalog(); }
  async function loadCatalog() { const [accounts, groups, dashboard] = await Promise.all([api("/accounts"), api("/groups"), api("/dashboard")]); state.accounts = accounts; state.groups = groups; renderAccounts(); renderGroups(); renderDashboard(dashboard); renderSlackBindings(state.slackBindings); }
  function currentBindingSelection() { const map = {}; $$("#slack-bindings input[data-bind-user]").forEach((el) => { (map[el.dataset.bindUser] = map[el.dataset.bindUser] || []); if (el.checked) map[el.dataset.bindUser].push(el.value); }); return map; }
  function renderSlackBindings(saved) { const node = $("#slack-bindings"); if (!node) return; const users = $("#slack-users").value.split(/\n|,/).map((x) => x.trim()).filter(Boolean); const keep = currentBindingSelection(); const aliases = state.accounts.map((a) => a.alias); const known = new Set(aliases); node.innerHTML = users.length ? users.map((u) => { const selected = new Set(keep[u] !== undefined ? keep[u] : (saved || {})[u] || []); const options = aliases.concat([...selected].filter((al) => !known.has(al))); const boxes = options.length ? options.map((al) => `<label class="checkbox"><input type="checkbox" data-bind-user="${esc(u)}" value="${esc(al)}"${selected.has(al) ? " checked" : ""}> <code>${esc(al)}</code>${known.has(al) ? "" : `<span class="muted">（未知 alias，保存会被后端拒绝）</span>`}</label>`).join("") : `<span class="muted">还没有 Account，先去 Accounts 页新增。</span>`; const assigned = [...selected].filter((al) => known.has(al)).length; return `<div class="binding-row"><div><strong>${esc(u)}</strong></div><div class="binding-aliases">${boxes}</div><div>${assigned ? "" : `<span class="muted">no accounts assigned — 该用户不能操作任何账号</span>`}</div></div>`; }).join("") : `<span class="muted">先在上方填写 Allowed Slack User IDs。</span>`; }
  function collectSlackBindings() { const map = {}; const users = $("#slack-users").value.split(/\n|,/).map((x) => x.trim()).filter(Boolean); users.forEach((u) => { map[u] = []; }); $$("#slack-bindings input[data-bind-user]").forEach((el) => { if (el.checked && map[el.dataset.bindUser] !== undefined) map[el.dataset.bindUser].push(el.value); }); return map; }
  async function loadSlack() { const s = await api("/slack"); $("#slack-enabled").checked = s.enabled; $("#slack-users").value = (s.allowed_slack_user_ids || []).join("\n"); state.slackBindings = s.slack_user_account_bindings || {}; renderSlackBindings(state.slackBindings); $("#slack-app-token").value = ""; $("#slack-bot-token").value = ""; $("#clear-app-token").checked = false; $("#clear-bot-token").checked = false; const stateText = s.connected ? "Connected" : s.connection_state; $("#slack-status").innerHTML = `${badge(stateText)} <span>App token: ${s.app_token_configured ? "已配置（已隐藏）" : "未配置"} · Bot token: ${s.bot_token_configured ? "已配置（已隐藏）" : "未配置"}</span>${s.last_error ? `<span class="muted"> · ${esc(s.last_error)}</span>` : ""}`; }
  async function saveSlack(event) { event.preventDefault(); try { const payload = { enabled: $("#slack-enabled").checked, allowed_slack_user_ids: $("#slack-users").value.split(/\n|,/).map((x) => x.trim()).filter(Boolean), slack_user_account_bindings: collectSlackBindings(), app_token: $("#slack-app-token").value || null, bot_token: $("#slack-bot-token").value || null, clear_app_token: $("#clear-app-token").checked, clear_bot_token: $("#clear-bot-token").checked }; const result = await api("/slack", { method: "PUT", body: JSON.stringify(payload) }); $("#slack-app-token").value = ""; $("#slack-bot-token").value = ""; await loadSlack(); toast(result.connected ? "Slack 设置已保存并已连接" : `Slack 设置已保存，但尚未连接：${result.last_error || result.connection_state}`, !result.connected); } catch (error) { toast(error.message, true); } }
  async function handleAction(button) { const action = button.dataset.action; try { if (action === "set-admin-token") { await setAdminToken(); } else if (action === "refresh-test-status") { await loadTestStatus(); } else if (action === "run-test-environment") { await runTestPhase("environment"); } else if (action === "run-test-discovery") { await runTestPhase("discovery", $("#test-account").value || null); } else if (action === "run-test-slack") { await runTestPhase("slack"); } else if (action === "run-test-real-login") { await runRealLoginTest(); } else if (action === "run-test-full-slack") { await runFullSlackTest(); } else if (action === "await-test-slack") { await awaitTestSlack(); } else if (action === "run-test-group") { await runGroupTest(); } else if (action === "apply-test-selectors") { await applyTestSelectors(); } else if (action === "run-win32-inspect") { await runWin32Inspect(); } else if (action === "apply-win32-settings") { await applyWin32Settings(); } else if (action === "view-report") { await viewReport(button.dataset.filename); } else if (action === "select-test-report") { await selectTestReport(button.dataset.reportId); } else if (action === "refresh-dashboard") { renderDashboard(await api("/dashboard")); } else if (action === "refresh-history") { await loadHistory(); } else if (action === "new-account") { resetAccountForm(); $("#account-editor").classList.remove("hidden"); } else if (action === "cancel-account") { resetAccountForm(); } else if (action === "edit-account") { fillAccountForm(state.accounts.find((a) => a.id === button.dataset.id)); } else if (action === "duplicate-account") { duplicateAccountForm(state.accounts.find((a) => a.id === button.dataset.id)); } else if (action === "validate-account") { const result = await api(`/accounts/${encodeURIComponent(button.dataset.id)}/validate`, { method: "POST" }); toast(result.valid ? "配置检查通过（Windows 路径存在性仍需实机确认）" : `配置检查未通过：${result.checks.filter((c) => !c.passed).map((c) => c.detail).join("；")}`, !result.valid); } else if (action === "toggle-account") { await api(`/accounts/${encodeURIComponent(button.dataset.id)}/enabled`, { method: "POST", body: JSON.stringify({ enabled: button.dataset.enabled !== "true" }) }); await loadCatalog(); } else if (action === "delete-account") { if (!confirm("确定删除这个 Account？关联 Group 会自动移除它。")) return; await api(`/accounts/${encodeURIComponent(button.dataset.id)}`, { method: "DELETE" }); await loadCatalog(); } else if (action === "new-group") { resetGroupForm(); $("#group-members").innerHTML = state.accounts.map((a) => `<option value="${esc(a.id)}">${esc(a.alias)} — ${esc(a.display_name)}</option>`).join(""); $("#group-editor").classList.remove("hidden"); } else if (action === "cancel-group") { resetGroupForm(); } else if (action === "edit-group") { fillGroupForm(state.groups.find((g) => g.id === button.dataset.id)); } else if (action === "delete-group") { if (!confirm("确定删除这个 Group？")) return; await api(`/groups/${encodeURIComponent(button.dataset.id)}`, { method: "DELETE" }); await loadCatalog(); } else if (action === "group-up") { await moveMember(button.dataset.id, Number(button.dataset.index), -1); } else if (action === "group-down") { await moveMember(button.dataset.id, Number(button.dataset.index), 1); } else if (action === "test-slack") { const result = await api("/slack/test", { method: "POST" }); toast(result.message, !result.ok); } } catch (error) { toast(error.message, true); } }
  const TEST_STEPS = [
    { key: "environment", label: "Step 1 Environment", requires: [], unlock: "点 Run Phase 1，检查环境、配置和 Account 静态完整性。不需要 OTP。" },
    { key: "discovery", label: "Step 2 MT4 Detect", requires: ["environment"], unlock: "选好测试 Account，保持 MT4 登录窗可见后点 Detect。不需要 OTP。UIA 找不到时会自动尝试已启用的 Win32 dialog fallback。" },
    { key: "slack", label: "Step 3 Slack", requires: ["environment"], unlock: "点 Run Phase 3 验证 Slack 命令链路，不需要 OTP。" },
    { key: "real_login", label: "Step 4 Real Login", requires: ["environment", "discovery"], unlock: "整个流程第一次使用真实 OTP。先确认券商不处于 maintenance/offline，并勾选两个确认框。" },
    { key: "group", label: "Step 5 Group", requires: ["environment", "discovery", "real_login"], unlock: "最后才做。Group 需要 shared_otp_confirmed，并按成员数准备一次性 OTP。" },
  ];
  function testStepState(data) {
    const done = new Set((data && data.completed_phases) || []);
    const verdicts = (data && data.phase_status) || {};
    const manual = (data && data.phase_manual) || {};
    const route = (data && data.discovery_route) || "";
    return TEST_STEPS.map((step) => {
      const ran = done.has(step.key);
      const verdict = verdicts[step.key] || "not_run";
      const manualCount = manual[step.key] || 0;
      const usable = ran && (step.key !== "discovery" || route === "uia" || route === "win32");
      return { ...step, ran, verdict, manualCount, usable, route };
    });
  }
  // A manual or optional check is something the human still has to decide, not a
  // degradation. It is reported as a count so the step card is not painted amber.
  function manualNote(step) {
    if (!step.manualCount) return "";
    return step.manualCount === 1
      ? "1 manual confirmation remaining"
      : `${step.manualCount} manual checks remaining`;
  }
  function missingSteps(steps, keys) { return keys.filter((key) => !(steps.find((s) => s.key === key) || {}).usable); }
  function stepLabels(steps, keys) { return keys.map((key) => (steps.find((s) => s.key === key) || {}).label || key).join(" 和 "); }
  function requireSteps(stepKey, action) {
    const steps = testStepState(state.testStatus);
    const step = steps.find((s) => s.key === stepKey);
    if (!step) return true;
    const missing = missingSteps(steps, step.requires);
    if (!missing.length) return true;
    toast(`请先完成 ${stepLabels(steps, missing)}，再${action}。`, true);
    return false;
  }
  function renderTestGuide(data) {
    state.testStatus = data;
    const bar = $("#test-steps"); const hint = $("#test-next-hint");
    const steps = testStepState(data);
    const currentIndex = steps.findIndex((s) => !s.usable);
    if (bar) bar.innerHTML = steps.map((step, index) => {
      const missing = missingSteps(steps, step.requires);
      const note = manualNote(step);
      let stateText; let mark;
      if (step.usable && step.verdict === "fail") { stateText = "已运行但有失败"; mark = badge("FAIL"); }
      else if (step.usable && step.verdict === "warn") { stateText = "有真实告警，流程仍可继续"; mark = badge("WARN"); }
      else if (step.usable && step.verdict === "pass") { stateText = note ? `已通过 · ${note}` : "已通过"; mark = badge("PASS"); }
      else if (step.usable) { stateText = note || "已运行"; mark = badge("READY"); }
      else if (step.ran) { stateText = "已运行，但未解析出可用路径"; mark = badge("WARN"); }
      else if (missing.length) { stateText = `待完成 ${stepLabels(steps, missing)}`; mark = index === currentIndex ? badge("WARN") : `<span class="muted">未开始</span>`; }
      else { stateText = "当前可执行"; mark = badge("WARN"); }
      return `<div class="card"><div class="card-label">${esc(step.label)}</div><div class="card-value">${mark}</div><div class="muted">${esc(stateText)}</div></div>`;
    }).join("");
    if (!hint) return;
    const route = (data && data.discovery_route) || "";
    const routeText = route === "win32" ? "当前 Real Login 走 Win32 dialog fallback（UIA Automation ID 不需要）。" : route === "uia" ? "当前 Real Login 走 UIA selectors。" : "";
    if (currentIndex === -1) { hint.innerHTML = `<strong>5 个 Step 全部完成。</strong> ${esc(routeText)} 报告已生成在 data-dir 的 <code>reports/&lt;run-id&gt;/</code>。任何无法真机确认的项仍须保持 <code>WINDOWS_REAL_TEST_REQUIRED</code>，不得手动改成 success。`; return; }
    const step = steps[currentIndex];
    const missing = missingSteps(steps, step.requires);
    hint.innerHTML = (routeText ? `<strong>${esc(routeText)}</strong> ` : "") + (missing.length
      ? `<strong>下一步：${esc(step.label)}</strong> — 尚未解锁，请先完成 ${esc(stepLabels(steps, missing))}。${esc(step.unlock)}`
      : `<strong>下一步：${esc(step.label)}</strong> — ${esc(step.unlock)}`);
  }
  function renderTestStatus(data) {
    renderTestGuide(data);
    const notice = $("#test-platform-notice");
    if (notice) notice.innerHTML = data.windows_available ? `${badge("PASS")} Windows Test Runner 已就绪。` : `${badge("NOT_RUN")} Windows Acceptance Tests require Windows。macOS/Linux 只显示模型和 SAFE 检查，不显示真实登录 PASS。`;
    const accountSelect = $("#test-account");
    if (accountSelect) { const current = accountSelect.value; accountSelect.innerHTML = `<option value="">选择 Account</option>${state.accounts.map((a) => `<option value="${esc(a.id)}">${esc(a.alias)} — ${esc(a.display_name)}</option>`).join("")}`; accountSelect.value = current; }
    const groupSelect = $("#test-group");
    if (groupSelect) { const current = groupSelect.value; groupSelect.innerHTML = `<option value="">选择 Group</option>${state.groups.map((g) => `<option value="${esc(g.name)}">${esc(g.name)}</option>`).join("")}`; groupSelect.value = current; }
    updateWin32Button();
    const results = data.results || [];
    const resultNode = $("#test-results");
    if (resultNode) resultNode.innerHTML = results.length ? `<table><thead><tr><th>Phase/Test</th><th>Status</th><th>Observed</th><th>Action</th></tr></thead><tbody>${results.map((r) => `<tr class="test-result-row"><td><code>${esc(r.id)}</code><br>${esc(r.name)}</td><td>${badge(r.status)}</td><td>${esc(r.message)}</td><td>${esc(r.suggested_action || "—")}</td></tr>`).join("")}</tbody></table>` : `<div class="empty">尚未运行 Windows Acceptance Test。</div>`;
    const controls = data.detected_controls || [];
    const route = (data && data.discovery_route) || "";
    const allHigh = controls.length > 0 && controls.every((c) => c.confidence === "HIGH");
    const canApply = allHigh && route === "uia";
    const applyNote = route === "win32" ? "当前 Account 走 Win32 dialog fallback，不需要应用 UIA Automation ID。" : allHigh ? "" : "没有全部为 HIGH confidence 的 selector，无法安全应用。";
    const controlsNode = $("#test-controls");
    if (controlsNode) controlsNode.innerHTML = controls.length ? `<h4>Detected UIA candidates</h4><table><thead><tr><th>Field</th><th>AutomationId</th><th>Type</th><th>Password</th><th>Confidence</th></tr></thead><tbody>${controls.map((c) => `<tr><td>${esc(c.field)}</td><td>${esc(c.automation_id || "—")}</td><td>${esc(c.control_type || "—")}</td><td>${c.is_password ? "yes" : "no"}</td><td>${badge(c.confidence === "HIGH" ? "PASS" : "MANUAL")}</td></tr>`).join("")}</tbody></table>${canApply ? `<button class="button secondary" data-action="apply-test-selectors">Apply detected selectors</button>` : `<p class="muted">${esc(applyNote)}</p>`}` : "";
    const reportsNode = $("#test-reports");
    if (reportsNode && data.report_id) reportsNode.innerHTML = `<div class="test-report-links"><span class="muted">Report ${esc(data.report_id)}</span><button class="button secondary" data-action="view-report" data-filename="report.html">View HTML</button><button class="button secondary" data-action="view-report" data-filename="report.json">Export JSON</button></div>`;
  }
  async function loadTestStatus() { try { const [status, reports] = await Promise.all([api("/test/windows"), api("/test/windows/report")]); renderTestStatus(status); const node = $("#test-reports"); if (node && reports.length && !status.report_id) node.innerHTML = reports.map((item) => `<div class="test-report-links"><span class="muted">${esc(item.report_id)}</span><button class="button secondary" data-action="select-test-report" data-report-id="${esc(item.report_id)}">Select</button></div>`).join(""); } catch (error) { toast(error.message, true); } }
  async function runTestPhase(phase, accountId = null) { if (!requireSteps(phase, `执行 ${phase} 阶段`)) return; const result = await api("/test/windows/phase", { method: "POST", body: JSON.stringify({ phase, account_id: accountId }) }); renderTestStatus(result.status); toast(`Windows Test ${phase} 已完成`); }
  async function runRealLoginTest() { if (!requireSteps("real_login", "执行真实登录测试")) return; if (!$("#test-broker-confirm").checked) { toast("请先人工确认 Rakuten 当前不处于已知 maintenance/offline 时段。", true); return; } if (!$("#test-real-confirm").checked) { toast("必须先确认真实测试 Account。", true); return; } const otp = $("#test-otp").value; if (!otp) { toast("请输入本次测试 OTP。", true); return; } const accountId = $("#test-account").value; if (!accountId) { toast("请先选择 Account。", true); return; } try { const result = await api("/test/windows/real-login", { method: "POST", body: JSON.stringify({ account_id: accountId, otp, confirmed: true, broker_confirmed: true }) }); $("#test-otp").value = ""; renderTestStatus(result.status); toast("Real login test 已完成，结果见报告"); await loadHistory(); } catch (error) { $("#test-otp").value = ""; toast(error.message, true); } }
  async function runFullSlackTest() { const accountId = $("#test-account").value; if (!accountId) { toast("请先选择 Account。", true); return; } try { const result = await api("/test/windows/real-login", { method: "POST", body: JSON.stringify({ account_id: accountId, full_slack: true, confirmed: true, broker_confirmed: true }) }); renderTestStatus(result.status); const session = result.results.find((item) => item.evidence && item.evidence.session_id); state.slackSession = session ? session.evidence.session_id : ""; $("#test-slack-session").innerHTML = session ? `ACTION REQUIRED：发送 <code>/mt4 ${esc(session.evidence.account_alias)} &lt;OTP&gt;</code>，然后 Await session <code>${esc(session.evidence.session_id)}</code>。` : "请在受控 Slack 频道完成 Full Slack E2E。"; toast("Full Slack E2E 已准备，请手动发送命令"); } catch (error) { toast(error.message, true); } }

  async function awaitTestSlack() { if (!state.slackSession) { toast("请先点击 Start Full Slack E2E。", true); return; } try { const result = await api("/test/windows/slack/await", { method: "POST", body: JSON.stringify({ session_id: state.slackSession, timeout_seconds: 120 }) }); renderTestStatus(result.status); toast("Full Slack E2E 结果已记录"); await loadHistory(); } catch (error) { toast(error.message, true); } }
  async function runGroupTest() { if (!requireSteps("group", "执行 Group 测试")) return; if (!$("#test-broker-confirm").checked) { toast("请先人工确认 Rakuten 当前不处于已知 maintenance/offline 时段。", true); return; } if (!$("#test-group-confirm").checked) { toast("必须先确认 Group 成员和顺序。", true); return; } const otp = $("#test-otp").value; const groupName = $("#test-group").value; if (!otp || !groupName) { toast("请选择 Group 并输入本次测试 OTP。", true); return; } try { const result = await api("/test/windows/group", { method: "POST", body: JSON.stringify({ group_name: groupName, otp, confirmed: true, broker_confirmed: true }) }); $("#test-otp").value = ""; renderTestStatus(result.status); toast("Group test 已完成，结果见报告"); await loadHistory(); } catch (error) { $("#test-otp").value = ""; toast(error.message, true); } }
  async function applyTestSelectors() { const accountId = $("#test-account").value; if (!accountId) { toast("请先选择 Account。", true); return; } const result = await api("/test/windows/selectors", { method: "POST", body: JSON.stringify({ account_id: accountId, confirmed: true }) }); toast(result.applied ? "检测到的 selectors 已应用" : result.reason || "Selectors 未应用", !result.applied); await loadCatalog(); await loadTestStatus(); }
  const WIN32_REQUIRED_KEYS = ["dialog_class", "anchors", "login_id_combo", "login_id_edit", "otp", "server_combo", "server_edit", "login_button"];
  function win32SuggestedText(key, value) { if (value === null || value === undefined) return "—"; if (key === "anchors") return Array.isArray(value) && value.length ? value.join(", ") : "—"; return String(value); }
  function updateWin32Button() { const btn = $("#win32-inspect-btn"); const select = $("#test-account"); if (!btn || !select) return; btn.disabled = !select.value; }
  function renderWin32Inspector(report, accountId) {
    const node = $("#win32-inspector"); if (!node) return;
    state.win32 = report; state.win32AccountId = accountId;
    if (!report) { node.innerHTML = ""; return; }
    const outcome = report.outcome || "unavailable";
    const detail = report.detail || "";
    const suggested = report.suggested || {};
    const confidence = report.confidence || {};
    const appliable = !!report.appliable;
    const missing = report.missing || [];
    const titles = report.titles || {};
    const captions = report.menu_captions || [];
    const safetyCopy = "Detect Win32 only reads the login dialog and changes nothing by itself. Nothing is saved until a human applies. The inspection never reads the credential field.";
      const scopeCopy = "New broker / MT4 adaptation only. Configured accounts do not need this when MT4_DISCOVERY_READY is PASS. 仅用于新券商 / 新 MT4 版本适配；已配置且 MT4_DISCOVERY_READY=PASS 的 Account 不需要运行。不会影响已配置 Account 的 Real Login。";
      // Two different failures need two different fixes: several candidates means
      // close the unrelated windows, none means open the dialog by hand. Guessing
      // here would send the user to the wrong action.
      const multipleCandidates = /login-shaped windows matched/.test(detail || "");
      const noCandidates = /no window with both an Edit and a Button/.test(detail || "");
      const adviceCopy = multipleCandidates
        ? "检测到多个候选窗口，请关闭无关的下单 / 新建账户 / 其它输入窗口后重试。Close the unrelated order, new-account or other input windows, then run Detect Win32 again."
        : noCandidates
          ? "未找到登录窗口；如果 auto-open 未生效，请手动打开登录框后重试。No login dialog was found; if auto-open did not work, open it by hand and retry."
          : "按上方 detail 处理即可。Follow the detail above.";
      if (outcome !== "inspected") { node.innerHTML = `<div class="card"><div class="card-label">Win32 inspection — ${esc(outcome)}</div><div class="card-value">${esc(outcome)}</div><p class="muted">${esc(scopeCopy)}</p><p class="muted">${esc(detail)}</p><p class="muted">${esc(adviceCopy)}</p><p class="muted">${esc(safetyCopy)}</p></div>`; return; }
    const rows = WIN32_REQUIRED_KEYS.map((key) => { const conf = confidence[key] || "NEEDS_CONFIRMATION"; const high = conf === "HIGH"; const mark = high ? `${badge("PASS")} HIGH` : `${badge("MANUAL")} NEEDS_CONFIRMATION — value is not confirmed`; return `<tr><td><code>${esc(key)}</code></td><td>${esc(win32SuggestedText(key, suggested[key]))}</td><td>${mark}</td></tr>`; }).join("");
    const missingNote = (!appliable || missing.length) ? `<div class="warning-box">Not appliable: ${missing.length ? esc(missing.join(", ")) : "suggestion is incomplete"}. Fill the listed fields by hand, or open the dialog and retry. No Apply button is offered.</div>` : "";
    const applyBtn = appliable ? `<div class="actions"><button class="button secondary" data-action="apply-win32-settings">Apply detected Win32 settings</button></div>` : "";
    const observedClass = report.dialog_class ?? "—";
    const observedTitle = report.dialog_title ?? "—";
    const titleRow = (label, value) => `<div><span class="muted">${esc(label)}: </span>${value ? `<code>${esc(value)}</code>` : "<span class=\"muted\">—</span>"}</div>`;
    node.innerHTML = `<div class="card"><div class="card-label">Win32 inspection — inspected</div><p class="muted">${esc(scopeCopy)}</p><p class="muted">${esc(detail)}</p><p class="muted">${esc(safetyCopy)}</p><p class="muted">Observed dialog: <code>${esc(observedClass)}</code> <code>${esc(observedTitle)}</code></p><div class="table-wrap"><table><thead><tr><th>Field</th><th>Suggested value</th><th>Confidence</th></tr></thead><tbody>${rows}</tbody></table></div>${missingNote}${applyBtn}<h4>Suggested titles (copy into the Account form, NOT auto-applied)</h4>${titleRow("window_title_regex", titles.window_title_regex)}${titleRow("success_window_title_regex", titles.success_window_title_regex)}<p class="muted">These titles are suggestions to copy into the Account form, NOT auto-applied.</p><h4>Menu captions (read-only reference)</h4><p class="muted">${captions.length ? captions.map((c) => `<code>${esc(c)}</code>`).join(" ") : "—"}</p><p class="muted">These are this build's own menu captions; a different broker's wording will differ, and the project does not auto-extend its caption list from this.</p></div>`;
  }
  async function runWin32Inspect() { const accountId = $("#test-account").value; if (!accountId) { toast("请先选择 Account。", true); return; } try { const report = await api("/test/windows/win32-inspect", { method: "POST", body: JSON.stringify({ account_id: accountId }) }); renderWin32Inspector(report, accountId); toast(report.outcome === "inspected" ? "Win32 inspection 已完成，结果见下方" : `Win32 inspection: ${report.outcome}`); } catch (error) { toast(error.message, true); } }
  async function applyWin32Settings() { const accountId = $("#test-account").value; if (!accountId) { toast("请先选择 Account。", true); return; } if (!state.win32 || state.win32AccountId !== accountId || !state.win32.appliable) { toast("当前没有可应用的 Win32 suggestion，请先运行 Detect Win32。", true); return; } try { const result = await api("/test/windows/win32-apply", { method: "POST", body: JSON.stringify({ account_id: accountId, suggested: state.win32.suggested || {}, confidence: state.win32.confidence || {} }) }); if (!result.applied) { toast(result.reason || "Win32 settings 未应用", true); return; } const node = $("#win32-inspector"); if (node) node.innerHTML = `<div class="card"><div class="card-label">Win32 settings applied</div><p>Only the Win32 fallback fields were written. Alias, login id, server, terminal path and working directory were NOT touched. Please refresh the Account form (open the Account in Accounts to confirm).</p></div>`; toast("Win32 fallback 设置已应用"); await loadCatalog(); } catch (error) { toast(error.message, true); } }
  async function viewReport(filename) { const response = await fetch(`/api/test/windows/report/${filename}`, { headers: adminToken ? { "X-Admin-Token": adminToken } : {} }); if (!response.ok) { toast("报告下载失败", true); return; } const blob = await response.blob(); const url = URL.createObjectURL(blob); const link = document.createElement("a"); link.href = url; link.download = filename; link.click(); URL.revokeObjectURL(url); }
  async function selectTestReport(reportId) { try { const report = await api(`/test/windows/report/${encodeURIComponent(reportId)}/select`, { method: "POST" }); renderTestStatus({ ...report, results: report.results || [], detected_controls: report.detected_controls || [], windows_available: true, report_id: reportId }); toast("已选择历史报告"); } catch (error) { toast(error.message, true); } }
  document.addEventListener("click", (event) => { const tab = event.target.closest("[data-tab]"); if (tab) { showTab(tab.dataset.tab); if (tab.dataset.tab === "windows-test") loadTestStatus(); if (tab.dataset.tab === "history") loadHistory(); return; } const link = event.target.closest("[data-tab-link]"); if (link) { showTab(link.dataset.tabLink); if (link.dataset.tabLink === "history") loadHistory(); return; } const action = event.target.closest("[data-action]"); if (action) handleAction(action); });
  $("#slack-users").addEventListener("input", () => renderSlackBindings(state.slackBindings));
  const testAccountSelect = $("#test-account");
  if (testAccountSelect) testAccountSelect.addEventListener("change", () => { updateWin32Button(); if (state.win32AccountId && state.win32AccountId !== testAccountSelect.value) { state.win32 = null; state.win32AccountId = ""; const inspector = $("#win32-inspector"); if (inspector) inspector.innerHTML = ""; } });
  updateWin32Button();
  $("#account-form").addEventListener("submit", saveAccount); $("#account-alias").addEventListener("input", updateCommandPreview); $("#group-form").addEventListener("submit", saveGroup); $("#slack-form").addEventListener("submit", saveSlack);
  async function refreshDashboardIfVisible() { if (document.visibilityState !== "visible" || !adminToken) return; try { renderDashboard(await api("/dashboard")); } catch (error) { if (error.message !== "Local admin token required") console.debug("dashboard refresh failed"); } }
  setInterval(refreshDashboardIfVisible, 5000);
  document.addEventListener("visibilitychange", refreshDashboardIfVisible);
  (async () => { updateTokenBar(); if (!ensureAdminToken()) { toast("需要 start.bat 控制台中的 Local admin token 才能访问 Web Admin。", true); return; } try { await loadCatalog(); await loadSlack(); await loadTestStatus(); await loadHistory(); } catch (error) { toast(`无法连接本地 Agent：${error.message}`, true); } })();
})();
