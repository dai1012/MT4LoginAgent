# Windows + Rakuten MT4 实机验收清单

**状态：WINDOWS_REAL_TEST_REQUIRED**

本文件是最终实机 gate。当前 macOS/Linux 环境和自动化测试没有证明任何真实 Rakuten MT4 登录成功；不要把 Mock、Web contract 或静态检查结果当作实机证据。

## 自动化 Test Runner 入口

在 Windows 上双击：

```text
test-windows.bat
```

也可以打开已有 Web Admin 的 **Windows Test** 页面。两者调用同一个 Test Runner Core。

```text
Phase 1 Environment / Safe Checks   AUTOMATED
Phase 2 MT4 Discovery / UIA         SEMI_AUTOMATED
Phase 3 Slack Safe Tests            SEMI_AUTOMATED
Phase 4 Internal Real Login         MANUAL CONFIRMATION
Phase 4 Full Slack E2E              SEMI_AUTOMATED / MANUAL
Phase 5 Group                       OPTIONAL / MANUAL CONFIRMATION
Destructive cases                   MANUAL_TEST_REQUIRED
```

Test Runner 默认不会输入 OTP、启动真实登录或向真实 Slack 批量发消息。Real Login 和 Group 必须在 Web UI 中明确确认；Full Slack E2E 由用户自己发送 `/mt4 A <OTP>`，Test Runner 只等待并关联结果。

每次 Test Runner 运行会在 runtime data 目录生成 report.html、report.json 和 logs-sanitized.txt；只有在 Phase 2 实际执行 MT4/UIA Discovery 时，才会额外生成 uia-tree-sanitized.json：

```text
reports/<run-id>/report.html
reports/<run-id>/report.json
reports/<run-id>/logs-sanitized.txt
reports/<run-id>/uia-tree-sanitized.json   # 仅 Phase 2 实际发现时
```

报告不包含 OTP、Slack token、Local admin token、HMAC key 或未脱敏 Login ID。报告可以直接提供给开发者分析。

以下人工清单仍需按本文件执行；Test Runner 会把它们标成 `MANUAL_TEST_REQUIRED`。

## 0. 安全准备

- [ ] 使用专用 Windows 本地账户和专用测试/观察 MT4 账户。
- [ ] 确认 `%LOCALAPPDATA%\RakutenMT4Agent` 只允许该账户访问。
- [ ] 不把真实 Login ID、OTP、token、原始 Slack payload 或异常 dump 放进 Git、issue、截图或录屏。
- [ ] 确认没有其他 Agent 进程操作同一 Profile。
- [ ] 记录 Windows 版本、Python 版本、Rakuten MT4 版本、terminal.exe 路径和 Profile 路径（不含凭据）。

## 1. 自动化测试已验证的项目

这些项目已在本机 macOS/Linux 环境用 pytest 覆盖，但不代表 Windows UI 真实成功：

- [x] Account/Group CRUD、alias 冲突、删除回滚。
- [x] Slack command 解析、allowlist 顺序、重复 delivery digest、ack 失败释放 key。
- [x] 队列容量、Account pending 防重、排队期间 disable/delete/live reload、OTP 过期。
- [x] History schema、OTP 脱敏、raw Slack body/异常 dump 脱敏。
- [x] Web Local admin token（含非 ASCII header 拒绝）、Origin/Host/body limit、write-only secrets。
- [x] Windows adapter 静态 contract：spawn worker、显式 UIA selector、无位置/坐标/键盘 fallback、Profile 歧义 fail closed、认证窗口新建/变化、密码掩码回读、TogglePattern、Server SelectionItem、进程枚举 fail-closed。
- [x] 双 Python 版本 pytest、Ruff、compileall、wheel、Web localhost smoke；每次发布前以实际输出中的用例数为准。

## 2. 第一次安装和 Web Admin

