from __future__ import annotations

import asyncio
import random

from playwright.async_api import BrowserContext, async_playwright
from playwright_stealth import Stealth

FETCH_ATTEMPTS = 3
FETCH_RETRY_BACKOFF_SECONDS = 5


class Poller:
    def __init__(self, user_data_dir: str, headless: bool = False):
        self._user_data_dir = user_data_dir
        self._headless = headless
        self._playwright = None
        self._context: BrowserContext | None = None

    async def start(self) -> None:
        self._playwright = await async_playwright().start()
        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=self._user_data_dir,
            channel="chrome",
            headless=self._headless,
        )

    async def stop(self) -> None:
        if self._context is not None:
            await self._context.close()
        if self._playwright is not None:
            await self._playwright.stop()

    async def fetch_search_html(self, url: str) -> str:
        assert self._context is not None, "call start() before fetch_search_html()"
        last_exc: Exception | None = None
        for attempt in range(FETCH_ATTEMPTS):
            page = await self._context.new_page()
            try:
                await Stealth().apply_stealth_async(page)
                response = await page.goto(url, wait_until="domcontentloaded")
                # page.goto only raises on network-level failures (DNS, timeout,
                # SSL) — an HTTP error status like a 502 is returned normally and
                # must be checked explicitly, or it would silently be treated as
                # "a valid page with zero listings" instead of a retryable failure.
                if response is not None and response.status >= 500:
                    raise RuntimeError(f"upstream error {response.status} for {url}")
                try:
                    await page.wait_for_selector(
                        '[data-testid^="listing-card-"]', state="attached", timeout=15000
                    )
                except Exception:
                    pass  # genuinely empty results or a challenge page — let the
                    # caller's parser/challenge-detector interpret whatever HTML
                    # actually came back
                await page.wait_for_timeout(random.uniform(500, 1500))
                return await page.content()
            except Exception as e:
                last_exc = e
                if attempt < FETCH_ATTEMPTS - 1:
                    await asyncio.sleep(FETCH_RETRY_BACKOFF_SECONDS * (attempt + 1))
            finally:
                await page.close()
        assert last_exc is not None
        raise last_exc
