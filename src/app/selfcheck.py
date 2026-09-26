from __future__ import annotations

import sys


def main() -> None:
    import app.main  # noqa: F401

    if sys.platform == "win32":
        import psutil  # noqa: F401

        if hasattr(sys, "coinit_flags"):
            sys.coinit_flags = 2
        import pywinauto  # noqa: F401

        if getattr(pywinauto, "UIA_support", None) is False:
            raise RuntimeError(
                "pywinauto UIA backend is unavailable (comtypes/COM support missing)"
            )
        from pywinauto import Desktop

        Desktop(backend="uia")
    print("self-check: ok")


if __name__ == "__main__":
    main()
