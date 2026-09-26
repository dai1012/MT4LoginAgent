from __future__ import annotations

import logging
import os
from contextlib import suppress
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.security.redaction import RedactingFilter


def configure_logging(log_dir: Path, level: int = logging.INFO) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    with suppress(OSError):
        log_dir.chmod(0o700)
    root = logging.getLogger()
    root.setLevel(level)

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    redactor = RedactingFilter()

    has_file_handler = any(
        isinstance(handler, RotatingFileHandler)
        and Path(getattr(handler, "baseFilename", "")) == (log_dir / "agent.log").resolve()
        for handler in root.handlers
    )
    if not has_file_handler:
        log_path = log_dir / "agent.log"
        with suppress(OSError):
            log_path.chmod(0o600)
        with suppress(OSError):
            descriptor = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            os.close(descriptor)
        file_handler = RotatingFileHandler(
            log_dir / "agent.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.addFilter(redactor)
        with suppress(OSError):
            Path(file_handler.baseFilename).chmod(0o600)
        root.addHandler(file_handler)

    for rotated in log_dir.glob("agent.log*"):
        with suppress(OSError):
            rotated.chmod(0o600)

    for handler in root.handlers:
        if not any(item is redactor for item in handler.filters):
            handler.addFilter(redactor)

    # Socket payloads may contain the OTP. Never let third-party debug output persist them.
    if not any(item is redactor for item in root.filters):
        root.addFilter(redactor)
    for name in ("slack_bolt", "slack_sdk", "pywinauto", "asyncio"):
        logger = logging.getLogger(name)
        logger.setLevel(max(level, logging.WARNING))
        if not any(item is redactor for item in logger.filters):
            logger.addFilter(redactor)
