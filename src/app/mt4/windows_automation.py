from __future__ import annotations

import asyncio
import logging
import multiprocessing
import os
import re
import subprocess
import threading
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from app.models.domain import (
    _WIN32_FALLBACK_KEYS,  # single source of truth for the control id key space
    AccountConfig,
    AccountRuntimeStatus,
    AutomationResult,
    ErrorCategory,
    LoginStatus,
    utc_now,
)
from app.security.redaction import install_redaction_filter, sensitive_values

WINDOWS_REAL_TEST_REQUIRED = "WINDOWS_REAL_TEST_REQUIRED"

_FAILURE_WORDS = (
    "login failed",
    "invalid account",
    "incorrect password",
    "invalid password",
    "authentication failed",
    "server unavailable",
    "invalid server",
    "trade server is offline",
    "no connection to trade server",
    "account disabled",
    "not authorized to trade",
    "scheduled maintenance",
    "定期メンテナンス",
    "サーバーへ接続できません",
    "接続できません",
    "ログイン失敗",
    "認証失敗",
    "パスワードが正しくありません",
    "パスワード誤",
    "アカウントが凍結",
    "ワンタイムパスワードの有効期限が切れました",
)
_MAIN_WINDOW_WORDS = ("metatrader", "mt4", "取引")

# Menu captions that open the MT4 login dialog. Compared after normalisation, and
# matched exactly so that zero or several matches both refuse to act.
_MENU_LOGIN_CAPTIONS = frozenset(
    {
        "取引口座にログイン",  # MT4 standard Japanese
        "login to trade account",  # English
    }
)
_WM_COMMAND = 0x0111
_MF_BYPOSITION = 0x0400
_SMTO_ABORTIFHUNG = 0x0002


class WindowsAutomationError(Exception):
    def __init__(self, category: ErrorCategory, message: str) -> None:
        super().__init__(message)
        self.category = category
        self.safe_message = message


