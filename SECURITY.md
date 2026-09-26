# Security Model

本项目处理交易账户登录，发布前请把下面边界当作安全契约。

## OTP 生命周期

- OTP 来自 Slack command 文本，由 `SecretStr` 包装。
- Agent 只在当前登录任务的内存中使用 OTP。
- OTP 不写入 `accounts.json`、`settings.json`、`history.jsonl`、日志、异常文本、临时文件、subprocess 命令行或 Web API。进程 dump/系统级内存抓取属于操作系统调试边界，Agent 无法阻止。
- 为了防止任务结束后几秒内的第三方日志泄漏，进程内保留一个短 TTL、有限容量的脱敏摘要集合；它不持久化原始 OTP。
- Slack 本身会保存用户发送的 command 消息；这是 Workspace 的消息记录，不是 Agent 的 History。使用私聊/受控频道并遵循 Slack retention 设置。
- pytest、Mock、Web `GET` 均不会回显 OTP。
- 如果 Windows worker 在 OTP 已填入后被硬终止，MT4 登录窗口可能仍显示该 OTP；这是进程被强制结束时的 UI 残留风险，不会进入 Agent 文件或日志。

## Slack

- Socket Mode 连接在 Agent 主动建立的出站 WebSocket 上。
- allowlist 在解析 OTP 前执行。
- 只接受 allowlist 中的 Slack User ID。
- 同一 delivery 的重试/重连重复由 HMAC digest guard 抑制，guard 摘要不包含原始 OTP。
- Slack App-level token、Bot token 只写入 `secrets.json`，API 只返回 configured 布尔值。
- Socket Mode 失败会退避重连；连接状态在 Web/Slack 页面可见。

## Web Admin

- Uvicorn 固定绑定 `127.0.0.1`。
- TrustedHost 限制 Host header。
- API 写操作需要启动时生成的 `X-Admin-Token`；token 只放当前浏览器标签页的 `sessionStorage`。
- Origin 检查拒绝跨源 Web API 请求。
- 无 CORS；`Cache-Control: no-store`；CSP、`nosniff`、X-Frame-Options 已启用。
- Web Admin 没有多用户账号系统。能以同一 Windows 用户权限运行本地进程，仍可能读取进程内存或数据目录；不要把 Agent 当成对同一用户恶意进程隔离的安全边界。
- Account Login ID 会在本机 Web 页面显示，以方便审计；不要在共享桌面/录屏中暴露。

## Files and secrets

- 默认数据目录在 `%LOCALAPPDATA%\RakutenMT4Agent`（Windows）。
- `secrets.json` 保存 Slack token、Web admin token 和 dedup HMAC key；程序尽力 chmod 0600，Windows 依赖目录 ACL。
- `.gitignore` 排除 secrets、runtime data、`.tmp`、`.bak`、editor swap 文件。
- 同一 data-dir 同时只允许一个 Agent 实例（`agent.lock`）；Windows 上该锁的真实 ACL/UAC 行为仍需实机确认。
- 备份软件可能复制 secrets；请使用受控磁盘和加密备份。
- V1 没有接入 Windows Credential Manager/DPAPI；不要把这一限制误读为“Windows ACL 已验证”。
- 泄漏 token 后立即在 Slack 撤销并重新生成。

## Network

运行时主动连接只有：

1. Slack 官方 Socket Mode/API。
2. MT4 自己的 broker/行情连接。
3. 本机浏览器访问 `127.0.0.1`。

安装阶段 `pip` 可能访问包索引。程序没有 telemetry、analytics、AI API、云端自建服务、Docker 或 Redis。

## Fail-closed guarantees

- 未知 alias/Group、未授权 sender、队列冲突：拒绝。
- Login/OTP/Server/Login button 没有唯一明确 UIA selector：拒绝。
- Profile 不可验证或多个进程匹配：拒绝，不向不确定窗口输入。
- 没有匹配配置的认证主窗口（新建或状态变化）：不报告成功。
- Slack ack/submit 失败：释放 dedup key，允许安全重试。
- Windows worker 超时：终止独立子进程，不留下继续填表的线程。

## Windows Acceptance Test Runner

- Test Runner 是现有 `LoginService`、`WindowsAutomation`、Slack gateway 和 Web Admin 的编排层，不是第二套登录实现。
- Phase 1/2/3 默认只执行 SAFE 检查，不输入 OTP、不启动真实登录、不向真实 Slack 批量发消息。
- Real Login、Group 和 Full Slack E2E 都必须由用户在 Web UI 明确确认。
- Test Runner 的 OTP 只在进程内存中存在；不写入 command line、报告、UIA diagnostic、History、临时文件或 traceback。
- UIA diagnostic 不读取密码控件明文 value，并对 Login ID 和节点数量做限制/脱敏。
- HTML/JSON 报告只保存布尔型 `secret_leak_detected`；报告目录和文件使用本地私有权限，Windows 实际 ACL 仍需实机确认。
- 故意错误 OTP、kill 进程、多实例、改系统时间、UAC、网络中断等破坏性测试默认是 `MANUAL_TEST_REQUIRED`。
- macOS/Linux 页面和 CLI 只能显示 `NOT_RUN`/`WINDOWS_REAL_TEST_REQUIRED`，不能显示真实 Windows PASS。


## Reporting

不要提交真实 OTP、Login ID、token、完整异常 dump 或 Slack payload。报告问题时只提供：版本、平台、Account alias、错误 category、脱敏日志片段和 `WINDOWS_REAL_TEST_REQUIRED` 状态。
