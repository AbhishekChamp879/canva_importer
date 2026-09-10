from __future__ import annotations

import asyncio

from playwright.async_api import async_playwright

from canva_converter.acquisition.browser_runtime import install_managed_chromium
from canva_converter.config import Settings


async def verify() -> None:
    settings = Settings.load()
    install_managed_chromium(settings.browser_install_timeout_seconds)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        await browser.close()
    print("Playwright-managed Chromium is installed and launches successfully.")


if __name__ == "__main__":
    asyncio.run(verify())