- [ ] `git clone` 或解压完整 `MT4LoginAgent/`。
- [ ] 双击 `install.bat`；记录 Python 版本，确认 import self-check 通过（脚本严格按 3.12→3.11 选择）。
- [ ] 确认 `install.bat`/`start.bat` 在 Windows 上以 CRLF 执行正常。
- [ ] 双击 `start.bat`。
- [ ] 确认控制台显示 Local admin token；不要把它写入文件或 URL。
- [ ] 浏览器打开 `http://127.0.0.1:8765`，输入 token。
- [ ] Dashboard、Accounts、Groups、Slack、History 页面均可打开。
- [ ] 确认错误配置能显示具体 check 名称，而不是 `[object Object]`。
- [ ] 确认端口占用、venv 失效、settings.json 损坏时窗口不会闪退，错误可见。
- [ ] 停止 Agent 后确认 Web/Slack 断开，MT4 已运行终端不被 Agent 终止。
- [ ] 第二次用同一 data-dir 启动时得到明确的单实例错误，而不是两个 Agent 同时监听。

## 3. Slack App

- [ ] 导入 `slack-app-manifest.yaml` 并安装到目标 Workspace。
- [ ] 开启 Socket Mode，生成 `xapp-...` App-level token（`connections:write`）。
- [ ] 生成/安装 `xoxb-...` Bot token。
- [ ] 在 Web Admin → Slack 填入两个 token 和 Allowed User IDs。
- [ ] Connection Test 显示 App token、Bot token、Workspace/Team 信息。
- [ ] 故意填错一个 token，确认页面显示“已保存但尚未连接”，且不会假报 Connected。
- [ ] 确认 Bot 已加入目标频道或私聊。
- [ ] 重启 Agent，确认 Socket Mode 自动重连。
- [ ] 让一个未授权 User 执行 `/mt4 status`，确认回执显示其 User ID 且没有解析 OTP。

## 4. Account 配置 gate

Windows 上以下字段全部是硬性要求：

- [ ] `terminal_path` 是存在的绝对 `.exe`。
- [ ] `profile_path` 存在、绝对、可唯一区分目标 MT4 实例。
- [ ] `process_name` 与真实进程一致（辅助诊断，不替代 Profile）。
- [ ] `window_title_regex` 明确匹配登录对话框。
- [ ] `control_ids` 至少有唯一且不同的 `login_id`、`otp`、`server`、`login_button` Automation ID。
- [ ] `success_window_title_regex` 已锚定（`^...$`），只匹配认证主窗口，不匹配错误/维护/服务器提示。
- [ ] `save_login_info` 保持关闭，除非已证明不会保存 OTP。
- [ ] 点击 Account → Test 确认静态 checks 通过；该按钮不会启动 MT4 或连接 UIA，真实 Automation ID 仍需下方实机登录确认。

## 5. 单账号真实登录

准备本次真实 OTP 后执行：

- [ ] 开始真实 OTP 测试前，人工确认 Rakuten 当前不处于已知 maintenance/offline 时段；当前没有 broker preflight。
- [ ] `/mt4 A <OTP>` 立即收到“处理中”。
- [ ] Group 登录前确认 `shared_otp_confirmed`，只对已确认属于允许共享同一 OTP 的同一身份范围使用 Group。
- [ ] A 未运行时，Agent 启动正确 terminal.exe/Profile。
- [ ] A 已运行时，Agent 找到唯一正确进程/Profile；多个匹配必须报 ambiguous/instance unverifiable。
- [ ] Server 先于 Login ID/OTP 选择并回读一致。
- [ ] Login ID、OTP 通过 ValuePattern 写入并回读（密码字段允许空回读）；没有键盘模拟或坐标点击。
- [ ] Login button 通过 InvokePattern 激活。
- [ ] 错误 OTP 返回 `login_rejected` 或明确 unverified/timeout，不报告成功。
- [ ] 过期 OTP、broker offline、maintenance、账户冻结分别返回可区分 category。
- [ ] 登录成功后必须观察到匹配 success regex 的认证主窗口（新建窗口或状态变化）；新出现的错误/维护窗口不能算成功。
- [ ] 成功回执包含 alias、状态和耗时；不包含 OTP/Login ID。
- [ ] History 有成功记录，字段中没有 OTP。

