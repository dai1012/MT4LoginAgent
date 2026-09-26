from __future__ import annotations

import os
import sys
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class AppPaths:
    data_dir: Path

    @classmethod
    def resolve_default(cls) -> AppPaths:
        override = os.environ.get("MT4_AGENT_DATA_DIR")
        if override:
            return cls(Path(override).expanduser().resolve())
        if sys.platform == "win32":
            local_app_data = os.environ.get("LOCALAPPDATA")
            if local_app_data:
                return cls(Path(local_app_data) / "RakutenMT4Agent")
            return cls(Path.home() / "AppData" / "Local" / "RakutenMT4Agent")
        xdg_data_home = os.environ.get("XDG_DATA_HOME")
        base = (
            Path(xdg_data_home).expanduser() if xdg_data_home else Path.home() / ".local" / "share"
        )
        return cls((base / "rakuten-mt4-agent").resolve())

    @classmethod
    def from_value(cls, value: str | Path | None) -> AppPaths:
        if value is None:
            return cls.resolve_default()
        return cls(Path(value).expanduser().resolve())

    @property
    def settings_file(self) -> Path:
        return self.data_dir / "settings.json"

    @property
    def secrets_file(self) -> Path:
        return self.data_dir / "secrets.json"

    @property
    def accounts_file(self) -> Path:
        return self.data_dir / "accounts.json"

    @property
    def groups_file(self) -> Path:
        return self.data_dir / "groups.json"

    @property
    def history_file(self) -> Path:
        return self.data_dir / "history.jsonl"

    @property
    def dedup_file(self) -> Path:
        return self.data_dir / "dedup.json"

    @property
    def instance_file(self) -> Path:
        return self.data_dir / "agent.lock"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / "reports"

    def ensure(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        with suppress(OSError):
            self.data_dir.chmod(0o700)
