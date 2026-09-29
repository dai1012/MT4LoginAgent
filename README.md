# Rakuten MT4 Remote Login Agent V1

一个运行在 Windows 上、源码可审计的 Rakuten MT4 远程登录 Agent。

授权用户从 Slack 发送：

```text
/mt4 A 123456
```

其中 `A` 是本机 Web Admin 中配置的 alias，不是真实 MT4 Login ID；`123456` 是本次一次性 OTP。Agent 会在本机找到对应 MT4，使用本机保存的 Login ID/Server 和本次 OTP 完成登录，并把结果回复 Slack。

> **重要边界**：当前开发环境不是 Windows，也没有真实 Rakuten MT4。非 Windows 使用明确标记的 Mock；真实 Windows UIA、控件、Profile、broker 和 Slack 连接都必须标记为 `WINDOWS_REAL_TEST_REQUIRED`，不能把静态/mock 结果当成真实登录证据。

## 0. 给第一次使用者：最短上手路径

这个程序运行在你的 Windows 电脑上，把 Slack 收到的一次性 OTP 交给本机 Rakuten MT4，自动完成一次登录，并把结果回复 Slack。

**为什么需要 Slack？** 登录 OTP 是短时有效的，Slack 负责把“账号别名 + OTP”送到 Agent；Agent 不需要公网服务器，也不需要你一直打开 Windows 浏览器窗口。

```text
Slack
  │  /mt4 A 123456
  ▼
Windows MT4 Login Agent（本机）
  │  UIA 控制对应 Profile 的 Rakuten MT4
  ▼
Rakuten MT4
  │
  └── 登录结果回复 Slack

Windows 本机浏览器
  │  http://127.0.0.1:8765 + Local admin token
  ▼
Web Admin
  ├── Account / Alias
  ├── Group（多个账号按顺序执行）
  ├── Slack token / allowlist
  └── History（不保存 OTP）
```

第一次使用只做这几步：

1. 把整个 `MT4LoginAgent/` 文件夹复制到 Windows。
2. 安装 Python 3.12（推荐）或 3.11 和 Python Launcher `py.exe`。
3. 双击 [`install.bat`](install.bat)，等待依赖和 self-check 完成。
4. 双击 [`start.bat`](start.bat)，记下控制台显示的 **Local admin token**。
5. 浏览器打开 `http://127.0.0.1:8765`，输入 token；默认端口被占用时按控制台提示修改 `settings.json` 或使用 `--port`。
6. 在 Web Admin 的 **Accounts** 添加一个账号。`Alias`（例如 `A`）是 Slack 中使用的短名称，不是真实 Login ID。
7. 在 **Groups** 中把多个账号组成登录顺序；不需要批量登录时可以跳过。
8. 在 **Slack** 页面创建/导入 [`slack-app-manifest.yaml`](slack-app-manifest.yaml)，填入 `xapp-` App-level token、`xoxb-` Bot token 和你的 Slack Member ID，点击 **保存并重连**。
9. 在 Slack 发送：

   ```text
   /mt4 A 123456
   /mt4 status
   ```

`123456` 只是本次命令中的一次性 OTP。Agent 不把 OTP 写入 History、配置文件或日志；History 只记录时间、alias、结果和耗时。停止程序是在 `start.bat` 窗口按 `Ctrl+C`；卸载步骤见下方。

> 当前环境尚未做真实 Windows/Rakuten 验证。所有真实控件、Profile、broker、Slack 权限和登录结果都必须按 [`docs/WINDOWS-MT4-TEST.md`](docs/WINDOWS-MT4-TEST.md) 验证，不能把 Mock 或静态检查当成真实登录成功。

## 1. 支持范围

### 支持

- Slack Bolt Socket Mode，无需公网 HTTP endpoint。
- `/mt4 <alias> <OTP>`、`/mt4 <group> <OTP>`、`/mt4 status`。
- Slack User ID allowlist、重连、重复 delivery 去重。
- 多 Account/Group CRUD、顺序执行、部分失败继续。
- Windows terminal.exe 启动/复用、Profile 匹配、多实例 fail closed。
- pywinauto UIA 控件定位、ValuePattern/InvokePattern。
- FastAPI localhost Web Admin、Account 验证、Slack token 管理、append-only History。
- JSON/JSONL 原子本地存储，无 Docker/Redis/外部数据库。

