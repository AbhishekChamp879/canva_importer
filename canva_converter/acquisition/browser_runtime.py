from __future__ import annotations

import asyncio
from pathlib import Path
import re
import subprocess
import sys
from threading import Lock
from typing import Any, Callable

from ..errors import AcquisitionError


Progress = Callable[[str], None]
SYSTEM_BROWSER_CHANNELS = ("chrome", "msedge")
_INSTALL_LOCK = Lock()
_INSTALL_COMPLETED = False


def _short_error(error: Exception | str, limit: int = 1200) -> str:
    return re.sub(r"\s+", " ", str(error)).strip()[:limit] or "Unknown browser error"


def browser_is_missing(error: Exception) -> bool:
    message = str(error).casefold()
    return any(fragment in message for fragment in (
        "executable doesn't exist",
        "executable does not exist",
        "please run the following command",
        "playwright install",
        "could not find browser",
        "is not found at",
        "distribution 'chrome' is not found",
        "distribution 'msedge' is not found",
    ))


def install_managed_chromium(timeout_seconds: int = 900) -> None:
    """Install Playwright's pinned Chromium once for this Python environment."""
    global _INSTALL_COMPLETED
    with _INSTALL_LOCK:
        if _INSTALL_COMPLETED:
            return
        command = [sys.executable, "-m", "playwright", "install", "chromium"]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise AcquisitionError(
                "CAPTURE_SETUP",
                f"Portable Chromium installation exceeded {timeout_seconds} seconds. Run: {sys.executable} -m playwright install chromium",
            ) from error
        except OSError as error:
            raise AcquisitionError(
                "CAPTURE_SETUP",
                f"Could not start Playwright browser installation: {_short_error(error)}",
            ) from error
        if result.returncode != 0:
            detail = _short_error(result.stderr or result.stdout)
            raise AcquisitionError(
                "CAPTURE_SETUP",
                f"Could not install portable Chromium. {detail} Run: {sys.executable} -m playwright install chromium",
            )
        _INSTALL_COMPLETED = True


class PortableBrowserRuntime:
    """Launch a browser without assuming an operating-system-specific path."""

    def __init__(
        self,
        configured_executable: str | None = None,
        auto_install: bool = True,
        install_timeout_seconds: int = 900,
    ):
        self.configured_executable = configured_executable
        self.auto_install = auto_install
        self.install_timeout_seconds = install_timeout_seconds

    def configured_path(self) -> str | None:
        if not self.configured_executable:
            return None
        path = Path(self.configured_executable).expanduser()
        if not path.is_file():
            raise AcquisitionError(
                "CAPTURE_SETUP",
                f"Optional PLAYWRIGHT_EXECUTABLE_PATH does not point to a browser executable: {path}",
            )
        return str(path.resolve())

    async def launch(self, chromium: Any, launch_options: dict[str, Any], progress: Progress):
        configured = self.configured_path()
        if configured:
            progress("[launching] Using the configured browser executable.")
            try:
                return await chromium.launch(**launch_options, executable_path=configured)
            except Exception as error:
                raise AcquisitionError("CAPTURE_SETUP", f"Configured browser failed to launch: {_short_error(error)}") from error

        failures: list[str] = []
        try:
            browser = await chromium.launch(**launch_options)
            progress("[launching] Using Playwright-managed Chromium.")
            return browser
        except Exception as error:
            failures.append(f"managed Chromium: {_short_error(error, 300)}")
            if not browser_is_missing(error):
                raise AcquisitionError("CAPTURE_SETUP", f"Playwright Chromium failed to launch: {_short_error(error)}") from error

        # Playwright resolves these channels using platform-native discovery on
        # Windows, macOS, and Linux, so no installation path is hardcoded here.
        for channel in SYSTEM_BROWSER_CHANNELS:
            try:
                browser = await chromium.launch(**launch_options, channel=channel)
                progress(f"[launching] Using system browser channel: {channel}.")
                return browser
            except Exception as error:
                failures.append(f"{channel}: {_short_error(error, 300)}")
                if not browser_is_missing(error):
                    continue

        if not self.auto_install:
            raise AcquisitionError(
                "CAPTURE_SETUP",
                "No compatible browser is available and automatic installation is disabled. "
                f"Run: {sys.executable} -m playwright install chromium",
            )

        progress("[setup] Installing Playwright-managed Chromium for this user. This happens only once.")
        await asyncio.to_thread(install_managed_chromium, self.install_timeout_seconds)
        try:
            browser = await chromium.launch(**launch_options)
            progress("[launching] Portable Chromium installed and ready.")
            return browser
        except Exception as error:
            failures.append(f"installed Chromium: {_short_error(error, 300)}")
            detail = "; ".join(failures[-3:])
            raise AcquisitionError(
                "CAPTURE_SETUP",
                "Chromium was installed but could not launch. On Linux, system libraries may be missing; "
                f"run `{sys.executable} -m playwright install --with-deps chromium`. Details: {detail}",
            ) from error
