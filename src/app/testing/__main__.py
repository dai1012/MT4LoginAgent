"""CLI for safe Windows acceptance phases.

Real login is intentionally not available from the CLI without the Web confirmation
flow, so a shell history or process command line can never contain an OTP.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import threading
import webbrowser
from contextlib import suppress

from app.config.paths import AppPaths
from app.config.repositories import SettingsRepository
from app.runtime import build_runtime
from app.testing.models import TestPhase
from app.testing.runner import TestRunner


def web_url_for_data_dir(data_dir: str | None) -> str:
    paths = AppPaths.from_value(data_dir)
    port = SettingsRepository(paths).get().web_port
    return f"http://127.0.0.1:{port}/#windows-test"


def _open_web_after_delay(url: str) -> None:
    def opener() -> None:
        with suppress(Exception):
            webbrowser.open(url, new=2)

    threading.Timer(2.0, opener).start()


def _run_web(data_dir: str | None) -> None:
    import app.main

    _open_web_after_delay(web_url_for_data_dir(data_dir))
    sys.argv = ["app.main", "--data-dir", str(data_dir or AppPaths.resolve_default().data_dir)]
    app.main.run()


async def _run_safe_cli(data_dir: str | None, phase: str, account_id: str | None) -> int:
    runtime = build_runtime(data_dir)
    runner = TestRunner(runtime)
    try:
        results = await runner.run_phase(TestPhase(phase), account_id=account_id)
        payload = {
            "report_id": runner.report_id,
            "results": [item.safe_dict() for item in results],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if all(item.status.value not in {"FAIL"} for item in results) else 1
    finally:
        await runtime.stop()


def main() -> int:
    parser = argparse.ArgumentParser(description="Rakuten MT4 Windows acceptance tests")
    parser.add_argument("--data-dir", default=None, help="Runtime data directory")
    parser.add_argument(
        "--phase",
        choices=[TestPhase.ENVIRONMENT.value, TestPhase.DISCOVERY.value, TestPhase.SLACK.value],
        default=TestPhase.ENVIRONMENT.value,
    )
    parser.add_argument("--account", default=None, help="Account ID for discovery")
    parser.add_argument(
        "--open-web", action="store_true", help="Start Web Admin and open Windows Test"
    )
    args = parser.parse_args()
    if args.open_web:
        _run_web(args.data_dir)
        return 0
    return asyncio.run(_run_safe_cli(args.data_dir, args.phase, args.account))


if __name__ == "__main__":
    raise SystemExit(main())
