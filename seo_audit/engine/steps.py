"""Étapes d'un audit : init (robots.txt, sitemaps) → crawl → sitemap (pages orphelines) → finalisation.

Chaque appel à `step_audit` travaille au plus `budget` secondes puis rend la main : on le rappelle
tant que l'audit n'est pas terminé (planificateur Supabase, ou navigateur ouvert sur le tableau de bord)."""
import asyncio
import logging
import time
from collections import defaultdict
from types import SimpleNamespace

import httpx

from ..checks import run_checks
from ..crawler import DEFAULT_UA, Crawler
from ..models import PageResult
from ..report import build_report
from ..urls import normalize_url, site_key

log = logging.getLogger(__name__)
BATCH = 10
DEFAULT_BUDGET = 40.0  # secondes ; à régler sous la durée maximale de la fonction


def make_crawler(audit: dict, url_guard=None) -> Crawler:
    o = audit["options"]
    return Crawler(audit["url"], max_pages=o.get("max_pages", 200), max_depth=o.get("max_depth", 10),
                   concurrency=o.get("concurrency", 5), delay=0.0, user_agent=o.get("user_agent", DEFAULT_UA),
                   respect_robots=not o.get("ignore_robots", False), use_sitemap=not o.get("no_sitemap", False),
                   url_guard=url_guard)


def _client(crawler: Crawler) -> httpx.AsyncClient:
    headers = {"User-Agent": crawler.user_agent, "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8"}
    return httpx.AsyncClient(headers=headers, timeout=crawler.timeout, follow_redirects=False,
                             limits=httpx.Limits(max_connections=crawler.concurrency * 2))


async def step_audit(store, audit_id: str, *, budget: float = DEFAULT_BUDGET, url_guard=None) -> dict:
    """Avance l'audit pendant `budget` secondes au plus. Renvoie l'audit à jour."""
    deadline = time.monotonic() + budget
    audit = store.get_audit(audit_id)
    if audit["status"] in ("done", "failed", "cancelled"):
        return audit
    if audit["status"] == "queued":
        store.update_audit(audit_id, status="running", started_at=time.time())
        audit = store.get_audit(audit_id)
    try:
        while time.monotonic() < deadline and audit["status"] == "running":
            phase = audit["phase"]
            if phase == "init":
                await _init(store, audit, url_guard)
            elif phase in ("crawl", "sitemap"):
                await _crawl(store, audit, deadline, url_guard)
            elif phase == "finalize":
                _finalize(store, audit)
            audit = store.get_audit(audit_id)
    except Exception as exc:  # un audit en échec ne doit pas bloquer les suivants
        log.exception("Audit %s en échec", audit_id)
        store.update_audit(audit_id, status="failed", error=f"{type(exc).__name__}: {exc}", finished_at=time.time())
        audit = store.get_audit(audit_id)
    return audit


async def run_tick(store, *, budget: float = DEFAULT_BUDGET, url_guard=None) -> list[str]:
    """Passe de planification : fait avancer les audits en cours, le plus ancien d'abord."""
    deadline = time.monotonic() + budget
    touched = []
    for audit_id in store.active_audits():
        left = deadline - time.monotonic()
        if left < 3:
            break
        await step_audit(store, audit_id, budget=left, url_guard=url_guard)
        touched.append(audit_id)
    return touched


async def _init(store, audit, url_guard):
    crawler = make_crawler(audit, url_guard)
    async with _client(crawler) as client:
        crawler.client = client
        await crawler._load_robots()
        if crawler.use_sitemap:
            await crawler._load_sitemaps()
    store.add_pending(audit["id"], [(crawler.start_url, 0)], crawler.max_pages)
    store.update_audit(audit["id"], phase="crawl", robots_text=crawler.robots_text,
                       sitemap_urls=sorted(crawler.sitemap_urls))


async def _crawl(store, audit, deadline, url_guard):
    crawler = make_crawler(audit, url_guard)
    if audit.get("robots_text"):
        crawler.set_robots(audit["robots_text"])
    sem = asyncio.Semaphore(crawler.concurrency)
    async with _client(crawler) as client:
        crawler.client = client

        async def one(item):
            page = PageResult(url=item["url"], depth=item["depth"])
            async with sem:
                try:
                    await crawler._fetch(page)
                except Exception as exc:  # une page ne doit jamais arrêter l'audit
                    page.error = f"{type(exc).__name__}: {exc}"
            return page

        while time.monotonic() < deadline:
            claimed = store.claim_pending(audit["id"], BATCH)
            if not claimed:
                break
            pages = await asyncio.gather(*(one(c) for c in claimed))
            store.save_pages(audit["id"], [(p.url, p.to_dict()) for p in pages])
            store.add_pending(audit["id"], [item for p in pages for item in crawler.discover(p)], crawler.max_pages)

    if store.counts(audit["id"])["pending"] == 0:
        if audit["phase"] == "crawl" and audit["sitemap_urls"]:
            # pages du sitemap jamais atteintes par les liens : orphelines potentielles
            store.add_pending(audit["id"], [(u, None) for u in audit["sitemap_urls"]], crawler.max_pages)
            store.update_audit(audit["id"], phase="sitemap")
        else:
            store.update_audit(audit["id"], phase="finalize")


def _finalize(store, audit):
    results = {url: PageResult.from_dict(d) for url, d in store.load_pages(audit["id"])}
    start = normalize_url(audit["url"])
    inlinks, external = defaultdict(set), defaultdict(set)
    for url, p in results.items():
        for t in p.internal_links:
            if t != url:
                inlinks[t].add(url)
        for t in p.external_links:
            external[t].add(url)
    sitemap = set(audit["sitemap_urls"])
    for u in sitemap & results.keys():
        results[u].in_sitemap = True
    crawler = SimpleNamespace(results=results, inlinks=inlinks, external_sources=external,
                              sitemap_urls=sitemap, start_url=start, site=site_key(start))
    issues = run_checks(crawler, {})
    previous = store.previous_report(audit["user_id"], audit["site"], audit["id"])
    meta = {"options": {k: str(v) for k, v in audit["options"].items()}}
    summary, issues, rows = build_report(crawler, issues, meta, previous)
    store.save_report(audit["id"], summary, [i.__dict__ for i in issues], rows)
    store.update_audit(audit["id"], status="done", finished_at=time.time(), summary=summary)
    store.purge_pages(audit["id"])
