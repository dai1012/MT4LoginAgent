# Windows 实机测试完整手册

> 面向**第一次接触本项目**的测试者。不需要读代码，照着做即可。
> 全流程只需要记住：**三个 bat 文件 + 一个 Web 页面**。
>
> 本手册是 [`docs/WINDOWS-MT4-TEST.md`](WINDOWS-MT4-TEST.md) 清单的完整展开版。
> 只想看勾选清单的，去那一份即可。

---

## 0. 全流程速览

```
第一次：  install.bat  →  start.bat  →  test-windows.bat
之后：    打开 Web Admin  →  Windows Test 页面  →  按 Step 1~5 逐步点
```

| Step | 名称 | 需要 OTP？ | 会登录真实 MT4？ |
|---|---|---|---|
| 1 | Environment | ❌ 否 | ❌ 否 |
| 2 | MT4 Detect | ❌ 否 | ❌ 否 |
| 3 | Slack | ❌ 否 | ❌ 否 |
| 4 | Real Login | ✅ **是（第一次用真实 OTP）** | ✅ 是 |
| 5 | Group | ✅ 是 | ✅ 是（多个账号） |

**铁律：第一次只测单个账号 A → 成功后再测 Full Slack E2E → 最后才测 Group。**

---

## 1. Windows 环境准备

### 1.1 安装 Python

- 安装 **Python 3.12（推荐）或 3.11**，来源 <https://www.python.org/downloads/windows/>
- 安装时**必须勾选** `Python Launcher (py.exe)`，以及 `Add python.exe to PATH`
- `install.bat` 会**严格按 `py -3.12` → `py -3.11` 的顺序**挑选解释器；
  两者都找不到就报错退出，**不会**静默回退到系统里任意一个 `py -3`
  （这是刻意设计：避免装到未验证的 3.13/3.14 组合上）

### 1.2 复制项目

把**整个 `MT4LoginAgent/` 目录**复制到 Windows（`git clone` 或直接拷贝文件夹）。

> ⚠️ 不要只拷贝单个脚本。`install.bat`、`start.bat`、`test-windows.bat`、
> `slack-app-manifest.yaml`、`config/examples/`、`src/` 缺一不可。

### 1.3 准备测试用的 MT4

- 一个**安全的测试 / 观察用** Rakuten MT4 账户
- 记录它的 `terminal.exe` 完整路径和 Profile 路径（Phase 2 需要）
- 确认该账户当前**不在**券商 maintenance / offline 时段（Agent 没有 broker
  preflight，这一步只能人工确认）

---

## 2. 第一次运行：三个 bat

### 2.1 `install.bat`

双击运行。脚本会创建 `.venv`、安装依赖、最后跑一次 self-check。

- 看到 `self-check: ok` 才算成功
- 看到 `[ERROR]` 就停下，把完整控制台输出保存下来（见 §9）

### 2.2 `start.bat`

启动 Agent。**控制台会打印一行 Web Admin token**：

```
Web Admin local admin token (keep private): <一串字符>
```

> 🔒 这串 token 只保存在你本机。请勿截图发给他人。

### 2.3 `test-windows.bat`

它会做两件事：启动 Agent 并**自动打开浏览器**到 Windows Test 页面。

- 默认地址 `http://127.0.0.1:8765/#windows-test`
- 如果你改过 `settings.json` 里的 `web_port`，脚本会读取实际端口，不会开错地址
- 浏览器打开后点右上角 **输入 token**，粘贴 §2.2 那串 token

> token 只存在当前浏览器标签页的 sessionStorage，关闭标签页即失效，重新粘贴即可。

---

## 3. 配置 Web Admin

打开左侧导航的 **Accounts** 标签。

### 3.1 新增 Account

