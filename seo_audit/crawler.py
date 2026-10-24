"""Crawler asynchrone : suit les liens internes, lit robots.txt et les sitemaps.

Les redirections ne sont pas suivies automatiquement : chaque saut est enregistré
comme une page à part, ce qui permet de reconstituer les chaînes et les boucles."""
import asyncio
import gzip
import logging
import time
from collections import defaultdict
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from defusedxml import ElementTree as ET

from .models import PageResult
from .parser import parse_html
from .urls import is_http, normalize_url, site_key

log = logging.getLogger(__name__)

DEFAULT_UA = "SEOAuditBot/0.1 (+audit interne)"
MAX_HTML_BYTES = 5 * 1024 * 1024
MAX_SITEMAPS = 50


class Crawler:
    def __init__(self, start_url: str, *, max_pages: int = 500, max_depth: int = 10,
                 concurrency: int = 5, delay: float = 0.0, user_agent: str = DEFAULT_UA,
                 respect_robots: bool = True, use_sitemap: bool = True,
                 timeout: float = 15.0, renderer=None, url_guard=None):
        self.start_url = normalize_url(start_url)
        self.site = site_key(self.start_url)
        parts = urlsplit(self.start_url)
        self.origin = f"{parts.scheme}://{parts.netloc}"
        self.max_pages, self.max_depth = max_pages, max_depth
        self.concurrency, self.delay = concurrency, delay
        self.user_agent, self.timeout = user_agent, timeout
        self.respect_robots, self.use_sitemap = respect_robots, use_sitemap
        self.renderer = renderer
        self.url_guard = url_guard  # callable(url) qui lève ValueError pour refuser une URL (SSRF)

        self.results: dict[str, PageResult] = {}
        self.inlinks: dict[str, set[str]] = defaultdict(set)
        self.external_sources: dict[str, set[str]] = defaultdict(set)
        self.sitemap_urls: set[str] = set()
        self.robots: RobotFileParser | None = None
        self.robots_sitemaps: list[str] = []
        self.robots_text = ""
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._done = 0

    def is_internal(self, url: str) -> bool:
        return site_key(url) == self.site

    # -- orchestration -----------------------------------------------------
    async def run(self) -> dict[str, PageResult]:
        headers = {"User-Agent": self.user_agent,
                   "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8"}
        limits = httpx.Limits(max_connections=self.concurrency * 2)
        async with httpx.AsyncClient(headers=headers, timeout=self.timeout,
                                     follow_redirects=False, limits=limits) as client:
            self.client = client
            await self._load_robots()
            if self.use_sitemap:
                await self._load_sitemaps()

            self._enqueue(self.start_url, 0)
            await self._drain()
            # Pages du sitemap jamais atteintes par les liens : pages orphelines potentielles
            for url in sorted(self.sitemap_urls):
                self._enqueue(url, None)
            await self._drain()

        for url in self.sitemap_urls:
            if url in self.results:
                self.results[url].in_sitemap = True
        log.info("Crawl terminé : %d URLs", len(self.results))
        return self.results

    def _enqueue(self, url: str, depth: int | None) -> None:
        if url in self.results or len(self.results) >= self.max_pages:
            return
        if depth is not None and depth > self.max_depth:
            return
        self.results[url] = PageResult(url=url, depth=depth)
        self._queue.put_nowait(url)

    async def _drain(self) -> None:
        workers = [asyncio.create_task(self._worker()) for _ in range(self.concurrency)]
        await self._queue.join()
        for w in workers:
            w.cancel()
        await asyncio.gather(*workers, return_exceptions=True)

    async def _worker(self) -> None:
        while True:
            url = await self._queue.get()
            try:
                await self._process(url)
            except Exception as exc:  # une page ne doit jamais arrêter le crawl
                self.results[url].error = f"{type(exc).__name__}: {exc}"
                log.debug("Erreur sur %s", url, exc_info=True)
            finally:
                self._done += 1
                if self._done % 25 == 0:
                    log.info("%d pages traitées, %d en file", self._done, self._queue.qsize())
                self._queue.task_done()
            if self.delay:
                await asyncio.sleep(self.delay)

    # -- une page ------------------------------------------------------------
    async def _process(self, url: str) -> None:
        page = self.results[url]
        await self._fetch(page)
        for target, depth in self.discover(page):
            self._enqueue(target, depth)
        for target in page.internal_links:
            if target != url:
                self.inlinks[target].add(url)
        for target in page.external_links:
            self.external_sources[target].add(url)

    def discover(self, page: PageResult) -> list[tuple[str, int | None]]:
        """URLs internes à explorer ensuite : cible d'une redirection, puis liens de la page."""
        child_depth = page.depth + 1 if page.depth is not None else None
        found = []
        if page.redirect_to and self.is_internal(page.redirect_to):
            found.append((page.redirect_to, page.depth))
        found.extend((t, child_depth) for t in page.internal_links)
        return found

    async def _fetch(self, page: PageResult) -> None:
        """Télécharge et analyse une page ; remplit `page` sans toucher à la file ni aux liens entrants."""
        url = page.url
        if self.respect_robots and self.robots and not self.robots.can_fetch(self.user_agent, url):
            page.blocked_by_robots = True
            return
        if self.url_guard:
            try:
                self.url_guard(url)
            except ValueError as exc:
                page.error = f"URL refusée : {exc}"
                return

        t0 = time.perf_counter()
        html = None
        try:
            async with self.client.stream("GET", url) as resp:
                page.ttfb_ms = round((time.perf_counter() - t0) * 1000, 1)
                page.status = resp.status_code
                page.content_type = resp.headers.get("content-type", "")
                page.x_robots_tag = ", ".join(resp.headers.get_list("x-robots-tag"))
                location = resp.headers.get("location")
                if 300 <= resp.status_code < 400 and location:
                    page.redirect_to = normalize_url(urljoin(url, location))
                if resp.status_code == 200 and "html" in page.content_type.lower():
                    html = await self._read_html(resp)
        except httpx.HTTPError as exc:
            page.error = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            return
        if html is None:
            return

        page.parsed = parse_html(html, url)
        if self.renderer:
            rendered = await self.renderer.render(url)
            if rendered:
                page.parsed_raw = page.parsed
                page.parsed = parse_html(rendered, url)

        internal, external = {}, {}
        for link, _nofollow in page.parsed.links:
            if not is_http(link):
                continue
            norm = normalize_url(link)
            (internal if self.is_internal(norm) else external)[norm] = True
        page.internal_links, page.external_links = list(internal), list(external)

    @staticmethod
    async def _read_html(resp: httpx.Response) -> str:
        chunks, size = [], 0
        async for chunk in resp.aiter_bytes():
            size += len(chunk)
            if size > MAX_HTML_BYTES:
                break
            chunks.append(chunk)
        raw = b"".join(chunks)
        try:
            return raw.decode(resp.encoding or "utf-8", errors="replace")
        except LookupError:
            return raw.decode("utf-8", errors="replace")

    # -- robots.txt et sitemaps ----------------------------------------------
    async def _get_following(self, url: str, max_hops: int = 5) -> httpx.Response:
        """GET avec redirections suivies à la main, pour que le garde-fou s'applique à chaque saut."""
        for _ in range(max_hops + 1):
            if self.url_guard:
                self.url_guard(url)
            r = await self.client.get(url)
            location = r.headers.get("location")
            if not (300 <= r.status_code < 400 and location):
                return r
            url = urljoin(url, location)
        raise ValueError("trop de redirections")

    async def _load_robots(self) -> None:
        robots_url = f"{self.origin}/robots.txt"
        try:
            r = await self._get_following(robots_url)
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("robots.txt inaccessible (%s), crawl sans restriction", exc)
            return
        if r.status_code != 200:
            if r.status_code >= 500:
                log.warning("robots.txt renvoie %d : Google peut suspendre le crawl du site", r.status_code)
            return
        self.set_robots(r.text)

    def set_robots(self, text: str) -> None:
        self.robots_text = text
        rp = RobotFileParser()
        rp.parse(text.splitlines())
        self.robots = rp
        self.robots_sitemaps = list(rp.site_maps() or [])

    async def _load_sitemaps(self) -> None:
        to_visit = list(self.robots_sitemaps) or [f"{self.origin}/sitemap.xml"]
        seen: set[str] = set()
        while to_visit and len(seen) < MAX_SITEMAPS:
            sm = to_visit.pop()
            if sm in seen:
                continue
            seen.add(sm)
            try:
                r = await self._get_following(sm)
                if r.status_code != 200:
                    log.warning("Sitemap %s : HTTP %d", sm, r.status_code)
                    continue
                content = r.content
                if content[:2] == b"\x1f\x8b":
                    content = gzip.decompress(content)
                root = ET.fromstring(content)
            except Exception as exc:
                log.warning("Sitemap %s illisible : %s", sm, exc)
                continue
            locs = [el.text.strip() for el in root.iter()
                    if el.tag.rsplit("}", 1)[-1] == "loc" and el.text]
            if root.tag.rsplit("}", 1)[-1] == "sitemapindex":
                to_visit.extend(locs)
            else:
                for loc in locs:
                    norm = normalize_url(loc)
                    if self.is_internal(norm):
                        self.sitemap_urls.add(norm)
        log.info("Sitemaps : %d URLs trouvées", len(self.sitemap_urls))
