# Pre-Windows AI Hardening 实施报告

项目：`<project-root>/`

## 结论

已完成五轮 AI 审计、修复和回归。当前代码可以进入真实 Windows + Rakuten MT4 实机测试阶段，但**不能把当前 macOS 测试结果描述为真实 Rakuten 登录成功**。所有真实 UIA/COM/Slack/broker 行为继续标记为 `WINDOWS_REAL_TEST_REQUIRED`。

## Round 1 — Core correctness / concurrency

### 发现并修复

- **同一 Slack delivery 重复执行**：新增进程内 + 本地 HMAC digest dedup guard；handler 重启、Salt retry、ack 失败和 queue 满时正确释放 key。
- **同一账号/Group 并发**：加入 pending account ID 防重和队列容量上限；不再让第二个 OTP 并发操作同一窗口。
- **排队期间 Account/Group 变化**：每个成员执行前 live reload；target kind/target_id 固定；同名 Group 替换不会执行新成员。
- **取消任务无回执**：cancelled 任务会为未执行成员生成安全结果并调用 completion callback。
- **OTP 排队过期**：加入 `otp_max_age_seconds`，超期不送入 automation。
- **原子/回滚**：Account/Group 使用共享 catalog lock；删除失败恢复；JSON 写入增加目录 fsync。
- **Windows worker**：UIA 放入 spawn 子进程；超时/取消 terminate + kill；worker 崩溃与 timeout 分开报告；父进程不阻塞事件循环。

### 结果

- 无 Critical。
- 关键 High/Medium 均有代码修复和回归测试。
- 双 Python 版本在 Round 1 结束时通过。

## Round 2 — Security / secrets / privacy

### 发现并修复

- **Web API 无认证**：生成 Local admin token，所有 `/api/*` 写/读接口（health 除外）要求 `X-Admin-Token`；token 只在 `start.bat` 控制台显示，浏览器只放 sessionStorage。
- **chunked body 绕过大小限制**：新增真实 ASGI body counting middleware，限制 128 KiB；同时限制 launch argument、selector 字符串长度和 Account 数量。
- **OTP 日志边界**：增加短 TTL、有限容量的进程内脱敏摘要，覆盖任务 context 外的日志；异常 traceback 清理。
- **Git/临时文件**：补充 `.gitignore` 的 dedup/tmp/bak/swap/editor 文件规则。
- **CSRF/Origin**：跨源 API Origin 拒绝；无 CORS；TrustedHost、no-store、CSP、nosniff 保留。
- **Slack token 校验**：Connection Test 先检查 App-level token，保留 Slack API 错误类别。
- **运行时数据**：secrets、dedup、log、account、history 与源码分离。

### 结果

- 无 Critical/High 残留。
- 仍明确记录：同一 Windows 用户恶意进程不在本 V1 的隔离威胁模型内；secrets.json 的 Windows ACL 需实机核对。

## Round 3 — Windows / Rakuten adversarial review

### 发现并修复

- 删除 `type_keys` 和 `click_input` fallback：没有键盘模拟、坐标点击或位置猜测。
- Login/OTP/Server/Login button 强制唯一明确 `control_ids`；关键字段不接受模糊 title selector。
- Server 先选择并回读，再填写 Login ID/OTP；ValuePattern 写入回读，密码/OTP 允许 UIA 掩码空回读。
- Profile 不可读、多个进程匹配时 `INSTANCE_UNVERIFIABLE`/`AMBIGUOUS_PROCESS`，不重复启动终端。
- 使用 realpath/normcase 处理 Windows 路径；Profile 路径成为 Windows 必填项。
- 登录窗口必须有明确 window regex 且包含 Login/OTP 控件；错误/维护窗口不会提前被选中。
- 成功必须匹配锚定认证主窗口（新建或状态变化）；新错误/维护窗口不算成功。
- 移除 index-based Edit 猜测；server/checkbox/selector 歧义 fail closed。
- 成功正则要求锚定；危险嵌套/灾难性 regex 拒绝。
- spawn worker 增加 COM STA 初始化、freeze support、process handle close 和敏感字段清理。
- failure 扫描只检查点击后新增/变化的窗口，并扩展 broker offline/maintenance/冻结类别。

### 结果

- 无 Critical/High 静态阻断。
- 真实控件 ID、ValuePattern/InvokePattern、COM、DPI/UAC、券商文案和认证窗口状态仍必须 `WINDOWS_REAL_TEST_REQUIRED`。

## Round 4 — Slack / Web / Admin UX

### 发现并修复

- `start.bat` 打印 Local admin token、日志路径和默认端口；配置/端口/venv/import 错误会以非零退出并 pause。
- batch 文件转换为 CRLF，并加入 `.gitattributes`。
- Account 校验错误包含具体 check 名称；前端格式化 FastAPI 数组 detail，不再显示 `[object Object]`。
- 无效配置 Account 仍可 Disable；Windows 必填项在表单和文档中明确。
- Group 编辑保留原成员顺序；卡片显示 disabled 成员；↑/↓ 保持执行顺序。
- Slack 保存后根据 connected/state/last_error 给出真实反馈；支持清除 App/Bot token。
- Connection Test 校验 App token、显示 Workspace/Team/Bot 信息。
- Dashboard 显示 validation issues 和 valid/invalid/ambiguous 颜色。
- Dashboard 可见时自动刷新；token 失效显示 token bar，可重新输入。
- 未授权 Slack 回执显示用户自己的 User ID，便于配置 allowlist。
- 坏 Group 引用降级为 missing_accounts，不让整个 Web Admin 空白。
- History 显示最近 500 条的说明。
- `--port` 只影响本次进程，不永久覆盖 settings.json。
- 修复 RuntimeSnapshot 字段不一致。

