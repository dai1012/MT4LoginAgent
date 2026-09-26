from __future__ import annotations

import sys

from app.models.domain import AppSettings, AutomationMode
from app.models.errors import ConfigurationError
from app.mt4.interface import MT4Automation
from app.mt4.mock_automation import MockAutomation


def build_automation(settings: AppSettings) -> MT4Automation:
    mode = settings.automation_mode
    if mode == AutomationMode.MOCK:
        return MockAutomation()
    if mode == AutomationMode.WINDOWS and sys.platform != "win32":
        raise ConfigurationError(
            "automation_mode=windows is only available on Windows; "
            "use auto or mock on this platform"
        )
    if mode == AutomationMode.AUTO and sys.platform != "win32":
        return MockAutomation()
    from app.mt4.windows_automation import WindowsAutomation

    return WindowsAutomation()