### 不支持/明确 fail closed

- 不支持 Docker、云服务器、Tailscale、Cloudflare Tunnel。
- 不支持未授权 Slack sender。
- 不支持无显式 UIA selector 的 Login/OTP/Server/Login button。
- 不支持通过 index/坐标猜测控件，也不使用键盘模拟 fallback。
- 不支持把“登录窗口消失”直接当作成功；必须有锚定认证窗口状态转换（新建认证主窗口也必须匹配该表达式）。
- 不支持在 Profile 不可验证时向不确定的 MT4 实例输入凭据。
- 不支持多 Agent 进程同时驱动同一个 Windows Profile。

## 2. 架构

```text
Slack Workspace
      │  Socket Mode WebSocket（无需公网 HTTP）
      ▼
SlackGateway ──► SlackCommandProcessor
      │                 │  allowlist / dedup / LoginRequest(SecretStr)
      │                 ▼
      │            LoginService（串行队列、Group、live reload、History）
      │                 │
      │                 ├──► MockAutomation（非 Windows）
      │                 │
      │                 └──► WindowsAutomation
      │                        └──► spawn worker ──► pywinauto UIA ──► Rakuten MT4
      │
      └──► Web Admin（127.0.0.1 + Local admin token）
                  ├── Accounts / Groups
                  ├── Slack settings
                  └── OTP-free History
```

核心边界：

- `adapters/slack` 只负责 Slack 传输和命令策略。
- `mt4/login_service.py` 负责队列、目标授权、部分失败和 History。
- `mt4/windows_automation.py` 负责 Windows UIA；非 Windows 不会导入 Windows 包。
- Web API 需要 `X-Admin-Token`，token 由 `start.bat` 控制台显示。

## 3. Windows 第一次安装

1. 复制整个 `MT4LoginAgent/` 目录到 Windows，不要只复制某个脚本。
2. 安装 Python 3.12（推荐）或 3.11，并安装 Python Launcher `py.exe`；`install.bat` 只按 3.12→3.11 顺序选择，不会静默回退到任意 `py -3`。
3. 双击 `install.bat`。
   - 脚本使用 CRLF，`.gitattributes` 也固定了 Windows 批处理换行。
   - 脚本创建 `.venv`、安装依赖，并执行 import self-check。
4. 双击 `start.bat`。
5. 记下控制台输出的：

   ```text
   Web Admin local admin token (keep private): ...
   ```

6. 浏览器打开默认地址：

   ```text
   http://127.0.0.1:8765
   ```

7. 页面会要求输入 Local admin token。token 只保存在当前浏览器标签页的 `sessionStorage`，不会写入 URL 或 localStorage。
8. 浏览器只应使用 `127.0.0.1`；不要把端口改成 `0.0.0.0` 或暴露到 LAN。

如果 8765 被占用，程序会明确提示；可以编辑数据目录 `settings.json` 的 `web_port`，或本次运行使用 `--port`（`--port` 只对本次进程生效，不写回配置）。

## 4. Slack 首次设置

### 4.1 创建 App 和导入 Manifest