| 字段 | 是否必填 | 怎么填 |
|---|---|---|
| Name | 必填 | 人类可读名，如 `Rakuten-A` |
| **Alias** | 必填 | Slack 里用的短名，如 `A`。**不能是 4~10 位纯数字**（会和 OTP 混淆） |
| Login ID | 必填 | MT4 登录 ID |
| Server | 必填 | 如 `RakutenMT4` |
| **MT4 executable path** | 必填 | `C:\Program Files\Rakuten\terminal.exe` 这样的绝对路径 |
| Process name | 可选 | 一般填 `terminal.exe` |
| **Process working directory (cwd)** | Windows 必填 | MT4 进程的当前工作目录绝对路径，**必须能唯一区分这个实例**。代码与 psutil `cwd()` 比较；**不是** MT4 Data Folder。Rakuten MT4 通常填 `C:\Program Files (x86)\Rakuten MetaTrader 4` |
| **Login window title regex** | Windows 必填 | 匹配登录对话框标题，如 `(?i)^.*login.*$` |
| **Success window title regex** | Windows 必填 | 匹配认证成功后的主窗口，**必须锚定**，如 `^Rakuten .* authenticated$` |
| Launch arguments | 可选 | JSON 数组，如 `["/portable"]` |
| **UIA control IDs** | Windows 必填 | 见 §3.2 |
| UIA control titles | 可选 | 非关键字段的标题映射 |
| Save login information | **保持关闭** | 打开可能让 MT4 保存 OTP |
| Enabled | 保持勾选 | 取消勾选会让该账号被跳过 |

保存后，在列表里点 **Test**，确认 Config 显示 `valid`。
若显示 `invalid`，Issues 列会写明缺哪一项。

### 3.2 UIA control IDs 怎么填

Windows 上是**硬性要求**，至少需要这四个键：

```json
{
  "login_id":    "loginIdEdit",
  "otp":         "passwordEdit",
  "server":      "serverCombo",
  "login_button": "loginButton"
}
```

**你一开始填不出真实值也没关系** —— Step 2 会帮你检测。流程是：
先随便填，然后做 Step 2，再点 **Apply detected selectors** 自动回填。

### 3.3 新增 Group（可选，Step 5 才需要）

- **Name**：如 `GROUP1`
- **Members**：按顺序点选，**顺序即执行顺序**
- **确认所有成员共享同一 Rakuten OTP**：勾选后写入 `shared_otp_confirmed=true`
  - **不勾选则 Group 登录会被直接拒绝**，并提示 Agent 不会猜测多账户共享规则
  - 只在你确实确认这些成员属于允许共享同一 OTP 的同一身份范围时才勾选

---

## 4. 配置 Slack（Step 3 需要）

### 4.1 导入 Manifest

1. 打开 <https://api.slack.com/apps> → **Create New App** → 选 **From an app manifest**
2. 粘贴本项目 `slack-app-manifest.yaml` 的内容
3. Create

### 4.2 生成两个 token（都要）

| Token | 在哪生成 | 作用 | 需要的 scope |
|---|---|---|---|
| `xapp-` | Basic Information → App-Level Tokens | Socket Mode 长连接 | `connections:write` |
| `xoxb-` | OAuth & Permissions → Install to Workspace | 发消息、收命令 | `commands`、`chat:write` |

### 4.3 Bot 加入频道

- `OAuth & Permissions` → Install to Workspace，把 Bot 邀请进你要发命令的那个频道（或 DM）

### 4.4 在 Web Admin 填入

左侧 **Slack** 标签：

- Enable Slack Socket Mode：勾选
- App-level token：填 `xapp-...`
- Bot token：填 `xoxb-...`
- **Allowed Slack User IDs**：每行一个，填**你自己**的 Slack Member ID
  （点你的头像 → 复制 Member ID，形如 `U0123456789`）
- 点 **Connection Test** → 显示 `Connected` 才算成功
- 点 **保存并重连**

> 🔒 token 只写入本机 secrets 文件，页面**不会回显**原值。不要把 secrets 文件或含 token 的截图发给别人。