class WindowsAutomation:
    """UI Automation adapter.

    Imports of Windows-only packages are deliberately deferred until an instance is
    constructed, so the rest of the application can be tested on macOS/Linux.
    """

    mode = "windows"

    def __init__(self) -> None:
        if os.name != "nt":
            raise RuntimeError("WindowsAutomation requires Windows")
        try:
            import psutil  # type: ignore[import-not-found]
            import pywinauto  # type: ignore[import-not-found]
            from pywinauto import Desktop  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - exercised on Windows only
            raise RuntimeError(
                "Windows automation dependencies are missing; run install.bat on Windows"
            ) from exc
        self._psutil = psutil
        self._desktop_factory = Desktop
        if getattr(pywinauto, "UIA_support", None) is False:
            raise RuntimeError(
                "Windows UI Automation backend is unavailable; install comtypes/pywinauto"
            )
        self._desktop_local = threading.local()

    async def login(self, account: AccountConfig, otp: SecretStr) -> AutomationResult:
        # UIA/COM runs in a terminate-able child process. A timeout can therefore stop
        # the worker instead of leaving a thread filling controls after the queue advances.
        return await self._login_isolated(account, otp.get_secret_value())

    async def _login_isolated(self, account: AccountConfig, otp: str) -> AutomationResult:
        started = utc_now()
        if multiprocessing.parent_process() is not None:
            return AutomationResult(
                status=LoginStatus.FAILED,
                category=ErrorCategory.UI_AUTOMATION_ERROR,
                message="Windows UI worker cannot start a nested login process",
                started_at=started,
                verification=WINDOWS_REAL_TEST_REQUIRED,
            )
        budget = account.launch_timeout_seconds + account.login_timeout_seconds + 10.0
        context = multiprocessing.get_context("spawn")
        receiver, sender = context.Pipe(duplex=False)
        process = context.Process(
            target=_windows_login_worker,
            args=(sender, account, otp),
            name=f"mt4-uia-{account.alias}",
            daemon=True,
        )
        try:
            process.start()
        except Exception:
            receiver.close()
            sender.close()
            return AutomationResult(
                status=LoginStatus.FAILED,
                category=ErrorCategory.UI_AUTOMATION_ERROR,
                message="Could not start the isolated Windows UI worker",
                started_at=started,
                verification=WINDOWS_REAL_TEST_REQUIRED,
            )
        sender.close()
        deadline = time.monotonic() + budget
        worker_exited = False
        try:
            while time.monotonic() < deadline:
                try:
                    has_payload = await asyncio.to_thread(receiver.poll, 0.1)
                    if has_payload:
                        try:
                            payload = receiver.recv()
                            return AutomationResult.model_validate(payload)
                        except (EOFError, OSError):
                            worker_exited = True
                            break
                        except ValueError:
                            return AutomationResult(
                                status=LoginStatus.FAILED,
                                category=ErrorCategory.UI_AUTOMATION_ERROR,
                                message="Isolated Windows UI worker returned an invalid result",
                                started_at=started,
                                verification=WINDOWS_REAL_TEST_REQUIRED,
                            )
                except (EOFError, OSError):
                    break
                if not process.is_alive():
                    # The child can exit immediately after send(); drain a payload
                    # that became visible between poll() and is_alive().
                    try:
                        if receiver.poll(0):
                            payload = receiver.recv()
                            return AutomationResult.model_validate(payload)
                    except (EOFError, OSError, ValueError):
                        pass
                    worker_exited = True
                    break
                await asyncio.sleep(0.02)
            await asyncio.to_thread(self._terminate_process, process)
            if worker_exited:
                return AutomationResult(
                    status=LoginStatus.FAILED,
                    category=ErrorCategory.UI_AUTOMATION_ERROR,
                    message="Isolated Windows UI worker exited before returning a result",
                    started_at=started,
                    verification=WINDOWS_REAL_TEST_REQUIRED,
                )
            return AutomationResult(
                status=LoginStatus.TIMEOUT,
                category=ErrorCategory.TIMEOUT,
                message="Windows UI Automation exceeded its hard wait budget",
                started_at=started,
                verification=WINDOWS_REAL_TEST_REQUIRED,
            )
        finally:
            receiver.close()
            if process.is_alive():
                await asyncio.to_thread(self._terminate_process, process)
            else:
                await asyncio.to_thread(process.join, 0.2)
            close_process = getattr(process, "close", None)
            if callable(close_process):
                with suppress(Exception):
                    close_process()

    @staticmethod
    def _terminate_process(process: Any) -> None:
        try:
            if process.is_alive():
                process.terminate()
                process.join(timeout=2.0)
            if process.is_alive() and hasattr(process, "kill"):
                process.kill()
                process.join(timeout=2.0)
        except Exception:
            # The worker may exit between is_alive/terminate; never expose provider text.
            return

    async def account_status(self, account: AccountConfig) -> AccountRuntimeStatus:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._status_sync, account), timeout=5.0
            )
        except TimeoutError:
            return AccountRuntimeStatus(
                account_id=account.id,
                alias=account.alias,
                enabled=account.enabled,
                configuration_valid=True,
                process_state="unavailable",
                issues=["Windows process inspection exceeded timeout"],
            )

    async def close(self) -> None:
        return None

    def _status_sync(self, account: AccountConfig) -> AccountRuntimeStatus:
        pids = self._process_ids(account)
        return AccountRuntimeStatus(
            account_id=account.id,
            alias=account.alias,
            enabled=account.enabled,
            configuration_valid=True,
            process_state=("ambiguous" if len(pids) > 1 else "running" if pids else "not_running"),
            issues=[],
        )

    def _login_sync(self, account: AccountConfig, otp: str) -> AutomationResult:
        started = utc_now()
        login_control: Any | None = None
        otp_control: Any | None = None
        try:
            pids = self._process_ids(account)
            if len(pids) > 1:
                raise WindowsAutomationError(
                    ErrorCategory.AMBIGUOUS_PROCESS,
                    "More than one matching MT4 process was found; configure a unique Profile",
                )
            if not pids:
                if account.profile_path and self._executable_is_running(account):
                    raise WindowsAutomationError(
                        ErrorCategory.INSTANCE_UNVERIFIABLE,
                        "A matching MT4 process is running but its Profile could not be verified",
                    )
                if not Path(account.terminal_path).expanduser().is_file():
                    raise WindowsAutomationError(
                        ErrorCategory.TERMINAL_NOT_FOUND,
                        "Configured terminal.exe was not found on this Windows PC",
                    )
                launched_pid = self._launch(account)
                pids = self._process_ids(account) or [launched_pid]
                if len(pids) > 1:
                    raise WindowsAutomationError(
                        ErrorCategory.AMBIGUOUS_PROCESS,
                        "The launched MT4 resolved to multiple processes; "
                        "refusing an ambiguous login",
                    )
            login_window = self._find_login_window(pids, account)
            win32_controls: dict[str, Any] = {}
            use_win32 = False
            auto_open_detail = ""
            if login_window is None:
                if account.win32_fallback.enabled:
                    # Opening the dialog here is safe: it happens before any
                    # credential reaches the form, so a failure cannot consume one.
                    # The command only ever reaches this Account's own main window.
                    outcome, detail = self._win32_open_login_dialog(pids, account)
                    if outcome not in {"already_open", "opened"}:
                        auto_open_detail = f"auto-open {outcome}: {detail}"
                # UIA is primary. The native dialog is only considered when the
                # Account explicitly opted in and every Win32 precondition holds.
                dialog = self._win32_dialog(pids, account)
                if dialog is not None:
                    win32_controls = self._win32_controls(dialog, account)
                    if self._win32_usable(win32_controls):
                        login_window, use_win32 = dialog, True
            if login_window is None:
                suffix = f" ({auto_open_detail})" if auto_open_detail else ""
                if self._has_main_window(pids):
                    raise WindowsAutomationError(
                        ErrorCategory.ALREADY_RUNNING_NO_LOGIN_WINDOW,
                        "MT4 is running but its login window was not found" + suffix,
                    )
                raise WindowsAutomationError(
                    ErrorCategory.UI_CONTROL_NOT_FOUND,
                    "MT4 login window was not found before timeout" + suffix,
                )

            if use_win32:
                server_ready = self._win32_select_server(win32_controls, account)
            else:
                server_ready = self._select_server(login_window, account)
            if not server_ready:
                raise WindowsAutomationError(
                    ErrorCategory.UI_CONTROL_NOT_FOUND,
                    "MT4 Server control was not found or could not be selected",
                )
            if use_win32:
                login_control, otp_control = (
                    win32_controls["login_id"],
                    win32_controls["otp"],
                )
            else:
                login_control, otp_control = self._find_login_controls(login_window, account)

            self._set_text(login_control, account.login_id)
            self._set_text(otp_control, otp, verify_exact=False)
            if not use_win32:
                self._check_save_login(login_window, account)

            if use_win32:
                login_button = win32_controls["login_button"]
            else:
                login_button = self._find_control(login_window, account, "login_button", "Button")
            if login_button is None:
                raise WindowsAutomationError(
                    ErrorCategory.UI_CONTROL_NOT_FOUND,
                    "MT4 Login button was not found",
                )
            success_windows_before = self._success_window_state(pids, account)
            failure_windows_before = self._visible_window_state(pids)
            self._invoke(login_button)

            status, category, message, verification = self._wait_for_result(
                pids, login_window, account, success_windows_before, failure_windows_before
            )
            self._clear_credentials(login_control, otp_control)
            return AutomationResult(
                status=status,
                category=category,
                message=message,
                started_at=started,
                verification=verification,
            )
        except WindowsAutomationError as exc:
            self._clear_credentials(login_control, otp_control)
            return AutomationResult(
                status=LoginStatus.TIMEOUT
                if exc.category == ErrorCategory.TIMEOUT
                else LoginStatus.FAILED,
                category=exc.category,
                message=exc.safe_message,
                started_at=started,
                verification=WINDOWS_REAL_TEST_REQUIRED,
            )
        except FileNotFoundError:
            self._clear_credentials(login_control, otp_control)
            return AutomationResult(
                status=LoginStatus.FAILED,
                category=ErrorCategory.TERMINAL_NOT_FOUND,
                message="Configured terminal.exe was not found",
                started_at=started,
                verification=WINDOWS_REAL_TEST_REQUIRED,
            )
        except Exception:
            # Never include exception text: third-party UI errors can echo field values.
            self._clear_credentials(login_control, otp_control)
            return AutomationResult(
                status=LoginStatus.FAILED,
                category=ErrorCategory.UI_AUTOMATION_ERROR,
                message="Windows UI Automation failed; inspect the local redacted log",
                started_at=started,
                verification=WINDOWS_REAL_TEST_REQUIRED,
            )

    def _launch(self, account: AccountConfig) -> int:
        if account.profile_path:
            profile = Path(account.profile_path).expanduser()
            if not profile.is_dir():
                raise WindowsAutomationError(
                    ErrorCategory.MT4_LAUNCH_FAILED,
                    "Configured MT4 Profile directory does not exist",
                )
            working_directory = profile
        else:
            working_directory = Path(account.terminal_path).expanduser().parent
        args = [account.terminal_path, *account.launch_arguments]
        kwargs: dict[str, Any] = {
            "cwd": str(working_directory),
            "close_fds": True,
        }
        # Do not hide the terminal's first window: a GUI MT4 login dialog may be its
        # only visible startup signal. The process is intentionally not killed on exit.
        try:
            process = subprocess.Popen(args, **kwargs)
        except OSError as exc:
            raise WindowsAutomationError(
                ErrorCategory.MT4_LAUNCH_FAILED,
                "MT4 could not be started; verify the executable and profile path",
            ) from exc
        return process.pid

    @staticmethod
    def _normalized_path(value: str) -> str:
        return os.path.normcase(os.path.realpath(os.path.abspath(os.path.expanduser(value))))

    def _processes(self, attrs: list[str]) -> list[Any]:
        try:
            return list(self._psutil.process_iter(attrs))
        except Exception as exc:
            raise WindowsAutomationError(
                ErrorCategory.INSTANCE_UNVERIFIABLE,
                "Could not enumerate MT4 processes; refusing to launch an unverified instance",
            ) from exc

    def _executable_is_running(self, account: AccountConfig) -> bool:
        wanted = self._normalized_path(account.terminal_path)
        for process in self._processes(["pid", "exe"]):
            try:
                info = process.info or {}
                executable = info.get("exe")
                if executable and self._normalized_path(executable) == wanted:
                    return True
            except (OSError, self._psutil.Error) as exc:
                raise WindowsAutomationError(
                    ErrorCategory.INSTANCE_UNVERIFIABLE,
                    "Could not inspect all MT4 processes; refusing to launch "
                    "an unverified instance",
                ) from exc
        return False

    def _process_ids(self, account: AccountConfig) -> list[int]:
        wanted = self._normalized_path(account.terminal_path)
        wanted_name = os.path.normcase(account.process_name or Path(wanted).name)
        wanted_cwd = self._normalized_path(account.profile_path) if account.profile_path else None
        exact_pids: list[int] = []
        exact_profile_pids: list[int] = []
        fallback_pids: list[int] = []
        for process in self._processes(["pid", "exe", "name", "cwd"]):
            try:
                info = process.info or {}
                executable = info.get("exe")
                name = info.get("name") or ""
                cwd = info.get("cwd") or ""
                path_match = bool(executable and self._normalized_path(executable) == wanted)
                profile_match = not wanted_cwd or (cwd and self._normalized_path(cwd) == wanted_cwd)
                name_match = bool(account.process_name and os.path.normcase(name) == wanted_name)
                pid = int(info["pid"])
                if wanted_cwd and name_match and not cwd:
                    raise WindowsAutomationError(
                        ErrorCategory.INSTANCE_UNVERIFIABLE,
                        "A matching MT4 process has an unreadable Profile; refusing to launch",
                    )
                if path_match:
                    exact_pids.append(pid)
                    if profile_match:
                        exact_profile_pids.append(pid)
                elif name_match and profile_match:
                    fallback_pids.append(pid)
            except (OSError, self._psutil.Error) as exc:
                raise WindowsAutomationError(
                    ErrorCategory.INSTANCE_UNVERIFIABLE,
                    "Could not inspect all MT4 processes; refusing to launch "
                    "an unverified instance",
                ) from exc
        if wanted_cwd:
            return exact_profile_pids or fallback_pids
        return exact_pids

    def _desktop(self) -> Any:
        desktop = getattr(self._desktop_local, "desktop", None)
        if desktop is None:
            desktop = self._desktop_factory(backend="uia")
            self._desktop_local.desktop = desktop
        return desktop

    def _windows(self, pids: list[int]) -> list[Any]:
        windows: list[Any] = []
        desktop = self._desktop()
        for pid in pids:
            try:
                windows.extend(desktop.windows(process=pid, visible_only=True))
            except Exception:
                continue
        return windows

    @staticmethod
    def _enum_top_windows() -> list[tuple[int, int, str, str]]:
        """Enumerate native top-level windows as (handle, pid, class, title).

        UI Automation does not expose some brokers' login dialogs (verified on
        Rakuten MT4), so discovery falls back to native enumeration. Only window
        identity is read here; no control text or value is touched.
        """
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        # EnumWindows takes a function pointer. Passing a bare Python callable fails
        # with ctypes.ArgumentError, so wrap it in the exact WNDENUMPROC signature and
        # keep a strong reference alive for the duration of the call. WINFUNCTYPE only
        # exists on Windows; CFUNCTYPE is its portable equivalent elsewhere.
        enum_proc_type = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
        enum_proc = enum_proc_type(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows.argtypes = [enum_proc, wintypes.LPARAM]
        user32.EnumWindows.restype = wintypes.BOOL
        rows: list[tuple[int, int, str, str]] = []

        def collect(handle: int, _param: int) -> bool:
            if not user32.IsWindow(handle):
                return True
            class_buffer = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(handle, class_buffer, 256)
            title = ""
            length = user32.GetWindowTextLengthW(handle)
            if length > 0:
                title_buffer = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(handle, title_buffer, length + 1)
                title = title_buffer.value
            process_id = wintypes.DWORD()
            user32.GetWindowThreadProcessId(handle, ctypes.byref(process_id))
            rows.append((int(handle), int(process_id.value), class_buffer.value, title))
            return True

        callback = enum_proc(collect)
        user32.EnumWindows(callback, 0)
        return rows

    @staticmethod
    def _normalize_menu_caption(text: str) -> str:
        """Normalise a Win32 menu caption for comparison.

        Menu text carries an ampersand accelerator marker and usually ends with a
        tab-separated shortcut hint, both of which are decoration rather than the
        command's identity. A trailing "(L)" accelerator hint is dropped for the
        same reason.
        """
        cleaned = (text or "").replace("&", "")
        cleaned = cleaned.split("\t", 1)[0]
        cleaned = re.sub(r"\([^()]{1,3}\)\s*$", "", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned.strip().casefold()

    @staticmethod
    def _win32_menu_commands(hwnd: int) -> list[tuple[int, str]]:
        """Walk the window's menu hierarchy and return (command_id, caption) pairs.

        Command ids are resolved at runtime from the menu itself, so no id is ever
        hard-coded. A depth bound keeps a malformed or cyclic menu from looping.
        """
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        for function, argtypes, restype in (
            (user32.GetMenu, [wintypes.HWND], wintypes.HANDLE),
            (user32.GetSubMenu, [wintypes.HANDLE, ctypes.c_int], wintypes.HANDLE),
            (user32.GetMenuItemCount, [wintypes.HANDLE], ctypes.c_int),
            (
                user32.GetMenuStringW,
                [
                    wintypes.HANDLE,
                    wintypes.UINT,
                    wintypes.LPWSTR,
                    ctypes.c_int,
                    wintypes.UINT,
                ],
                ctypes.c_int,
            ),
            (user32.GetMenuItemID, [wintypes.HANDLE, ctypes.c_int], wintypes.UINT),
        ):
            function.argtypes = argtypes
            function.restype = restype

        root = user32.GetMenu(hwnd)
        if not root:
            return []
        found: list[tuple[int, str]] = []

        def walk(menu: Any, depth: int) -> None:
            if depth > 6:
                return
            for index in range(user32.GetMenuItemCount(menu)):
                buffer = ctypes.create_unicode_buffer(512)
                user32.GetMenuStringW(menu, index, buffer, 512, _MF_BYPOSITION)
                caption = buffer.value
                submenu = user32.GetSubMenu(menu, index)
                if submenu:
                    walk(submenu, depth + 1)
                    continue
                command_id = user32.GetMenuItemID(menu, index)
                # 0xFFFFFFFF marks a separator or a popup, not a command.
                if command_id and command_id != 0xFFFFFFFF and caption:
                    found.append((int(command_id), caption))

        walk(root, 0)
        return found

    def _win32_main_window(
        self, pids: list[int], account: AccountConfig
    ) -> tuple[int | None, str]:
        """Locate the single native MT4 main window of the resolved process.

        The login dialog also carries words such as "MetaTrader" in its title, so
        the window class is the discriminator: an MT4 main window is a MetaQuotes
        class while the login dialog is a plain dialog class. Ambiguity refuses to
        choose, so one Account can never open a dialog on another instance.
        """
        expected = set(pids)
        if len(expected) != 1:
            return None, f"expected exactly one MT4 process, found {len(expected)}"
        candidates = [
            handle
            for handle, pid, class_name, _title in self._enum_top_windows()
            if pid in expected and class_name.startswith("MetaQuotes")
        ]
        if not candidates:
            return None, "no MT4 main window was found for the resolved process"
        if len(candidates) > 1:
            return None, f"{len(candidates)} MT4 main windows matched; refusing to choose"
        return candidates[0], "one MT4 main window matched"

    def _win32_login_menu_command(self, hwnd: int) -> tuple[int | None, str]:
        """Resolve the single login-to-trade command id from the window's own menu."""
        matches = [
            command_id
            for command_id, caption in self._win32_menu_commands(hwnd)
            if self._normalize_menu_caption(caption) in _MENU_LOGIN_CAPTIONS
        ]
        if not matches:
            return None, "no login menu command matched the expected captions"
        if len(matches) > 1:
            return None, f"{len(matches)} login menu commands matched; refusing to choose"
        return matches[0], "one login menu command matched"

    @staticmethod
    def _win32_send_command(hwnd: int, command_id: int, timeout_ms: int = 5000) -> bool:
        """Send a menu command to a window with a bounded, non-blocking wait."""
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        result = ctypes.c_size_t()
        return bool(
            user32.SendMessageTimeoutW(
                hwnd,
                _WM_COMMAND,
                wintypes.WPARAM(command_id),
                0,
                _SMTO_ABORTIFHUNG,
                timeout_ms,
                ctypes.byref(result),
            )
        )

    def _win32_open_login_dialog(
        self,
        pids: list[int],
        account: AccountConfig,
        *,
        wait_seconds: float = 8.0,
        require_opt_in: bool = True,
    ) -> tuple[str, str]:
        """Open the MT4 login dialog through the window's own menu.

        Only used for an Account that opted into the Win32 dialog fallback. No
        coordinate is used and no key is sent: the command id is resolved from the
        menu hierarchy at runtime and must be unique, and the message goes only to
        the main window of the process this Account resolved to. The credential is
        not involved at any point, so a failure here cannot consume one.
        """
        if require_opt_in and not account.win32_fallback.enabled:
            return "not_enabled", "the Win32 dialog fallback is not enabled for this Account"
        if self._win32_dialog(pids, account) is not None:
            return "already_open", "the login dialog was already present"
        hwnd, detail = self._win32_main_window(pids, account)
        if hwnd is None:
            return "unavailable", detail
        command_id, detail = self._win32_login_menu_command(hwnd)
        if command_id is None:
            return "unavailable", detail
        if not self._win32_send_command(hwnd, command_id):
            return "unavailable", "the window did not accept the menu command"
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            if self._win32_dialog(pids, account) is not None:
                return "opened", "the login dialog appeared after the menu command"
            time.sleep(0.25)
        return "timeout", "the login dialog did not appear before the bounded wait"

    @staticmethod
    def _win32_wrap(handle: int) -> Any | None:
        try:
            from pywinauto import Application

            application = Application(backend="win32").connect(handle=int(handle))
            return application.window(handle=int(handle))
        except Exception:
            return None

    @staticmethod
    def _win32_descendants(control: Any) -> list[Any]:
        try:
            return list(control.descendants())
        except Exception:
            return []

    @staticmethod
    def _win32_control_id(control: Any) -> int | None:
        try:
            value = getattr(control.element_info, "control_id", None)
            return int(value) if value is not None else None
        except Exception:
            return None

    @staticmethod
    def _win32_class(control: Any) -> str:
        try:
            return str(getattr(control.element_info, "class_name", "") or "")
        except Exception:
            return ""

    def _win32_child(
        self, root: Any, control_id: int | None, class_name: str | None = None
    ) -> Any | None:
        """Select exactly one descendant. Ambiguity resolves to nothing, fail closed.

        Both the Login ID and the Server ComboBox contain a child Edit that shares
        one native control id, so callers must pass the owning ComboBox as ``root``
        instead of the dialog.
        """
        if control_id is None:
            return None
        pool = [
            control
            for control in self._win32_descendants(root)
            if self._win32_control_id(control) == int(control_id)
            and (class_name is None or self._win32_class(control) == class_name)
        ]
        return pool[0] if len(pool) == 1 else None

    def _win32_controls(self, dialog: Any, account: AccountConfig) -> dict[str, Any]:
        """Resolve the login controls by native id, anchored to their owners."""
        config = account.win32_fallback
        found: dict[str, Any] = {}
        login_combo = self._win32_child(dialog, config.key("login_id_combo"), "ComboBox")
        login_edit = self._win32_child(login_combo, config.key("login_id_edit"), "Edit")
        found["login_id"] = login_edit if login_edit is not None else login_combo
        found["otp"] = self._win32_child(dialog, config.key("otp"), "Edit")
        server_combo = self._win32_child(dialog, config.key("server_combo"), "ComboBox")
        found["server_combo"] = server_combo
        found["server_edit"] = self._win32_child(server_combo, config.key("server_edit"), "Edit")
        found["login_button"] = self._win32_child(dialog, config.key("login_button"), "Button")
        return found

    @staticmethod
    def _win32_usable(controls: dict[str, Any]) -> bool:
        if controls.get("login_id") is None or controls.get("otp") is None:
            return False
        if controls.get("login_button") is None:
            return False
        return controls.get("server_combo") is not None or controls.get("server_edit") is not None

    def _win32_dialog(self, pids: list[int], account: AccountConfig) -> Any | None:
        """Locate the login dialog natively, verifying every precondition first.

        Fails closed unless the dialog belongs to one of the resolved MT4 pids, has
        the configured class, matches the Account window_title_regex, carries every
        configured anchor text, and exposes each required control id unambiguously.
        """
        config = account.win32_fallback
        if not config.enabled or not account.window_title_regex:
            return None
        expression = re.compile(account.window_title_regex)
        expected = set(pids)
        for handle, process_id, class_name, title in self._enum_top_windows():
            if process_id not in expected or class_name != config.dialog_class:
                continue
            if not expression.search(title):
                continue
            dialog = self._win32_wrap(handle)
            if dialog is None:
                continue
            texts = []
            for control in self._win32_descendants(dialog):
                try:
                    value = control.window_text()
                except Exception:
                    continue
                if value:
                    texts.append(str(value))
            joined = " ".join(texts).casefold()
            if any(anchor.casefold() not in joined for anchor in config.anchors):
                continue
            if not self._win32_usable(self._win32_controls(dialog, account)):
                continue
            return dialog
        return None

    def _win32_inspect(self, pids: list[int], account: AccountConfig) -> dict[str, Any]:
        """Suggest a Win32 dialog configuration for the resolved MT4 instance.

        This reads control *metadata* only: native control id, class name, and the
        window text of inert controls. It never reads an Edit's value, so it cannot
        surface a credential, and it never saves anything: the caller presents the
        suggestions and a human applies them.

        Confidence is HIGH only when the structural evidence resolves uniquely. A
        shape that cannot be pinned down is reported as NEEDS_CONFIRMATION rather
        than guessed, so a wrong id is never offered as a confident answer.
        """
        report: dict[str, Any] = {
            "outcome": "unavailable",
            "detail": "",
            "suggested": {},
            "confidence": {},
            "dialog_class": None,
            "dialog_title": None,
            "titles": {},
            "menu_captions": [],
        }
        resolved = sorted({int(item) for item in pids})
        if len(resolved) != 1:
            report["detail"] = (
                f"expected exactly one MT4 process, found {len(resolved)}; the "
                "multi-instance guard needs a distinct terminal folder and cwd"
            )
            return report

        dialog, how = self._win32_inspect_dialog(resolved, account)
        if dialog is None:
            report["detail"] = how
            return report

        report["outcome"] = "inspected"
        report["detail"] = how
        try:
            report["dialog_class"] = self._win32_class(dialog)
            report["dialog_title"] = str(dialog.window_text() or "")
        except Exception as exc:
            report["detail"] = f"{how}; identity unreadable: {type(exc).__name__}"
        pairs = self._win32_suggest(dialog)
        report["suggested"] = {key: value for key, (value, _) in pairs.items()}
        report["confidence"] = {key: conf for key, (_, conf) in pairs.items()}
        report["titles"] = self._win32_title_suggestions(resolved, account, dialog)
        report["menu_captions"] = self._win32_menu_captions(resolved, account)
        return report

    def _win32_inspect_dialog(
        self, pids: list[int], account: AccountConfig
    ) -> tuple[Any | None, str]:
        """Find the login dialog, opening it through the menu when necessary."""
        existing, why = self._win32_locate_dialog(pids)
        if existing is not None:
            return existing, "the login dialog was already open"
        # Reuse the menu driven open the real login uses. A different broker may not
        # ship the expected menu caption, so failure is not fatal here: the user can
        # open the dialog by hand and run Detect Win32 again.
        self._win32_open_login_dialog(pids, account, require_opt_in=False)
        existing, why = self._win32_locate_dialog(pids)
        if existing is not None:
            return existing, "the login dialog was opened through the window menu"
        return None, (
            f"{why}. If this broker uses a different menu caption, open the login "
            "dialog by hand and run Detect Win32 again."
        )

    def _win32_locate_dialog(self, pids: list[int]) -> tuple[Any | None, str]:
        """Locate a login-shaped dialog belonging to the resolved pids.

        The configuration is not known yet, so no dialog class is assumed. A window
        qualifies when it exposes both an Edit and a Button, which is the same shape
        test the real login path uses.
        """
        expected = set(pids)
        candidates: list[Any] = []
        for handle, process_id, _class_name, _title in self._enum_top_windows():
            if process_id not in expected:
                continue
            dialog = self._win32_wrap(handle)
            if dialog is None:
                continue
            try:
                if not self._win32_login_shaped(dialog):
                    continue
            except Exception:
                continue
            candidates.append(dialog)
        if not candidates:
            return None, "no window with both an Edit and a Button was found"
        if len(candidates) > 1:
            return None, f"{len(candidates)} login-shaped windows matched; refusing to choose"
        return candidates[0], "one login-shaped window matched"

    def _win32_login_shaped(self, dialog: Any) -> bool:
        """Whether a native dialog exposes both an Edit and a Button.

        The UIA shape test cannot answer this: a broker whose login form UIA never
        exposes is exactly the case the Win32 route exists for.
        """
        classes = set()
        for control in self._win32_descendants(dialog):
            classes.add(self._win32_class(control))
        return "Edit" in classes and "Button" in classes

    def _win32_metadata(self, control: Any) -> dict[str, Any]:
        """Control metadata only. The caption of an editable control is never read."""
        try:
            class_name = self._win32_class(control)
        except Exception:
            class_name = ""
        try:
            control_id = self._win32_control_id(control)
        except Exception:
            control_id = None
        item: dict[str, Any] = {"class": class_name, "control_id": control_id}
        # An Edit can hold the Login ID and a ComboBox the selected server, so only
        # inert controls expose their caption.
        if class_name not in {"Edit", "ComboBox"}:
            try:
                item["text"] = str(control.window_text() or "")
            except Exception:
                item["text"] = ""
        return item

    def _win32_owned_by_combo(self, dialog: Any) -> set[int]:
        """Identity set of the Edits that a ComboBox owns."""
        owned: set[int] = set()
        for control in self._win32_descendants(dialog):
            try:
                if self._win32_class(control) != "ComboBox":
                    continue
                for child in control.descendants():
                    if self._win32_class(child) == "Edit":
                        owned.add(id(child))
            except Exception:
                continue
        return owned

    def _win32_suggest(self, dialog: Any) -> dict[str, Any]:
        """Map the dialog's controls onto the configuration's logical keys."""
        children = self._win32_descendants(dialog)
        metadata = [self._win32_metadata(control) for control in children]
        owned = self._win32_owned_by_combo(dialog)
        controls_by_identity = {
            id(control): meta
            for control, meta in zip(children, metadata, strict=True)
        }

        def by_class(name: str) -> list[dict[str, Any]]:
            return [item for item in metadata if item["class"] == name]
        combos = by_class("ComboBox")
        buttons = by_class("Button")
        free_edits = [
            meta
            for control, meta in controls_by_identity.items()
            if meta["class"] == "Edit" and control not in owned
        ]

        suggested: dict[str, Any] = {}

        def _put(key: str, value: Any, confidence: str) -> None:
            suggested[key] = (value, confidence)

        try:
            _put("dialog_class", self._win32_class(dialog), "HIGH")
        except Exception:
            _put("dialog_class", None, "NEEDS_CONFIRMATION")

        def _combo_edit(combo_id: Any) -> Any:
            for control in children:
                try:
                    if self._win32_class(control) != "ComboBox":
                        continue
                    if self._win32_control_id(control) != combo_id:
                        continue
                    owned_edits = [
                        child
                        for child in control.descendants()
                        if self._win32_class(child) == "Edit"
                    ]
                except Exception:
                    continue
                if len(owned_edits) == 1:
                    return self._win32_control_id(owned_edits[0])
            return None

        if len(combos) == 2:
            # Dialog order is login id first then server on every MT4 build.
            for key, combo in (("login_id_combo", combos[0]), ("server_combo", combos[1])):
                _put(key, combo["control_id"], "HIGH")
                edit_id = _combo_edit(combo["control_id"])
                _put(
                    key.replace("_combo", "_edit"),
                    edit_id,
                    "HIGH" if edit_id else "NEEDS_CONFIRMATION",
                )
        elif combos:
            # Keep the reported shape identical whatever was found, so a caller can
            # always read the same keys.
            for index, combo in enumerate(combos):
                key = "login_id_combo" if index == 0 else "server_combo"
                edit_id = _combo_edit(combo["control_id"])
                _put(key, combo["control_id"], "NEEDS_CONFIRMATION")
                _put(
                    key.replace("_combo", "_edit"),
                    edit_id,
                    "NEEDS_CONFIRMATION",
                )
        else:
            for key in ("login_id_combo", "server_combo", "login_id_edit", "server_edit"):
                _put(key, None, "NEEDS_CONFIRMATION")

        # The credential box is the Edit no ComboBox owns.
        _put("otp", free_edits[0]["control_id"] if len(free_edits) == 1 else None,
             "HIGH" if len(free_edits) == 1 else "NEEDS_CONFIRMATION")

        if len(buttons) == 1:
            _put("login_button", buttons[0]["control_id"], "HIGH")
        else:
            words = ("login", "ログイン", "接続", "sign in")
            matches = [
                item
                for item in buttons
                if any(word in str(item.get("text", "")).casefold() for word in words)
            ]
            _put("login_button", matches[0]["control_id"] if len(matches) == 1 else None,
                 "HIGH" if len(matches) == 1 else "NEEDS_CONFIRMATION")

        # Whatever was found, the reported shape is identical: every key the
        # configuration defines is present, so a caller can always read them.
        for key in _WIN32_FALLBACK_KEYS:
            if key not in suggested:
                _put(key, None, "NEEDS_CONFIRMATION")

        anchors = self._win32_anchor_candidates(metadata)
        _put("anchors", anchors, "HIGH" if anchors else "NEEDS_CONFIRMATION")
        return suggested

    @staticmethod
    def _win32_anchor_candidates(metadata: list[dict[str, Any]]) -> list[str]:
        """Visible field labels, later used to confirm the dialog is the right one."""
        anchors: list[str] = []
        for item in metadata:
            if item["class"] not in {"Static", "Label"}:
                continue
            text = str(item.get("text", "")).strip()
            if not text or len(text) > 60 or text in anchors:
                continue
            anchors.append(text)
            if len(anchors) == 2:
                break
        return anchors

    def _win32_title_suggestions(
        self, pids: list[int], account: AccountConfig, dialog: Any
    ) -> dict[str, str | None]:
        """Suggest anchored title expressions from what is on screen right now.

        The Account's own login id is generalised away rather than written into the
        expression, so a suggestion stays valid for the broker's other accounts
        instead of encoding a value that expires with this one.
        """
        suggestions: dict[str, str | None] = {
            "window_title_regex": None,
            "success_window_title_regex": None,
        }
        try:
            login_title = str(dialog.window_text() or "").strip()
        except Exception:
            login_title = ""
        if login_title:
            suggestions["window_title_regex"] = f"^{re.escape(login_title)}$"

        main_title = ""
        for _handle, process_id, class_name, title in self._enum_top_windows():
            if process_id in set(pids) and class_name.startswith("MetaQuotes"):
                main_title = str(title or "").strip()
                break
        if main_title:
            # Escape first and substitute afterwards: escaping a string that already
            # contains the placeholder would turn the wildcard into a literal "\.*".
            escaped = re.escape(main_title)
            login_id = (account.login_id or "").strip()
            if login_id:
                escaped = re.sub(re.escape(login_id), ".*", escaped)
            suggestions["success_window_title_regex"] = f"^{escaped}$"
        return suggestions

    def _win32_menu_captions(
        self, pids: list[int], account: AccountConfig
    ) -> list[str]:
        """Report the target's own menu captions so a new broker's wording is visible.

        The global caption list is never modified from here; it stays a code constant
        that a human extends after seeing what this build actually ships.
        """
        hwnd, _detail = self._win32_main_window(pids, account)
        if hwnd is None:
            return []
        captions: list[str] = []
        try:
            commands = self._win32_menu_commands(hwnd)
        except Exception:
            return []
        for caption, _command_id in commands:
            text = str(caption or "").strip()
            if text and text not in captions:
                captions.append(text)
        return captions[:40]

    def _win32_select_server(
        self, controls: dict[str, Any], account: AccountConfig
    ) -> bool:
        """Select the server through the ComboBox API, never by keypress count."""
        combo = controls.get("server_combo")
        if combo is not None:
            try:
                items = [str(item) for item in combo.texts()]
            except Exception:
                items = []
            if account.server in items:
                try:
                    combo.select(account.server)
                except Exception:
                    return False
                return self._read_control_value(combo).casefold() == account.server.casefold()
        edit = controls.get("server_edit")
        if edit is None:
            return False
        self._set_text(edit, account.server)
        return True

    def _find_login_window(self, pids: list[int], account: AccountConfig) -> Any | None:
        if not account.window_title_regex:
            return None
        deadline = time.monotonic() + account.launch_timeout_seconds
        expression = re.compile(account.window_title_regex)
        while time.monotonic() < deadline:
            candidates = self._windows(pids)
            best_window = None
            best_score = -1
            for window in candidates:
                try:
                    if not expression.search(self._window_text(window)):
                        continue
                    if not self._looks_like_login_window(window):
                        continue
                    if not self._window_has_login_controls(window, account):
                        continue
                    score = len(self._visible_controls(window, "Edit"))
                    if score > best_score:
                        best_window, best_score = window, score
                except Exception:
                    continue
            if best_window is not None and best_score > 0:
                return best_window
            time.sleep(0.25)
        return None

    def _looks_like_login_window(self, window: Any) -> bool:
        return bool(self._visible_controls(window, "Edit")) and bool(
            self._visible_controls(window, "Button")
        )

    def _window_has_login_controls(self, window: Any, account: AccountConfig) -> bool:
        try:
            self._find_login_controls(window, account)
            return True
        except WindowsAutomationError:
            return False

    def _has_main_window(self, pids: list[int]) -> bool:
        for window in self._windows(pids):
            try:
                title = self._window_text(window).casefold()
                if any(word.casefold() in title for word in _MAIN_WINDOW_WORDS):
                    return True
            except Exception:
                continue
        return False

    def _success_window_state(self, pids: list[int], account: AccountConfig) -> dict[str, str]:
        if not account.success_window_title_regex:
            return {}
        expression = re.compile(account.success_window_title_regex)
        state: dict[str, str] = {}
        for window in self._windows(pids):
            try:
                title = self._window_text(window)
                key = self._window_key(window)
                if key is not None and expression.search(title):
                    state[key] = title
            except Exception:
                continue
        return state

    @staticmethod
    def _window_key(window: Any) -> str | None:
        try:
            info = window.element_info
            runtime_id = getattr(info, "runtime_id", None)
            if runtime_id:
                return f"{getattr(info, 'process_id', '')}:{runtime_id}"
            native_handle = getattr(info, "handle", None)
            if native_handle:
                return f"handle:{native_handle}"
        except Exception:
            return None
        return None

    def _wait_for_result(
        self,
        pids: list[int],
        login_window: Any,
        account: AccountConfig,
        success_windows_before: dict[str, str],
        failure_windows_before: dict[str, str],
    ) -> tuple[LoginStatus, ErrorCategory, str, str]:
        deadline = time.monotonic() + account.login_timeout_seconds
        success_expression = (
            re.compile(account.success_window_title_regex)
            if account.success_window_title_regex
            else None
        )
        closed_without_verification = False
        while time.monotonic() < deadline:
            current_failure_state = self._visible_window_state(pids)
            for key, text in current_failure_state.items():
                if key in failure_windows_before and failure_windows_before[key] == text:
                    continue
                if any(word.casefold() in text for word in _FAILURE_WORDS):
                    return (
                        LoginStatus.FAILED,
                        self._failure_category(text),
                        "MT4 rejected the login request",
                        WINDOWS_REAL_TEST_REQUIRED,
                    )
            psutil_module = getattr(self, "_psutil", None)
            if psutil_module is not None and not any(self._safe_pid_exists(pid) for pid in pids):
                return (
                    LoginStatus.FAILED,
                    ErrorCategory.UI_AUTOMATION_ERROR,
                    "MT4 process exited before reporting a login result",
                    WINDOWS_REAL_TEST_REQUIRED,
                )
            try:
                if not login_window.exists():
                    closed_without_verification = True
                    if success_expression is not None:
                        current_success_state = self._success_window_state(pids, account)
                        state_changed = any(
                            key not in success_windows_before
                            or title != success_windows_before[key]
                            for key, title in current_success_state.items()
                        )
                        if state_changed:
                            return (
                                LoginStatus.SUCCESS,
                                ErrorCategory.NONE,
                                "Authenticated window appeared or changed to the "
                                "configured verification",
                                "authenticated_window_transition",
                            )
                        # The main window can already carry the authenticated title
                        # before this attempt, for example when an earlier session was
                        # left open, so an unchanged title on its own proves nothing.
                        # Requiring the login dialog to be gone while a window that
                        # still matches the anchored success regex is present is
                        # sufficient evidence. The regex keeps this fail-closed: when
                        # nothing matches, the result stays unverified.
                        if current_success_state:
                            return (
                                LoginStatus.SUCCESS,
                                ErrorCategory.NONE,
                                "Login dialog closed while a window matching the "
                                "configured authenticated title was present",
                                "authenticated_window_present_after_login_closed",
                            )
            except Exception:
                pass
            time.sleep(0.25)
        if closed_without_verification:
            return (
                LoginStatus.FAILED,
                ErrorCategory.UI_VERIFICATION_UNVERIFIED,
                "登录窗口已关闭，但在超时前没有观察到新的认证主窗口状态转换",
                WINDOWS_REAL_TEST_REQUIRED,
            )
        return (
            LoginStatus.TIMEOUT,
            ErrorCategory.TIMEOUT,
            "MT4 did not report a definitive login result before timeout",
            WINDOWS_REAL_TEST_REQUIRED,
        )

    def _safe_pid_exists(self, pid: int) -> bool:
        try:
            return bool(self._psutil.pid_exists(pid))
        except Exception:
            return False

    def _visible_controls(self, window: Any, control_type: str) -> list[Any]:
        try:
            return [
                wrapper
                for wrapper in window.descendants(control_type=control_type)
                if self._is_usable(wrapper)
            ]
        except Exception:
            return []

    def _find_login_controls(self, window: Any, account: AccountConfig) -> tuple[Any, Any]:
        login_control = self._find_control(window, account, "login_id", "Edit")
        otp_control = self._find_control(window, account, "otp", "Edit")
        if login_control is None or otp_control is None:
            raise WindowsAutomationError(
                ErrorCategory.UI_CONTROL_NOT_FOUND,
                "Login ID or OTP control was not found; configure explicit UIA selectors",
            )
        if self._same_control(login_control, otp_control):
            raise WindowsAutomationError(
                ErrorCategory.UI_CONTROL_NOT_FOUND,
                "Login ID and OTP selectors resolve to the same control",
            )
        return login_control, otp_control

    def _find_control(
        self, window: Any, account: AccountConfig, field: str, control_type: str
    ) -> Any | None:
        candidates = self._visible_controls(window, control_type)
        control_id = account.control_ids.get(field)
        title = account.control_titles.get(field)
        if field in {"login_id", "otp", "login_button"} and not control_id:
            return None
        if control_id:
            matches = [
                candidate
                for candidate in candidates
                if self._element_value(candidate, "automation_id") == control_id
            ]
            return matches[0] if len(matches) == 1 else None
        if title:
            matches = [
                candidate
                for candidate in candidates
                if title.casefold() in self._element_value(candidate, "name").casefold()
            ]
            return matches[0] if len(matches) == 1 else None
        words = {
            "login_id": ("login", "id", "ログイン"),
            "otp": ("password", "パスワード", "otp", "one-time"),
            "server": ("server", "サーバー"),
            "save_login": ("save", "保存"),
            "login_button": ("login", "ログイン", "接続"),
        }.get(field, ())
        matches = [
            candidate
            for candidate in candidates
            if any(
                word.casefold() in self._element_value(candidate, "name").casefold()
                for word in words
            )
        ]
        return matches[0] if len(matches) == 1 else None

    def _select_server(self, window: Any, account: AccountConfig) -> bool:
        control = self._find_control(window, account, "server", "ComboBox")
        if control is not None:
            if self._select_combo_value(control, account.server):
                return self._read_control_value(control).casefold() == account.server.casefold()
            return False
        control = self._find_control(window, account, "server", "Edit")
        if control is not None:
            self._set_text(control, account.server)
            return True
        return False

    def _select_combo_value(self, control: Any, value: str) -> bool:
        if self._read_control_value(control).casefold() == value.casefold():
            return True
        try:
            selection_item = getattr(control, "iface_selection_item", None)
            select = getattr(selection_item, "Select", None)
            if callable(select):
                select()
                return True
        except Exception:
            pass
        try:
            expand = getattr(control, "iface_expand", None)
            expand_method = getattr(expand, "Expand", None)
            if callable(expand_method):
                expand_method()
            for item in self._visible_controls(control, "ListItem"):
                name = self._element_value(item, "name")
                if name.casefold() != value.casefold():
                    continue
                item_selection = getattr(item, "iface_selection_item", None)
                item_select = getattr(item_selection, "Select", None)
                if callable(item_select):
                    item_select()
                    return True
        except Exception:
            return False
        return False

    def _check_save_login(self, window: Any, account: AccountConfig) -> None:
        control = self._find_control(window, account, "save_login", "CheckBox")
        if control is None:
            if account.save_login_info:
                raise WindowsAutomationError(
                    ErrorCategory.UI_CONTROL_NOT_FOUND,
                    "Save-login was requested but the checkbox was not found",
                )
            return
        state = self._read_toggle_state(control)
        if state is None:
            if account.save_login_info:
                raise WindowsAutomationError(
                    ErrorCategory.UI_AUTOMATION_ERROR,
                    "Save-login checkbox state could not be verified",
                )
            return
        desired = 1 if account.save_login_info else 0
        if state == desired:
            return
        try:
            toggle_pattern = getattr(control, "iface_toggle", None)
            toggle = getattr(toggle_pattern, "Toggle", None)
            if not callable(toggle):
                raise AttributeError("TogglePattern is unavailable")
            toggle()
        except Exception as exc:
            raise WindowsAutomationError(
                ErrorCategory.UI_AUTOMATION_ERROR,
                "Save-login checkbox could not be set to the configured state",
            ) from exc
        for _ in range(3):
            if self._read_toggle_state(control) == desired:
                return
            time.sleep(0.05)
        raise WindowsAutomationError(
            ErrorCategory.UI_AUTOMATION_ERROR,
            "Save-login checkbox state did not converge to the configured value",
        )

    @staticmethod
    def _read_toggle_state(control: Any) -> int | None:
        candidates: list[Any] = []
        getter = getattr(control, "get_toggle_state", None)
        if callable(getter):
            candidates.append(getter)
        toggle_pattern = getattr(control, "iface_toggle", None)
        current = getattr(toggle_pattern, "CurrentToggleState", None)
        if current is not None:
            candidates.append(lambda: current)
        for candidate in candidates:
            try:
                value = candidate()
                if isinstance(value, bool):
                    return int(value)
                if isinstance(value, int) and value in {0, 1, 2}:
                    return value
                text = str(value).casefold()
                if text in {"1", "on", "true"}:
                    return 1
                if text in {"0", "off", "false"}:
                    return 0
            except Exception:
                continue
        return None

    @staticmethod
    def _window_text(window: Any) -> str:
        try:
            return str(window.window_text())
        except Exception:
            return ""

    def _visible_window_text(self, window: Any) -> str:
        # Read labels/static text only; never inspect Edit values (which may hold OTP).
        values = [self._window_text(window)]
        try:
            for control in window.descendants():
                control_type = self._element_value(control, "control_type")
                if control_type in {"Text", "Document", "Group"} and self._is_usable(control):
                    values.append(self._element_value(control, "name"))
        except Exception:
            pass
        return " ".join(value for value in values if value)

    @staticmethod
    def _failure_category(text: str) -> ErrorCategory:
        folded = text.casefold()
        if any(
            word in folded
            for word in (
                "offline",
                "no connection",
                "server unavailable",
                "接続できません",
                "サーバーへ接続できません",
            )
        ):
            return ErrorCategory.BROKER_OFFLINE
        if any(word in folded for word in ("maintenance", "メンテナンス")):
            return ErrorCategory.MAINTENANCE
        if any(word in folded for word in ("account disabled", "not authorized", "凍結")):
            return ErrorCategory.ACCOUNT_DISABLED
        return ErrorCategory.LOGIN_REJECTED

    def _visible_window_state(self, pids: list[int]) -> dict[str, str]:
        state: dict[str, str] = {}
        for window in self._windows(pids):
            try:
                key = self._window_key(window)
                if key is not None:
                    state[key] = self._visible_window_text(window).casefold()
            except Exception:
                continue
        return state

    @staticmethod
    def _same_control(first: Any, second: Any) -> bool:
        if first is second:
            return True
        try:
            first_info = first.element_info
            second_info = second.element_info
            return (
                first_info.process_id == second_info.process_id
                and first_info.runtime_id == second_info.runtime_id
            )
        except Exception:
            return False

    @staticmethod
    def _element_value(control: Any, name: str) -> str:
        try:
            return str(getattr(control.element_info, name, "") or "")
        except Exception:
            return ""

    @staticmethod
    def _is_usable(control: Any) -> bool:
        try:
            minimized = getattr(control, "is_minimized", None)
            if callable(minimized) and minimized():
                return False
            return bool(control.is_visible() and control.is_enabled())
        except Exception:
            return False

    def _set_text(self, control: Any, value: str, *, verify_exact: bool = True) -> None:
        errors: list[Exception] = []
        for method in ("set_edit_text", "set_value"):
            try:
                action = getattr(control, method, None)
                if callable(action):
                    action(value)
                    actual = self._read_control_value(control)
                    valid = actual == value if verify_exact else actual in {"", value}
                    if valid:
                        return
                    errors.append(ValueError("UIA value read-back mismatch"))
            except Exception as exc:
                errors.append(exc)
        try:
            value_pattern = getattr(control, "iface_value", None)
            setter = getattr(value_pattern, "SetValue", None)
            if callable(setter):
                setter(value)
                actual = self._read_control_value(control)
                valid = actual == value if verify_exact else bool(actual)
                if valid:
                    return
                errors.append(ValueError("UIA value read-back mismatch"))
        except Exception as exc:
            errors.append(exc)
        raise WindowsAutomationError(
            ErrorCategory.UI_AUTOMATION_ERROR,
            "A text field could not be filled through UI Automation",
        ) from (errors[-1] if errors else None)

    @staticmethod
    def _clear_credentials(*controls: Any) -> None:
        for control in controls:
            if control is None:
                continue
            try:
                setter = getattr(control, "set_edit_text", None)
                if callable(setter):
                    setter("")
                    continue
            except Exception:
                pass
            try:
                value_pattern = getattr(control, "iface_value", None)
                setter = getattr(value_pattern, "SetValue", None)
                if callable(setter):
                    setter("")
            except Exception:
                continue

    @staticmethod
    def _read_control_value(control: Any) -> str:
        for method in ("get_value", "selected_text", "window_text"):
            try:
                action = getattr(control, method, None)
                if callable(action):
                    value = action()
                    if value is not None:
                        return str(value)
            except Exception:
                continue
        try:
            value_pattern = getattr(control, "iface_value", None)
            value = getattr(value_pattern, "CurrentValue", None)
            if value is not None:
                return str(value)
        except Exception:
            pass
        return ""

    @staticmethod
    def _invoke(control: Any) -> None:
        # UIA wrappers expose invoke(); win32 wrappers expose click().
        action = getattr(control, "invoke", None)
        if callable(action):
            try:
                action()
                return
            except Exception as exc:
                raise WindowsAutomationError(
                    ErrorCategory.UI_AUTOMATION_ERROR,
                    "The MT4 login control could not be activated through InvokePattern",
                ) from exc
        action = getattr(control, "click", None)
        if callable(action):
            try:
                action()
                return
            except Exception as exc:
                raise WindowsAutomationError(
                    ErrorCategory.UI_AUTOMATION_ERROR,
                    "The MT4 login control could not be activated through click",
                ) from exc
        raise WindowsAutomationError(
            ErrorCategory.UI_AUTOMATION_ERROR,
            "The MT4 login control does not expose InvokePattern or click",
        )


def _windows_login_worker(sender: Any, account: AccountConfig, otp: str) -> None:
    """Spawn-safe child entry point; never send exception text or the OTP back."""
    try:
        import sys

        if os.name == "nt" and hasattr(sys, "coinit_flags") and sys.coinit_flags == 0:
            sys.coinit_flags = 2  # STA: pywinauto's documented UIA default.
        # Spawn starts a fresh interpreter; import UIA first, then attach redaction
        # to any handlers the third-party packages installed.
        automation = WindowsAutomation()
        automation._desktop()
        logging.getLogger("pywinauto").setLevel(logging.WARNING)
        install_redaction_filter()
        with sensitive_values(otp):
            result = automation._login_sync(account, otp)
        with suppress(Exception):
            sender.send(result.model_dump(mode="json"))
    except BaseException:
        with suppress(Exception):
            sender.send(
                {
                    "status": "failed",
                    "category": ErrorCategory.UI_AUTOMATION_ERROR.value,
                    "message": "Isolated Windows UI worker failed",
                    "verification": WINDOWS_REAL_TEST_REQUIRED,
                }
            )
    finally:
        sender.close()