## 6. 失败、超时、Profile 和进程

- [ ] 登录窗口延迟出现时，Agent 等待而不是选择早期错误框。
- [ ] 登录窗口关闭但没有认证窗口状态转换时，返回 `ui_verification_unverified`。
- [ ] 无响应 UIA provider 达到预算后，spawn worker 被 terminate/kill；不继续填表。
- [ ] 关闭 Agent 时子进程被回收，但 MT4 进程不被终止。
- [ ] cwd 不可读、Profile 不匹配、多个相同 executable 时 fail closed，不重复启动终端。
- [ ] 32/64 位 Python、非管理员 Agent、管理员 MT4、UAC 场景记录结果。
- [ ] 8.3 短路径、junction、Unicode/日文窗口标题各测试一次。
- [ ] 失败后截图确认 Login ID/OTP 字段没有残留；不要把截图上传到公共位置。
- [ ] 若 worker 被硬终止导致无法清理字段，人工关闭/隐藏登录窗口并轮换该一次性 OTP；该 UI 残留是已记录的 Windows 边界。

## 7. Group 顺序和部分失败

- [ ] 创建 `GROUP1`，成员顺序为 A → B → C。
- [ ] 明确确认这些成员属于允许共享同一 Rakuten OTP 的同一身份范围，并设置 `shared_otp_confirmed=true`；否则 Group 登录必须被拒绝。
- [ ] 开始真实 Group OTP 测试前，人工确认 Rakuten 不处于已知 maintenance/offline 时段；当前没有 broker preflight。
- [ ] `/mt4 GROUP1 <OTP>` 按 A → B → C 执行。
- [ ] 让中间账号失败，确认后续账号仍执行。
- [ ] Slack 收到每个账号独立结果。
- [ ] History 每个账号一条记录。
- [ ] 排队期间修改/删除成员，确认只执行提交时原成员集合；新成员不会被越权加入。
- [ ] 验证队列深度上限为 16，OTP 排队超过有效期时返回 `otp_expired`，不会继续点击 MT4。
- [ ] 禁用成员时返回 `account_disabled`，不启动其 MT4。

## 8. Security/文件/网络

- [ ] `agent.log`、`history.jsonl`、`accounts.json` 中搜索本次 OTP，结果为 0。
- [ ] 浏览器 DOM、API 响应、Slack SDK debug 输出中没有 token/OTP。
- [ ] `secrets.json`、`.tmp`、`dedup.json`、`.bak`、`.swp`、`reports/` 均不会进入 Git。
- [ ] Test Runner 报告目录只允许本机验收用户读取；报告中没有 OTP、token、HMAC key 或密码明文。
- [ ] 确认 `dedup.json` 只有摘要，没有原始 OTP。
- [ ] 确认 Web 只监听 `127.0.0.1`，非 localhost Host 被拒绝。
- [ ] 确认 Origin 跨源写请求被拒绝，Local admin token 缺失时 API 返回 401。
- [ ] 出站连接只看到 Slack 官方服务和 MT4 原有连接；无 analytics/telemetry/AI endpoint。
- [ ] 核对 Windows ACL、磁盘加密和备份策略。

## 9. 仍需实机判断的项目

- 真实 Login/OTP/Server/Login button 的 Automation ID、名称、control type。
- ValuePattern/InvokePattern 是否可用。
- 真实登录窗口和认证主窗口标题/状态变化。
- Rakuten 券商的具体失败/维护/offline 文案。
- spawn 子进程中的 COM apartment 行为。
- DPI、UAC、32/64 位和 Profile cwd 组合。
- 真实 Slack App/Command/Channel 权限。
- Rakuten broker OTP 的签发时间、首次登录 2 分钟规则，以及同一身份多设备/多账户共享规则；`otp_max_age_seconds` 只用于 Agent stale-request cutoff。

任何一项无法确认时，保持 `WINDOWS_REAL_TEST_REQUIRED`，不要手动放宽为 success。