---

## 4b. 当 UIA 看不到登录框：Win32 dialog fallback

某些券商的登录框是普通 Win32 `#32770` 对话框，UI Automation **完全看不到**它
（`Desktop.windows()` 只返回主窗口，UIA descendants 里也没有登录控件）。
这时启用 Account 表单底部的 **Win32 dialog fallback**：

| 字段 | 说明 | Rakuten 实测值（仅供参考，需自行确认） |
| --- | --- | --- |
| 启用 | 默认关闭。UIA 路线能工作时不要开 | 关闭 |
| Dialog class | 登录框的原生窗口类 | `#32770` |
| Anchor texts | 逗号分隔，用来确认这是登录框而不是同标题的其它对话框 | `ログインID :, サーバー :` |
| Login ID ComboBox id | 承载 Login ID 的 ComboBox | `1181` |
| Login ID Edit id | 上面 ComboBox 里的子 Edit（**必须锚定父级**，不要全局匹配） | `1001` |
| Password / OTP Edit id | 可见的凭据输入框 | `1220` |
| Server ComboBox id | 券商下拉 | `1293` |
| Server Edit id | 下拉里的子 Edit | `1001` |
| Login Button id | 登录按钮（Cancel 通常是 2） | `1` |

要点：

- **两个 `Edit` 的 id 都是 `1001`**（Login ID 的和 Server 的），代码分别以各自 ComboBox 为父级锚定解析，
  不会串错；如果你看到 Login ID 出现在 Server 栏，说明 id 填错了。
- **不要填 hidden 的「ワンタイムパスワード」框**。本项目的 `otp` 语义就是 Slack 那次一次性 OTP 填进
  **凭据栏（パスワード）**，因为 Account 根本不保存密码。填错栏位等于用错字段。
- 启用后 Phase 2 会多出一条 `WIN32_DIALOG_DISCOVERY`：PASS 表示 dialog、pid、class、标题正则、
  anchor 和全部 control id 都成立；WARN 时 `technical_detail` 会写明是编程/环境错误还是前置不满足。
- 启用后 `Apply detected selectors` 不再可点 —— 这条路线用原生 id，不需要 UIA Automation ID。
- 页面顶部 Step 条会直接写明"当前 Real Login 走 Win32 dialog fallback"。

## 5. Step 1 ~ Step 5 逐步操作

进入 **Windows Test** 页面。页面顶部有 5 步步骤条，会告诉你现在该做哪一步。

### Step 1 — Environment / Safe Checks

- **目的**：检查 Windows、Python、pywinauto、data-dir、Web、配置、Account 静态完整性
- **需要 OTP**：否
- **操作**：点 **Run Phase 1**
- **预期**：状态变成 PASS，步骤条提示进入 Step 2
- **失败怎么办**：
  - 缺 Python / pywinauto → 重跑 `install.bat`
  - Account 相关 FAIL → 回 §3.1 补字段后重跑
  - 端口被占用 → 改 `settings.json` 的 `web_port` 后重启 Agent

> 此阶段不通过，Step 2/3/4/5 都会被门控拦住。**先修好再往下。**

### Step 2 — MT4 Detect（UIA Discovery）

- **目的**：枚举进程与登录控件。UIA 找不到登录框时，会自动尝试你已启用的 Win32 dialog fallback。
  运行前请**先手动打开 MT4 登录窗口并保持可见** —— 登录框不是独立的 UIA 顶层窗口时，只有它真实存在才可能被枚举到。
- **需要 OTP**：否
- **操作**：
  1. 在 Account 下拉框里选你的测试账号
  2. 点 **Detect**
  3. 看 **`MT4_DISCOVERY_READY`** 这一条 —— 它才是"能不能继续"的唯一判据