### 结果

- 无 Critical/High 用户阻塞项。
- 真实 Slack 安装/频道权限和真实 Windows 错误画面仍需实机。

## Round 5 — Documentation / release readiness

### 更新文件

- `README.md`：重写为完整 Windows 用户手册，包含架构、安全模型、支持/不支持、安装、Slack App/Manifest/Token/User ID、Web Admin、Account、Group、命令示例、History、数据/Secrets、停止、更新、卸载、故障排查和 `WINDOWS_REAL_TEST_REQUIRED`。
- `SECURITY.md`：新增 OTP 生命周期、Slack、Web、secrets、网络、fail-closed 和报告边界。
- `docs/WINDOWS-MT4-TEST.md`：重写为自动化已验证/Windows 静态验证/真实 Windows gate 三层 checklist。
- `config/examples/README.md`：说明所有占位符必须替换。
- `start.bat` / `install.bat`：加入 token、日志、self-check 和可读错误。
- `.gitattributes`：固定 Windows 脚本 CRLF。
- `config/examples/accounts.example.json`：加入 Windows 必填 Profile、control IDs 和锚定 success regex 占位符。

## Final Adversarial Review（本轮新增）

独立复核发现并修复了此前测试未覆盖的真实问题：

- Web admin token 比较改为 UTF-8 bytes，非 ASCII header 不再触发 500。
- Socket Mode transport 断开超过 grace period 后会丢弃 stale handler 并重新建立连接。
- 同一 data-dir 增加可重入的 OS 单实例锁；`stop()` 后 `start()` 会重新获取锁。
- Windows worker 在 fresh spawn 解释器中注册 OTP 脱敏 registry 和 redaction filter；pywinauto logger 强制 WARNING。
- `save_login_info` 改用 TogglePattern + 回读，不再读取不存在的 `element_info.toggle_state`。
- 认证成功允许“新出现的锚定认证主窗口”，修复正常 MT4 流程永远无法 SUCCESS 的结构性缺陷。
- Windows 进程枚举异常改为 `INSTANCE_UNVERIFIABLE`，不再 fail-open 启动第二个 MT4。
- Server 选择改用 SelectionItem/Toggle UIA pattern，移除库内部可能调用坐标 `click_input()` 的 ComboBox `.select()`。
- 密码/OTP 字段允许 UIA 掩码导致的空回读；其他不一致仍 fail closed。
- `native_window_handle` 更正为 pywinauto 实际的 `handle` 属性。
- worker 退出竞态会再次 drain Pipe；worker `sender.send()` 失败不会打印第三方 traceback。
- 端口探测不再设置 `SO_REUSEADDR`；Windows 使用 `SO_EXCLUSIVEADDRUSE`（若存在）。
- `agent.lock`、Windows UIA 依赖 self-check、`save_login_info` 回读、非 ASCII token、新建认证窗口、枚举失败、单实例锁、shutdown 拒绝新任务、单调时钟 OTP 年龄和 mid-group OTP 过期均补了针对性测试。
## Windows Acceptance Test Runner

- 新增 `src/app/testing/`：统一 TestResult/Report 模型、Environment Safe Checks、MT4/UIA Discovery、Slack Safe Tests、报告生成和 secret redaction。
- Web Admin 的 **Windows Test** 页面和 `test-windows.bat`/CLI 调用同一个 Test Runner Core。
- 默认只执行 SAFE；Real Login、Group、Full Slack E2E 和 destructive cases 需要人工确认或 `MANUAL_TEST_REQUIRED`。
- UIA diagnostic、HTML/JSON/log 报告写入 runtime data-dir，报告不包含 OTP/token/secret。
- Full Slack E2E 不由 Runner 自动发送 OTP；用户手动发送命令，Runner 等待 History 结果并关联 session id。
- 新增 Test Runner 自身测试：模型/报告序列化、HTML/JSON、secret redaction、UIA redaction、Windows guard、阶段顺序、Real Login confirmation、Group partial、Slack session、API 和分发入口。


```text
Python 3.14: 97 passed
Python 3.11: 97 passed
Ruff: All checks passed
```

最终质量门已执行：

- `pip check`：✅
- `compileall`（Python 3.11/3.14）：✅
- wheel build + Web asset contents：✅
- localhost Uvicorn smoke：✅（health、HTML、static、TrustedHost、Local admin token）
- git status 检查：✅

## 当前已知限制

1. 真实 Windows/Rakuten UI 行为未在当前环境证明。
2. 同一 Windows 用户的恶意本地进程仍可能读取进程内存或同 ACL 数据；Local admin token 解决的是无 token 的本地 HTTP/CSRF 驱动，不是完整 OS 沙箱。
3. `secrets.json` 依赖 Windows `%LOCALAPPDATA%` ACL，V1 未接入 DPAPI/Credential Manager。
4. History 文件不会自动轮转；长期运行需要人工备份/保留策略。
5. Slack 完成回执发送到触发命令的频道/DM；公共频道会显示 alias 和结果，建议私聊或受控频道。
6. 登录队列上限为 16，OTP 有效期上限为 900 秒；深度队列在慢速 Windows 机器上可能使后续成员返回 `otp_expired`。
7. MT4 主窗口 UIA 树规模、遍历耗时和 COM 行为仍需真实 Windows 测量。
8. Slack handler 在登录完成前被人工重连时，旧 generation 的 completion `say` 可能失败，结果需要用户用 `/mt4 status`/History 补查。
9. 项目当前在父仓库中是未跟踪目录，尚未创建 baseline commit；建议进入 Windows 测试前创建 `pre-windows-test` baseline commit，但本轮没有擅自提交或 push。
