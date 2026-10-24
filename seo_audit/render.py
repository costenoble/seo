"""Rendu JavaScript optionnel avec Playwright (pip install playwright && playwright install chromium)."""
import asyncio
import logging

log = logging.getLogger(__name__)


class PlaywrightRenderer:
    def __init__(self, user_agent: str, concurrency: int = 2, timeout_ms: int = 30000):
        self.user_agent, self.timeout_ms = user_agent, timeout_ms
        self._sem = asyncio.Semaphore(concurrency)

    async def __aenter__(self):
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise SystemExit("Playwright n'est pas installé : pip install playwright && "
                             "playwright install chromium") from exc
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch()
        self._context = await self._browser.new_context(user_agent=self.user_agent)
        return self

    async def __aexit__(self, *exc):
        await self._context.close()
        await self._browser.close()
        await self._pw.stop()

    async def render(self, url: str) -> str | None:
        async with self._sem:
            page = await self._context.new_page()
            try:
                await page.goto(url, wait_until="networkidle", timeout=self.timeout_ms)
                return await page.content()
            except Exception as exc:
                log.warning("Rendu JS impossible pour %s : %s", url, exc)
                return None
            finally:
                await page.close()