- **看哪一条结果**：

  | `MT4_DISCOVERY_READY` | 含义 | 下一步 |
  | --- | --- | --- |
  | **PASS** + `route=uia` | 四个 UIA selector 都已解析 | 若有 **Apply detected selectors** 按钮就点它，再回 Accounts 页确认 `Test` 显示 `valid` |
  | **PASS** + `route=win32` | 走已启用的 Win32 dialog fallback | **不需要** Apply，直接进 Step 4 |
  | **WARN** + `route=none` | 没解析出任何可用路径 | 确认 MT4 登录窗可见后重跑 Detect；看 `WIN32_DIALOG_DISCOVERY` 的 `technical_detail` |

- **候选表不再会骗你**：以前即使什么都没找到也会列出 4 行 MANUAL，看起来像"已检测到"。
  现在找不到的字段会进 `missing_fields`，**不会**被列成 detected 行。
- **Step 条不再把"跑过"当"通过"**：只有 `route=uia` 或 `route=win32` 的 Step 2 才算可用。
- **失败怎么办**：
  - `INSTANCE_UNVERIFIABLE` → cwd 没填或填错，无法唯一确定是哪个 MT4 实例
  - `AMBIGUOUS_PROCESS` → 同时有多个匹配进程，关掉多余的 MT4
  - `TERMINAL_NOT_FOUND` → executable path 填错
  - `UI_CONTROL_NOT_FOUND` → 窗口标题 regex 没匹配上，用任务管理器确认窗口标题再改正则

### Step 3 — Slack Safe Tests

- **目的**：验证 Slack 命令链路
- **需要 OTP**：否。**不会**向真实 Slack 发送测试消息
- **操作**：点 **Run Phase 3**
- **预期**：PASS 后页面明确提示「现在可以进入真实 OTP 测试（Step 4）」
- **失败怎么办**：
  - `Slack disconnected` → 回 §4 检查 token、`Connection Test`、Bot 是否在频道里
  - 命令无反应 → 注意 Bot token 正确**不代表** `/mt4` 已注册

### Step 4 — Real Login（第一次需要真实 OTP）

> ⚠️ **REAL ACCOUNT ACTION。** 这会真的尝试登录 Rakuten MT4。

- **需要 OTP**：是 —— **整个流程第一次用到真实 OTP**
- **开始前必须全部满足**：
  - [ ] 已人工确认 Rakuten 当前**不**处于已知 maintenance / offline 时段
        （Agent **没有** broker preflight，只能人工确认）
  - [ ] 已选好测试 Account
  - [ ] 已准备好一个**全新未使用**的 OTP（OTP 一次性，用过即失效）
- **操作**：
  1. 勾选两个确认框（broker 确认 + Account 确认）
  2. 在 OTP 框输入本次 OTP
  3. 点 **Start real login test**
- **预期**：登录成功，History 出现记录，报告里对应项 PASS
- **Full Slack E2E（可选但推荐）**：
  1. 点 **Start Full Slack E2E**（**不会**自动发 OTP）
  2. 页面给出 `ACTION REQUIRED`：在受控频道**手动**发送 `/mt4 A <OTP>`
  3. 回到页面点 **Await Full Slack result**
- **失败怎么办**：见 §10 错误分类对照

### Step 5 — Group（最后才做）

> ⚠️ **不要在 Step 4 成功之前做这一步。**

- **需要 OTP**：是
- **开始前必须全部满足**：
  - [ ] Step 1/2/3 已通过
  - [ ] 该 Group 的 `shared_otp_confirmed` 已勾选
  - [ ] broker 确认框已勾选
  - [ ] Group 成员确认框已勾选
  - [ ] **已按成员数准备好足够的一次性 OTP**
