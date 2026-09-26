这些文件只是占位示例，Agent 运行时不会自动读取本目录，也不会把示例复制到真实数据目录。

Windows 使用前必须替换：

- `REPLACE_WITH_LOCAL_LOGIN_ID`
- `REPLACE_WITH_RAKUTEN_SERVER`
- `REPLACE_WITH_AUTHENTICATED_MAIN_WINDOW_TITLE`
- 所有 `REPLACE_WITH_*_AUTOMATION_ID`
- terminal.exe 和 Profile 的绝对路径

`save_login_info` 默认 false。只有确认 Rakuten UI 不会保存 OTP 后才打开。
