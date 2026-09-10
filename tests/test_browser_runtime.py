from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from canva_converter.acquisition.browser_runtime import PortableBrowserRuntime, browser_is_missing
from canva_converter.errors import AcquisitionError


class FakeChromium:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    async def launch(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class PortableBrowserRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_uses_playwright_managed_chromium_without_a_path(self):
        browser = object()
        chromium = FakeChromium([browser])

        result = await PortableBrowserRuntime().launch(chromium, {"headless": True}, lambda _message: None)

        self.assertIs(result, browser)
        self.assertEqual(chromium.calls, [{"headless": True}])

    async def test_discovers_system_chrome_through_cross_platform_channel(self):
        browser = object()
        missing = RuntimeError("Executable doesn't exist. Please run playwright install")
        chromium = FakeChromium([missing, browser])

        result = await PortableBrowserRuntime().launch(chromium, {"headless": True}, lambda _message: None)

        self.assertIs(result, browser)
        self.assertEqual(chromium.calls[1]["channel"], "chrome")
        self.assertNotIn("executable_path", chromium.calls[1])

    @patch("canva_converter.acquisition.browser_runtime.install_managed_chromium")
    async def test_installs_managed_chromium_once_when_no_browser_exists(self, installer):
        missing = RuntimeError("Executable doesn't exist. Please run playwright install")
        browser = object()
        chromium = FakeChromium([missing, missing, missing, browser])

        result = await PortableBrowserRuntime(install_timeout_seconds=123).launch(
            chromium,
            {"headless": True},
            lambda _message: None,
        )

        self.assertIs(result, browser)
        installer.assert_called_once_with(123)
        self.assertNotIn("channel", chromium.calls[-1])

    async def test_reports_actionable_error_when_auto_install_is_disabled(self):
        missing = RuntimeError("Executable doesn't exist. Please run playwright install")
        chromium = FakeChromium([missing, missing, missing])

        with self.assertRaisesRegex(AcquisitionError, "automatic installation is disabled") as raised:
            await PortableBrowserRuntime(auto_install=False).launch(chromium, {}, lambda _message: None)

        self.assertEqual(raised.exception.code, "CAPTURE_SETUP")

    async def test_configured_path_remains_an_optional_override(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "browser"
            executable.touch()
            browser = object()
            chromium = FakeChromium([browser])

            result = await PortableBrowserRuntime(str(executable)).launch(chromium, {}, lambda _message: None)

        self.assertIs(result, browser)
        self.assertEqual(Path(chromium.calls[0]["executable_path"]), executable.resolve())

    async def test_invalid_override_fails_before_launching(self):
        chromium = FakeChromium([])

        with self.assertRaisesRegex(AcquisitionError, "does not point to a browser executable"):
            await PortableBrowserRuntime("missing-browser").launch(chromium, {}, lambda _message: None)

        self.assertEqual(chromium.calls, [])

    def test_missing_browser_detection_does_not_hide_other_launch_failures(self):
        self.assertTrue(browser_is_missing(RuntimeError("Executable doesn't exist")))
        self.assertFalse(browser_is_missing(RuntimeError("Host system is missing dependencies")))


if __name__ == "__main__":
    unittest.main()