- **操作**：选 Group → 勾三个确认框 → 输入 OTP → 点 **Start Group test**
- **预期**：按 Members 顺序依次登录，逐个显示结果，部分失败不中断其余
- **关于 `otp_max_age_seconds`**：
  这是 **Agent 侧的过期截断**（默认 120 秒、上限 300 秒），**不是券商 OTP 的有效期**。
  Group 是**排队顺序执行**的，后排成员可能因排队超期返回 `otp_expired`，
  **此时 Agent 不会接触 MT4**，换一个 OTP 重试即可。
  同一 Group 内**第一个成员登录成功后**，后续成员不再受这个截断拦截。

---

## 6. 报告在哪、怎么用

每次 Test Runner 运行都会在数据目录生成：

```
<data-dir>/reports/<run-id>/
├── report.html              # 人看的汇总页（点 View HTML）
├── report.json              # 机读结构化结果（点 Export JSON）
├── logs-sanitized.txt       # 脱敏后的运行日志
└── uia-tree-sanitized.json  # 仅当 Step 2 实际执行过 UIA Discovery 才生成
```

- 页面 **Reports** 区可以下载 `report.html` / `report.json`，也可以 Select 历史报告
- 顶层状态含义：
  - **PASS** —— 所有检查项都通过
  - **PARTIAL** —— 存在 WARN / MANUAL / NOT_RUN 项。**不等于失败**，表示有需要人工确认或尚未执行的项
  - **FAIL** —— 至少一项 FAIL
- macOS/Linux 上永远不可能显示真实 Windows 登录 PASS，只会显示 `WINDOWS_REAL_TEST_REQUIRED` 或 `NOT_RUN`

---

## 7. 出错了要收集什么

按这个清单收集，**不要连续浪费 OTP**：

1. **先停下，不要立刻再点一次 Real Login。** OTP 一次性，重试前先搞清楚原因
2. 完整控制台输出（`start.bat` / `test-windows.bat` 的窗口）
3. `report.json`（点 Export JSON）
4. `logs-sanitized.txt`
5. 若做过 Step 2，附上 `uia-tree-sanitized.json`
6. 环境信息：Windows 版本、`py -3.12 -V` 的输出、Rakuten MT4 版本
7. 复现步骤：卡在哪个 Step、点了什么、页面显示什么
8. 错误分类（报告里的 category 字段），对照 §10

> 🔒 收集时**不要**附带 OTP、token、含 token 的截图。报告和日志已脱敏，
> 但你的截图不一定。

---

## 8. 安全注意

- **OTP 只在内存中使用**，不写入日志、报告、History 或 accounts.json
- **Local admin token、Slack token** 只保存在本机 data-dir，不要外传
- **不要把含真实 token / secrets 的截图或文件发给别人**
- Agent 只监听 `127.0.0.1`，不对外网开放
- 建议用**专用测试账户**做真机测试，不要用日常交易账户
- Phase 4/5 前的两个/三个确认框是刻意设计的安全门，**不要为了图方便去改代码绕过**

---

## 9. 测试完成的验收标准

全部满足才算这一轮测试完成：

- [ ] Step 1 PASS
- [ ] Step 2 检测到候选控件，且 `Apply detected selectors` 后 Account `Test` 显示 `valid`
- [ ] Step 3 PASS，Slack `Connection Test` 显示 `Connected`
- [ ] Step 4 单账号 A 真实登录成功，History 有记录
- [ ] （推荐）Full Slack E2E 手动发 `/mt4 A <OTP>` 后 `Await` 拿到结果
- [ ] （可选）Step 5 Group 按顺序执行，部分失败不中断
- [ ] 报告中**没有** OTP / token 泄漏
- [ ] History 里能查到本次记录
- [ ] **无法真机确认的项，一律保持 `WINDOWS_REAL_TEST_REQUIRED`，不得手动改成 success**

---

## 10. Troubleshooting

### 安装 / 启动