1. 在目标 Slack Workspace 打开 [Slack App 管理](https://api.slack.com/apps)，创建 App。
2. 进入 **App Manifests**，粘贴：

   ```text
   slack-app-manifest.yaml
   ```

3. 创建并安装到 Workspace。
4. Manifest 开启 `socket_mode_enabled`，因此不需要公网、内网或 localhost HTTP endpoint。

### 4.2 获取 App-level token

在 **Settings → Socket Mode** 开启 Socket Mode，生成 App-level token：

- 以 `xapp-` 开头。
- 需要 `connections:write`。

### 4.3 获取 Bot token

在 **OAuth & Permissions** 安装应用到 Workspace，生成 Bot token：

- 以 `xoxb-` 开头。
- Manifest 已申请 `commands` 和 `chat:write`。

### 4.4 获取自己的 Slack User ID

在 Slack 中打开个人头像/资料，复制 **Member ID**，格式类似：

```text
U0123456789
```

不要填写 display name、`@handle` 或 Channel ID。未授权用户发送命令时，Agent 会把自己的 User ID 回显出来，便于加入 allowlist。

### 4.5 填入 Web Admin

打开 **Slack** 页面：

- 填入 `xapp-...` 和 `xoxb-...`。
- 每行填写一个允许的 Slack User ID。
- 可以勾选清除已经保存的 token。
- 点击 **保存并重连**。
- 点击 **Connection Test**，确认 App token、Bot token 和 Workspace。

Bot token 正确不代表 `/mt4` 已注册；如果命令没有反应，检查 Manifest、Slash Command 和 Bot 是否已加入频道/私聊。

## 5. Web Admin 使用

### Dashboard

显示 Agent、Slack、平台、自动化模式、任务队列、Account 进程状态、配置问题和最近结果。

### Account

点击 **新增 Account**，Windows 上必须填写：

- Name：显示名称。
- Alias：Slack 命令使用的本地 alias。
- Login ID：只保存在本机，不通过 Slack 发送。
- Server：MT4 Server。
- MT4 executable path：Windows 绝对路径。
- Profile path：用于区分同一台机器上的多个 MT4 实例。
- Login window title regex：明确登录窗口，例如 `(?i)^.*login.*$`。
- Authenticated main-window title regex：必须锚定，例如 `^Rakuten .*authenticated$`。
- UIA `control_ids`：至少 `login_id`、`otp`、`server`、`login_button` 四个明确 Automation ID。
- `save_login_info` 默认关闭；只有确认 UI 不会保存 OTP 时才打开。若 Windows worker 被硬终止，登录窗口可能暂时保留已填字段，需人工关闭并轮换 OTP。

`control_titles` 只用于非关键字段（例如 Save checkbox）；关键登录字段不接受模糊标题匹配。

保存前可以点击 **Test**，但它只是静态配置检查，不会启动 MT4 或连接 UIA；真实 Automation ID、control type 和窗口状态仍需实机验证。Windows 缺少 Profile、selector 或锚定 success regex 时，服务端会返回具体失败项，而不是只显示“无效配置”。

### Group

- Group 名称不能和 Account alias 重复。
- Group 成员顺序就是 Slack 执行顺序。
- Group 默认不能执行：必须明确勾选 **shared_otp_confirmed**，确认所有成员确实属于允许共享同一 Rakuten OTP 的同一身份范围。Agent 不会猜测多设备/多账户共享规则。
- 编辑 Group 时，成员列表会保留原顺序；保存后可使用卡片上的 ↑/↓ 调整。
- 禁用成员仍可保留在 Group 中，但执行时会明确返回 `account_disabled`。
- 排队期间修改 Group 不会把新成员加入已经提交的任务；已提交任务只执行原成员集合，并对每个成员执行前 live reload。
- Agent 最多同时保留 16 个登录 job；`otp_max_age_seconds` 是 Agent stale-request cutoff（默认 120 秒、上限 300 秒），不是 Rakuten broker OTP 有效期。排队过久的任务会明确返回 `otp_expired`。
- 旧 schema 允许 `otp_max_age_seconds` 到 900。升级后加载 `settings.json` 时，301..900 的旧值会被下调到上限 300 并在 `agent.log` 打印警告，不会阻止 Agent 启动；超出旧 schema 区间的值仍会直接报错，不会被静默改写。

### History

History 是 append-only/read-only 审计记录，只显示：

- 时间
- sender
- target/alias
- success/failed
- error category
- duration

不显示 OTP、原始异常文本或 token。页面显示最近 500 条，长期运行的数据文件不会自动删除；需要保留策略时应先备份再人工轮换。

## 6. Slack 命令

```text
/mt4 status
/mt4 A 123456
/mt4 GROUP1 123456
```

- `/mt4 A 123456`：`A` 是本地 alias，OTP 是本次命令中的数字。
- `/mt4 GROUP1 123456`：按 Group 顺序执行。
- `/mt4 status`：查看 Agent/Slack/队列状态。
- allowlist 在解析 OTP 前检查。
- 同一 Slack delivery 的重复投递会被进程内和本地 digest guard 抑制。
- 同一账号已有登录任务时，新的不同命令会被拒绝，而不是并发操作同一窗口。

## 7. Windows Acceptance Test Runner

Windows 拿到机器后，不必再手工逐条执行几十项检查。双击：

```text
test-windows.bat
```

它会启动同一个 Web Admin，并打开 **Windows Test** 页面；也可以在已运行的 Web Admin 中直接进入该页面。CLI 和 Web 页面调用同一个 Test Runner Core。

### 阶段

1. **Phase 1 — Environment / Safe Checks**：不需要 OTP。检查 Windows、Python、pywinauto、UIA、COM、data-dir、日志、Web 配置、Account 静态配置、Slack 配置和本地 token 状态。
2. **Phase 2 — MT4 Discovery / UIA Inspection**：选择 Account，不输入 OTP。检查 terminal/Profile/进程，枚举窗口和 Login ID、OTP、Server、Login button、Save checkbox 候选控件，生成 `uia-tree-sanitized.json`。
3. **Phase 3 — Slack Safe Tests**：检查 token、Socket Mode、Bot identity、allowlist、malformed/unknown/duplicate 路径。不会向真实 Slack 批量发消息；需要真实命令时显示 `ACTION REQUIRED`。
4. **Phase 4 — Real Login E2E**：必须勾选确认并输入一次性 OTP；默认只走本机 Login Core → MT4。Full Slack E2E 不会由 Test Runner 自动发送 OTP，而是让你手动发送 `/mt4 A <OTP>`，再用 session id 等待结果。
5. **Phase 5 — Group Test（可选）**：按 Group 顺序执行，逐个显示结果，支持部分失败；必须先明确设置 `shared_otp_confirmed`，不会默认运行。

### 不会自动执行的项目

以下项目显示为 `MANUAL_TEST_REQUIRED`，只在专用测试环境由用户明确执行：

- 故意输入错误 OTP；
- 等待 OTP 过期；
- kill MT4/worker；
- 制造多个 terminal.exe；
- 改系统时间；
- UAC/管理员切换；
- broker maintenance；
- 网络中断。

### 报告位置

报告写在 runtime data 目录，不写在源码目录。每次运行生成 HTML/JSON/log；只有实际执行 Phase 2 MT4/UIA Discovery 时才额外生成 `uia-tree-sanitized.json`：

```text
<data-dir>/reports/<run-id>/report.html
<data-dir>/reports/<run-id>/report.json
<data-dir>/reports/<run-id>/logs-sanitized.txt
<data-dir>/reports/<run-id>/uia-tree-sanitized.json   # 仅 Phase 2 实际发现时
```

报告不包含 OTP、Slack token、Local admin token、HMAC key 或未脱敏的 Login ID。UIA diagnostic 不读取密码控件明文 value，并限制节点数量。报告可以交给开发者分析；清除时删除对应的 `<data-dir>/reports/<run-id>/` 目录即可。

## 8. 数据和 Secrets 位置

默认数据目录：

- Windows：`%LOCALAPPDATA%\RakutenMT4Agent`
- macOS/Linux：`$XDG_DATA_HOME/rakuten-mt4-agent` 或 `~/.local/share/rakuten-mt4-agent`

目录内容：

| 文件 | 内容 |
|---|---|
| `settings.json` | 非秘密设置和 Web 端口 |
| `secrets.json` | Slack App/Bot token、Web admin token、dedup HMAC key |
| `accounts.json` | Account 本地配置，包括 Login ID |
| `groups.json` | Group 成员和顺序 |
| `history.jsonl` | OTP-free 审计记录 |
| `dedup.json` | 仅保存去重摘要，不保存原始 OTP |
| `logs/agent.log` | 已脱敏日志 |
| `reports/` | Windows Acceptance Test 的 HTML/JSON/diagnostic 报告 |
| `agent.lock` | 单实例锁文件，不应手工删除正在运行中的锁 |

这些文件已被 `.gitignore` 排除。不要把自定义 data-dir 放在 Git worktree 中而不加入 `.gitignore`。

Secrets 安全边界：

- V1 使用独立 `secrets.json`，不接入 Windows Credential Manager。
- 程序尽力设置 POSIX mode；Windows 实际 ACL 依赖 `%LOCALAPPDATA%` 继承权限。
- 备份、OneDrive、Time Machine 可能复制 secrets，请使用受控磁盘/备份策略。
- 丢失 admin token：停止 Agent，编辑 `secrets.json` 删除 `web_admin_token` 字段（保留 Slack token），重启后重新生成。
- 泄漏 Slack token：立即在 Slack 撤销并重新生成。

## 9. 停止、更新、卸载

### 停止

在 `start.bat` 控制台按 `Ctrl+C`。Agent 会停止 Slack/Web 调度，但不会主动终止已经运行的 MT4 终端。

### 更新

1. 停止 Agent。
2. 备份数据目录中的 `settings.json`、`accounts.json`、`groups.json` 和需要保留的 History。
3. 用新版本源码替换程序文件，保留数据目录。
4. 重新运行 `install.bat`。
5. 运行 `start.bat`，重新确认 Local admin token 和 Slack Connected。

### 卸载

1. 停止 Agent。
2. 在 Slack 撤销 App 或至少撤销 token。
3. 删除项目目录和 `.venv`。
4. 删除 `%LOCALAPPDATA%\RakutenMT4Agent`（如需保留审计记录，先备份）。
5. Rakuten MT4 Profile、券商账户和 MT4 本身不属于本项目卸载范围。

## 10. 网络连接

运行时程序主动连接：

1. Slack 官方 Socket Mode WebSocket/API。
2. Rakuten MT4 自己的 broker/行情连接。
3. 本机浏览器访问 `127.0.0.1`。

安装阶段 `pip` 可能访问 Python 包索引。没有 telemetry、analytics、AI API、Docker、Redis 或云端自建服务。

## 11. 故障排查

| 现象 | 处理 |
|---|---|
| `py.exe was not found` | 安装 Python Launcher，重新运行 `install.bat` |
| Python 版本错误 | 安装 3.11+，重新运行 `install.bat` |
| `.venv` 失效/换机器后导入失败 | 删除项目内 `.venv`，重新运行 `install.bat` |
| 8765 被占用 | 修改数据目录 `settings.json` 的 `web_port`，或使用 `--port` |
| Another Agent instance is already using this data directory | 已有 Agent 使用同一 data-dir；关闭旧进程，不要删除正在使用的 `agent.lock` |
| `Cannot load Agent configuration` | `settings.json`/`accounts.json`/`groups.json` 损坏，从备份恢复或修复 JSON |
| 页面一直要求 token | 从 `start.bat` 控制台复制最新 Local admin token；不要粘贴到 URL |
| Slack Disconnected | 确认 xapp-/xoxb- 前缀、Socket Mode、App 安装、allowlist、Bot 是否在频道 |
| `/mt4` 没有反应 | 检查 Manifest Slash Command、Bot 权限和频道成员身份 |
| Account Test 失败 | 查看具体 check：Profile、绝对路径、UIA IDs、锚定 success regex |
| MT4 状态 ambiguous/instance unverifiable | 不要继续输入 OTP；先配置唯一 Profile 并确认只有一个目标进程 |
| UI 显示 unverified | 没有观察到认证主窗口状态转换，按 Windows 清单检查 selector/title，不要手动放行 |
| 需要日志 | 查看 `%LOCALAPPDATA%\RakutenMT4Agent\logs\agent.log`，日志已脱敏 |

## 12. 开发命令

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check src tests
.venv/bin/python -m app.main --data-dir ./.local-data
```

## 13. 仍需 Windows/Rakuten 实测的内容

以下项目没有在当前 macOS 环境被“验证”为真实成功：

- Rakuten MT4 登录窗口 Automation ID、名称和类型。
- ValuePattern/InvokePattern 是否可用。
- Profile 目录、进程 cwd、32/64 位和权限组合。
- 锚定认证主窗口标题和状态变化。
- 错误/过期 OTP、broker offline、maintenance、冻结账户文案。
- Slack 真实 App 安装、Socket Mode 重连和 Bot 频道权限。
- spawn 子进程中的 COM/pywinauto 行为和退出回收。

请使用 [`docs/WINDOWS-MT4-TEST.md`](docs/WINDOWS-MT4-TEST.md) 作为最终实机 checklist。
