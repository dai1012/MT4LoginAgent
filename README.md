# MT4 远程登录 Agent

[简体中文](README.md) | [日本語](README.ja.md) | [English](README.en.md)

一个运行在**你自己的 Windows 电脑上**、源码可审计的 MetaTrader 4 远程登录 Agent。

同事在 Slack 里发一条命令：

```text
/mt4 A 123456
```

`A` 是你在本地 Web Admin 里配置的 **Account 别名**，不是真实 Login ID；`123456` 是本次
使用的**凭据**（密码或一次性验证码）。Agent 会在本机找到对应的 MT4 终端，用本机保存的
Login ID 和 Server 加上这个凭据完成登录，**并只把结果回复给发起的人**。

Agent 跑在你自己的机器上，不需要公网服务器、不需要开放入站端口、不需要数据库。

> ### ⚠️ 请先读这一段
>
> **本项目不是 Rakuten 的官方产品，也与 Rakuten 无任何关联、赞助或背书。**
> "Rakuten MT4" 在本仓库中仅作为**当前已在真机上验证过的券商与 MT4 build 的示例**
> 出现。所有券商专用配置（对话框控件 id、菜单文案、窗口标题）都是**按券商而定**的，
> 详见[新券商适配](#9-新券商适配)。
>
> **诚实的验证状态**：核心 MT4 自动化（Win32 登录对话框全链路、多实例隔离、登录成功
> 判定、自动打开登录框）**已在真实 Windows + 真实券商终端上验证过**。但**尚未**在真机
> 验证的有：完整 Slack 端到端往返、Group 登录、以及较新的 Win32 Inspector。任何需要
> 真机而未验证的项，一律返回 `WINDOWS_REAL_TEST_REQUIRED`，**不会伪造通过**。
> 逐项边界见[已知限制](#11-已知限制)。

## 目录

- [核心功能](#2-核心功能)
- [快速开始](#3-快速开始)
- [Slack 命令与权限](#4-slack-命令与权限)
- [多账户与多实例](#5-多账户与多实例)
- [Windows Test](#6-windows-test)
- [Win32 Inspector](#7-win32-inspector)
- [新券商适配](#9-新券商适配)
- [安全模型](#10-安全模型)
- [已知限制](#11-已知限制)
- [文档索引](#12-文档索引)
- [截图](#13-截图)
- [开发](#14-开发)
- [数据与文件](#15-数据与文件)
- [停止、更新、卸载](#16-停止更新卸载)
- [许可证](#17-许可证)

## 1. 项目定位

**它解决的问题**：MT4 的一次性验证码有效期很短，人不在电脑前时无法完成登录。Slack 负责
把"账号别名 + 凭据"送到 Agent，Agent 负责把它填进**你本机**的 MT4 登录框并回报结果。

**它不是什么**：

- 不是交易工具，不下单、不碰持仓、不读账户资金
- 不是云服务，没有服务器端，没有账号体系
- 不是某个券商的官方客户端

**边界**：Agent 只在你本机执行一次"填表 + 点登录"，其余全部交给 MT4 自己。

## 2. 核心功能

| 能力 | 说明 |
|---|---|
| Slack 命令触发本机 MT4 登录 | Socket Mode，**无需开放入站端口** |
| **多账户**，一人或多人 | 各自独立别名、独立终端安装目录、独立工作目录 |
| **Account 复制** | 同券商同 build 的第二个账号，一键复制全部已配置字段 |
| **自动打开登录框** | 通过终端**自身的菜单命令**打开，不用坐标、不盲发按键 |
| **UIA 优先 + Win32 回退** | build 暴露 UIA 就用 UIA；完全不暴露就用原生 Win32 控件 id |
| **Win32 Inspector** | 读目标机器上的登录框并**建议**配置，不用手抄控件 id |
| **Windows Test** | 分阶段的引导式验收，**不声称自己没验证过的东西** |
| **History** | 谁在什么时候请求了什么、结果如何，**不含凭据** |
| **按用户绑定 Account** | 同一 workspace 内，每人只能操作绑定给自己的别名 |
| **结果私密投递** | 完成结果只回给发起人，**不进频道** |

## 3. 快速开始

1. **把项目文件夹复制**到你的 Windows 电脑。
2. **安装 Python 3.12**（推荐）或 3.11，并安装 Python Launcher `py.exe`。
3. 运行 [`install.bat`](install.bat)，它会装依赖并自检。
4. 运行 [`start.bat`](start.bat)，记下它打印的 **本地 admin token**。
5. 浏览器打开 `http://127.0.0.1:8765` 并输入 token。端口被占用时控制台会提示，
   可改 `settings.json` 或用 `--port`。
6. 在 **Accounts** 里添加账号。给它一个 `alias`（例如 `A`）—— 这是 Slack 里用的名字，
   不是真实 Login ID。填 `terminal_path` 和进程工作目录。
7. 在 **Slack** 页用 [`slack-app-manifest.yaml`](slack-app-manifest.yaml) 创建 App，
   然后填入 `xapp-` app-level token、`xoxb-` bot token 和你的 Slack Member ID，保存。
8. 在 Slack 里发：

   ```text
   /mt4 A 123456
   /mt4 status
   ```

Slack 侧的完整步骤（App Home Messages tab、两个 token 的区别）见
**[docs/SLACK-SETUP.md](docs/SLACK-SETUP.md)**。

## 4. Slack 命令与权限

```text
/mt4 <alias-or-group> <credential>
/mt4 status
```

- `<alias-or-group>` 是 **Account 别名**（或 Group 名）。它是标识符，**永远不是 Login ID**。
- `<credential>` **只填密码或一次性验证码，不要包含 Login ID** —— Agent 从 Account 里已经知道了。

凭据是**不透明**的：可以包含空格与符号。Agent 把第一个 token 之后的所有文本都当作凭据，
所以带空格的密码也能用。

```text
/mt4 A hunter2correct-horse
/mt4 B 483920
```

### 两道独立的权限闸门

1. **Allowed Slack User IDs** —— 谁能用这个 Agent。
2. **Account bindings** —— 这些人在 Web Admin 里勾选了哪些 Account 别名。

第二道是 **fail-closed**：在 allowlist 里但**没有绑定任何人**，就**什么都操作不了**。
把某人加进 allowlist **绝不会**等于给他全部账号的权限，所以拉同事进来不会悄悄把你的
交易账号交出去。

两人协作示例：

```
U_A -> [A]
U_B -> [B]
```

**你无权访问的别名，和根本不存在的别名，返回完全相同的提示。** 否则光靠提示语就能
逐个试探出哪些别名存在。被拒绝的请求在进入队列、接触 MT4、写入任何凭据**之前**就被拦下，
所以不会消耗一次性验证码，也不会占用登录槽位。

| 消息 | 频道内 | DM 内 | 谁能看见 |
|---|---|---|---|
| 即时 ack | 仅发起人 | 仅发起人 | 其他任何人 |
| 完成结果 | 仅发起人（ephemeral） | 仅发起人 | 其他任何人 |
| `/mt4 status` | 仅发起人 | 仅发起人 | 其他任何人 |

`/mt4 status` **只报告调用者自己被绑定的别名**，不报告全局账号总数、排队数或运行中
任务数 —— 那些数字描述的是别人的工作。

> 同一 workspace 里，两人**仍然能在 Slack 成员目录看到彼此的 Profile**，这是 Slack 自身的
> 行为，Agent 无法也不打算隐藏。Agent 保证的是：看不到对方的 MT4 Account、无法对其发起
> 登录、读不到对方的登录结果。

## 5. 多账户与多实例

MetaTrader 官方的建议是：同时运行多个终端时，**每个终端装到不同目录**。Agent 同样依赖
这一点 —— 它靠 `terminal_path` + **进程工作目录**来区分终端。

- 每个并发终端有自己的**安装目录**
- 每个 Account 有自己的 `terminal_path` 和自己的 `profile_path`
  （`profile_path` 是**进程工作目录**，**不是** MT4 的 Open Data Folder）

**实例冲突护栏**：两个 enabled Account 不允许解析到同一个终端实例。尝试启用时会被拒绝
并说明原因，而不是让你一个人的登录撞上另一个人的。disabled 草稿允许暂时与 source 共用
路径，方便你先建好再改。

## 6. Windows Test

Web Admin 里的分步引导式验收。它**拒绝声称自己没观察到的事情**：只有当真正看到一个匹配
你那条锚定 success 表达式的窗口时，才会报告登录成功。

Step 卡片含义：

| 卡片 | 含义 |
|---|---|
| `FAIL` | 存在真正阻断的失败项 |
| `WARN` | 存在真实告警或降级，但流程仍可继续 |
| `PASS` / `READY` | 自动检查全部通过 |
| `MANUAL` 行 | 需要你自己做决定（破坏性测试、或只能手动完成的确认） |

**`MANUAL` 不会把卡片染黄。** 这类项目只以计数形式出现在副文案里
（`已通过 · 2 manual checks remaining`）。**要看某一步到底发生了什么，看表格，不看卡片颜色。**

破坏性测试（改系统时间、触发 UAC、杀 worker、双实例）标记为可选，**绝不自动执行**。

从空白机器到验收的完整走查见
**[docs/WINDOWS-TEST-GUIDE.md](docs/WINDOWS-TEST-GUIDE.md)**。

## 7. Win32 Inspector

**适用**：新券商，或同一券商的新 MT4 build。
**不适用**：同券商同 build 的第二个账号 —— 那种情况直接用 **Account 复制**更快。

```
新建 Account  →  启动目标 MT4  →  Windows Test: Detect Win32
              →  审阅建议  →  Apply  →  手动复制两条 title 建议  →  Real Login 验证
```

| 特性 | 说明 |
|---|---|
| 只读元数据 | 读控件 id、class、非编辑控件文本；**从不读 Edit 的值**，所以不可能泄漏 Login ID、Server 或凭据 |
| 不自动保存 | 只有你点 Apply 才写入 |
| 不猜 | 无法唯一判定的字段标 `NEEDS_CONFIRMATION` 且**不给值**；任一必需字段未确认，Apply 就不出现 |
| 只写 Win32 fallback | `alias` / `login_id` / `server` / `terminal_path` / `profile_path` 一律不动 |
| 暴露真实菜单文案 | 面板显示该 build 自己的菜单文案，auto-open 失败时最快的排查入口 |

详见 [docs/NEW-MT4-ADAPTER.md](docs/NEW-MT4-ADAPTER.md)。

## 8. 哪些字段稳定，哪些会失效

| 稳定 | 说明 |
|---|---|
| `terminal_path`、`profile_path`、`process_name` | 只在重装或移动软件时变 |
| `dialog_class` = `#32770` | Windows 标准对话框类，任何 MT4 build 都用 |
| Win32 六个控件 id | 数值型 `DlgCtrlID`，**不随语言变化** |

| 容易失效 | 原因 |
|---|---|
| `window_title_regex` | 通常包含券商品牌与产品名 |
| `success_window_title_regex` | 必须锚定，很容易写得太窄 |
| UIA `control_ids` | 取决于 UIA provider，跨 build 不保证；**部分 build 完全不暴露 UIA** |
| Win32 `anchors` | 是可见文案，随语言和品牌变 |
| 菜单 caption 列表 | 代码常量，定制 build 文案可能不同 |

**实际后果**：如果某个 build 上 UIA 曾经可用、升级后失效了，**这是已知且预期的失败**。

## 9. 新券商适配

```
装好券商 MT4 并手动登录一次  →  Web Admin 建 Account（选择器留空）
  →  启动该终端  →  Detect Win32  →  审阅  →  Apply
  →  手动复制两条 title 建议  →  Step 4 Real Login 验证
```

**UIA 完全不可用时不要手输 automation id** —— 那种 build 的登录框 UIA 根本看不到，
输什么都没用。请走 Win32 Inspector。

完整流程、字段稳定性分层与故障排查见
**[docs/NEW-MT4-ADAPTER.md](docs/NEW-MT4-ADAPTER.md)**。

## 10. 安全模型

凭据从 Slack 进入内存、进入登录框，**到此为止**。

**不会**出现在：任何级别的日志、History（`history.jsonl`）、报告
（`report.json` / `report.html` / 脱敏 UIA 树）、配置文件、临时文件、命令行参数
（因此进程列表里也看不到）。

三重保障：

1. **结构性** —— 没有任何代码路径把凭据格式化进消息；错误只带固定原因。
2. **注册** —— 凭据在操作期间注册为 live secret，所有渲染值都会按它擦除。
3. **模式** —— 按键擦除 `otp=`、`password:` 之类的片段，并把 `/mt4` 命令剩余部分整体擦除。

Web Admin **只绑定 loopback**，且每个写操作都需要 admin token。

**已诚实标注的局限**：按值擦除会忽略长度小于 4 的候选串（太短的串在普通文本里会频繁
偶合，替换会破坏输出）。短凭据因此依赖前两层，而降低这个下限并不是正确的修法 —— 正确
做法是根本不让它进入任何消息，这正是第一层保证的。

完整威胁模型见 **[SECURITY.md](SECURITY.md)**。

## 11. 已知限制

坦白列出，因为一个把自己说得比实际强的公开项目，比一个老实的更糟。

| 限制 | 影响 |
|---|---|
| **Windows-only 自动化** | 其他平台运行明确标记的 mock，**绝不静默假装是真实登录** |
| **Slack 成员目录** | 同事之间仍能看到彼此 Slack Profile；Agent 的隔离不覆盖 Slack 自己的界面 |
| **并非所有 build 都暴露 UIA** | 部分 MT4 build 的登录框对 UI Automation 完全不可见，因此有 Win32 回退，且需按 Account 显式启用 |
| **auto-open 依赖菜单文案** | 只认识少量标准菜单 caption；定制 build 可能需要手动打开登录框，其余流程不受影响 |
| **每个新券商都要 inspect** | 选择器因 build 而异，由 `Detect Win32` 建议 + 人工确认 |
| **64-bit Python 驱动 32-bit 终端** | 自动化库会打印一条警告，是警告不是失败；原生 Win32 路线的操作不敏感于位数 |
| **Group 是高级可选功能** | group 目标要求全部成员 Account 都绑定给调用者 |
| **部分未在真机验证** | 完整 Slack 端到端、Group 登录、部分新适配器仅有单元测试，它们会报告 `WINDOWS_REAL_TEST_REQUIRED` 而不是伪造通过 |
| **共用 Windows 账户** | 登录该 Windows 会话的任何人都能看到 Agent 窗口和数据目录 |
| **本项目非券商官方产品** | 券商名称仅作为已验证的示例出现 |

## 12. 文档索引

| 文档 | 用途 |
|---|---|
| [docs/WINDOWS-TEST-GUIDE.md](docs/WINDOWS-TEST-GUIDE.md) | 从空白 Windows 机器到验收通过，逐步走查 |
| [docs/SLACK-SETUP.md](docs/SLACK-SETUP.md) | Slack App、两个 token、allowlist、每用户绑定、回复隐私 |
| [docs/NEW-MT4-ADAPTER.md](docs/NEW-MT4-ADAPTER.md) | 新券商/新 build 适配、字段稳定性、Win32 Inspector |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | 报错症状对照表 |
| [SECURITY.md](SECURITY.md) | 威胁模型、凭据处理、保证的边界 |
| [docs/WINDOWS-MT4-TEST.md](docs/WINDOWS-MT4-TEST.md) | 验收必须覆盖什么的短清单 |
| [docs/IMPLEMENTATION-REPORT.md](docs/IMPLEMENTATION-REPORT.md) | 做了什么，以及验证状态 |

## 13. 截图

> 以下 4 张脱敏截图将在下一步加入。文件就位后即可正常显示。

### Dashboard 概览

![Dashboard 概览：Agent 健康状态、最近登录记录与队列状态](docs/images/dashboard_overview.webp)

Dashboard 汇总 Agent 运行状态与最近的登录记录，**不包含任何凭据**。

### Accounts 列表

![Accounts 列表：多个 Account 的别名、券商 Server 与启用状态](docs/images/accounts_list.webp)

每个 Account 一行，含别名、Server、启用状态与多实例隔离信息。

### Windows Test 总览

![Windows Test 页面：五个 Step 卡片与下方逐项结果表](docs/images/windows_test_overview.webp)

注意读法：**卡片是汇总，表格才是每一项的真实状态**。`MANUAL` 项不会把卡片染黄。

### Slack 私密回复

![Slack 中的回复：仅发起人可见的完成结果，不进入频道](docs/images/slack_private.webp)

完成结果是 ephemeral 的，频道内其他成员看不到这条登录结果。

更多界面截图见 [docs/SLACK-SETUP.md](docs/SLACK-SETUP.md) 与
[docs/NEW-MT4-ADAPTER.md](docs/NEW-MT4-ADAPTER.md)。

## 14. 开发

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"     # Windows: .venv\Scripts\pip install -e ".[dev]"

.venv/bin/python -m pytest -q         # 全量测试
.venv/bin/ruff check .               # lint
node --check src/app/web/static/app.js   # 唯一的静态 JS 文件
```

需要 Python 3.11 或更高；Windows 安装脚本优先 3.12，CI 矩阵也是 3.11 / 3.12。

测试套件很快、任何平台都能跑，但大部分验证的是**与平台无关的策略**而非自动化本身。
需要真实 Windows 机器和真实终端的用例都 gate 在 `sys.platform` 上，在非 Windows 上会
明确报告自己未运行。

## 15. 数据与文件

所有数据都在**一个数据目录**里（安装脚本默认指向本地 app data 目录），**绝不在仓库内**。

| 文件 | 内容 |
|---|---|
| `secrets.json` | Slack token、Web Admin token、去重 key。仅当前用户可读 |
| `settings.json` | 非敏感配置：端口、allowlist、绑定、超时 |
| `accounts.json` | Login ID 与选择器。**不是凭据**，但仍属隐私 |
| `groups.json` | Group 成员与顺序 |
| `history.jsonl` | 谁在何时请求了什么、结果如何。**无凭据** |
| `logs/`、`reports/` | 已脱敏 |

仓库已排除上述全部内容，以及各类缓存、虚拟环境和构建产物。见
[.gitignore](.gitignore)。

## 16. 停止、更新、卸载

**停止** —— 在 `start.bat` 窗口按 `Ctrl+C`。同一时间只允许一个 Agent 运行，由数据目录里的
锁文件保证。

**更新** —— 停掉 Agent，替换代码，再跑一次 [`install.bat`](install.bat)。
**保留数据目录**，它存着你的 token、账号与历史。

**卸载** —— 停掉 Agent，删除项目文件夹与数据目录。数据目录是唯一无法靠全新安装重建的东西。

## 17. 许可证

MIT，见 [LICENSE](LICENSE)。