| 现象 | 原因 | 处理 |
|---|---|---|
| `[ERROR] Python 3.12 or 3.11 was not found.` | 没装 3.12/3.11，或没装 Python Launcher | 装 3.12 并勾选 `py.exe` |
| `[ERROR] Installation failed` | 依赖装不上 | 确认网络可达 PyPI；重跑 `install.bat` |
| `Another Agent instance is already using this data directory` | 已有 Agent 在跑 | 关掉旧的 `start.bat` 窗口 |
| `Invalid settings.json` | 配置文件损坏 | 见 §11 旧配置说明 |
| 浏览器打不开 / 连接被拒 | 端口不对 | 看 `start.bat` 控制台实际端口；或查 `settings.json` 的 `web_port` |

### Slack

| 现象 | 原因 | 处理 |
|---|---|---|
| `Slack disconnected` | token 错 / Socket Mode 没开 / 重连失败 | 重新 `Connection Test`；确认 `xapp-` 有 `connections:write` |
| 发 `/mt4` 没反应 | Bot token 正确不代表命令已注册 | 确认 Bot 已进频道；重新 Install to Workspace |
| `未授权` / 立即拒绝 | 你的 User ID 不在 Allowed 列表 | 填自己的 Member ID 后保存并重连 |

### MT4 / UI 自动化

| 错误分类 | 含义 | 处理 |
|---|---|---|
| `instance_unverifiable` | 无法唯一确定是哪个 MT4 实例 | 填对 process cwd；关掉多余 MT4 |
| `ambiguous_process` | 匹配到多个进程 | 关掉多余的 terminal.exe |
| `terminal_not_found` | executable path 错 | 核对 `terminal.exe` 真实路径 |
| `mt4_launch_failed` | 启动 MT4 失败 | 检查路径、权限、是否被杀毒拦截 |
| `ui_control_not_found` | 找不到控件 | 改正则；重做 Step 2 并 Apply selectors |
| `ui_verification_unverified` | 登录后无法确证成功窗口 | 修正 `success_window_title_regex`（只锚定 broker/server 稳定特征，不要写具体账号 ID） |
| `already_running_no_login_window` | MT4 在跑但没有登录窗口 | 手动打开登录对话框后重试 |
| `ui_automation_error` | UIA 层异常 | 重启 Agent；确认非管理员运行时的 UAC 场景 |

### Phase 被拦 / 报告 PARTIAL

| 现象 | 原因 | 处理 |
|---|---|---|
| Step 2~5 按钮点不动 / 返回 `*_REQUIRED` | 前一阶段没完成 | 按顺序完成 Step 1 → 2 → 3 |
| Real Login 报 broker 确认 | 没勾 broker 确认框 | 人工确认券商非 maintenance 后勾选 |
| Group 被拒绝 | `shared_otp_confirmed` 未设置 | 回 §3.3 勾选并保存 |
| 报告 `PARTIAL` | 有 WARN / MANUAL / NOT_RUN | 展开报告逐项看；**PARTIAL 不等于失败** |
| `otp_expired` | 排队超过 Agent 截断 | 换新 OTP 重试；不是券商 OTP 失效 |

---

## 11. 旧配置升级说明

`otp_max_age_seconds` 的语义和上限都变过：

- **旧版本**允许 30~900 秒
- **当前版本**默认 **120 秒**、上限 **300 秒**，且明确它只是 **Agent 侧过期截断**，
  **不是券商 OTP 有效期**（Agent 不知道 OTP 的签发时间）

如果你升级前的 `settings.json` 里存着 301~900 的值（例如 900），升级后
Agent **不会因此启动失败** —— 加载时会把它下调到 300，并在 `agent.log` 打印一条
警告说明原因。超出旧区间（比如 29 或 901）的值仍会直接报错，避免手误被静默改写。

---

## 12. 命令速查

```
/mt4 A 123456        # 登录 alias 为 A 的账号
/mt4 GROUP1 123456   # 按 Group 顺序登录
/mt4 status          # 查看当前状态
```

只有 Allowed Slack User IDs 里的 User ID 可以执行登录；未授权请求会在解析 OTP 前就拒绝。
