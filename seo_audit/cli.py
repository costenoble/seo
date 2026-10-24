"""Point d'entrée : python -m seo_audit https://www.example.com [options]"""
import argparse
import asyncio
import json
import logging
import os
from contextlib import AsyncExitStack
from datetime import datetime
from pathlib import Path

from .checks import run_checks
from .crawler import DEFAULT_UA, Crawler
from .models import SEVERITIES
from .report import write_reports
from .urls import site_key


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="seo_audit", description="Audit SEO technique d'un site.")
    p.add_argument("url", help="URL de départ, par ex. https://www.example.com/")
    g = p.add_argument_group("crawl")
    g.add_argument("--max-pages", type=int, default=500)
    g.add_argument("--max-depth", type=int, default=10)
    g.add_argument("--concurrency", type=int, default=5, help="requêtes simultanées (défaut 5)")
    g.add_argument("--delay", type=float, default=0.0, help="pause en secondes après chaque page, par worker")
    g.add_argument("--user-agent", default=DEFAULT_UA)
    g.add_argument("--ignore-robots", action="store_true", help="ne pas respecter robots.txt")
    g.add_argument("--no-sitemap", action="store_true", help="ne pas lire les sitemaps")
    g.add_argument("--render-js", action="store_true", help="rendre les pages avec Playwright")
    g = p.add_argument_group("liens")
    g.add_argument("--check-external", action="store_true", help="vérifier les liens externes")
    g = p.add_argument_group("performance")
    g.add_argument("--pagespeed", type=int, default=0, metavar="N",
                   help="mesurer les N pages les plus liées (0 = désactivé)")
    g.add_argument("--psi-key", default=os.getenv("PSI_API_KEY"), help="clé API PageSpeed (ou PSI_API_KEY)")
    g.add_argument("--lighthouse-local", action="store_true", help="Lighthouse local au lieu de PageSpeed")
    g.add_argument("--strategy", choices=["mobile", "desktop"], default="mobile")
    g = p.add_argument_group("Search Console")
    g.add_argument("--gsc-site", help="propriété GSC : https://www.example.com/ ou sc-domain:example.com")
    g.add_argument("--gsc-credentials", default=os.getenv("GOOGLE_APPLICATION_CREDENTIALS"),
                   help="fichier JSON du compte de service")
    g.add_argument("--gsc-max", type=int, default=100, help="nombre maximum d'URLs inspectées")
    g = p.add_argument_group("sortie")
    g.add_argument("--output", type=Path, help="dossier de sortie (défaut : rapports/<site>-<date>)")
    g.add_argument("--fail-on", choices=[*SEVERITIES[:3], "never"], default="never",
                   help="code de sortie 1 si un problème de cette gravité ou plus est trouvé (CI)")
    g.add_argument("--compare", type=Path, metavar="AUDIT.JSON",
                   help="audit précédent à comparer (défaut : dernier audit du même site dans rapports/)")
    g.add_argument("--no-compare", action="store_true", help="ne pas comparer avec l'audit précédent")
    g.add_argument("-v", "--verbose", action="store_true")
    return p


def priority_pages(crawler, n: int) -> list[str]:
    """Pages indexables les plus liées en interne, l'accueil en premier."""
    pages = [u for u, p in crawler.results.items() if p.indexable]
    pages.sort(key=lambda u: (u != crawler.start_url, -len(crawler.inlinks.get(u, ()))))
    return pages[:n]


async def run(args) -> int:
    log = logging.getLogger("seo_audit")
    async with AsyncExitStack() as stack:
        renderer = None
        if args.render_js:
            from .render import PlaywrightRenderer
            renderer = await stack.enter_async_context(PlaywrightRenderer(args.user_agent))
        crawler = Crawler(args.url, max_pages=args.max_pages, max_depth=args.max_depth,
                          concurrency=args.concurrency, delay=args.delay, user_agent=args.user_agent,
                          respect_robots=not args.ignore_robots, use_sitemap=not args.no_sitemap,
                          renderer=renderer)
        log.info("Crawl de %s (max %d pages)", crawler.start_url, args.max_pages)
        await crawler.run()

    external = {}
    if args.check_external and crawler.external_sources:
        from .links import check_external_links
        log.info("Vérification de %d liens externes", len(crawler.external_sources))
        external = await check_external_links(sorted(crawler.external_sources), user_agent=args.user_agent)

    if args.pagespeed:
        from .performance import run_lighthouse_local, run_pagespeed
        targets = priority_pages(crawler, args.pagespeed)
        log.info("Mesure de performance sur %d pages", len(targets))
        if args.lighthouse_local:
            perf = await run_lighthouse_local(targets, strategy=args.strategy)
        else:
            perf = await run_pagespeed(targets, api_key=args.psi_key, strategy=args.strategy)
        for url, data in perf.items():
            crawler.results[url].performance = data

    if args.gsc_site:
        from .gsc import inspect_urls
        targets = priority_pages(crawler, args.gsc_max)
        log.info("Inspection Search Console de %d URLs", len(targets))
        gsc = await inspect_urls(targets, site_url=args.gsc_site, credentials_file=args.gsc_credentials)
        for url, data in gsc.items():
            crawler.results[url].gsc = data

    issues = run_checks(crawler, external)
    out = args.output or Path("rapports") / f"{site_key(crawler.start_url)}-{datetime.now():%Y%m%d-%H%M}"
    previous = None
    if not args.no_compare:
        from .compare import find_previous
        previous = args.compare or find_previous(Path("rapports"), site_key(crawler.start_url), out)
    paths = write_reports(crawler, issues, out, previous=previous, meta={
        "options": {k: str(v) for k, v in vars(args).items()
                    if k not in ("psi_key", "gsc_credentials")},
    })

    counts = {s: sum(1 for i in issues if i.severity == s) for s in SEVERITIES}
    print(f"\n{len(crawler.results)} URLs, {len(issues)} problèmes : "
          + ", ".join(f"{s} {n}" for s, n in counts.items() if n))
    print(f"Rapport : {paths['html']}")
    cmp = json.loads(paths["json"].read_text(encoding="utf-8"))["summary"].get("comparison")
    if cmp:
        print(f"Depuis le {cmp['previous_date']} : score {cmp['score_delta']:+d}, "
              f"{cmp['new_count']} nouveaux problèmes, {cmp['fixed_count']} corrigés")

    if args.fail_on != "never":
        threshold = SEVERITIES.index(args.fail_on)
        if any(SEVERITIES.index(i.severity) <= threshold for i in issues):
            return 1
    return 0


def main(argv=None) -> int:
    import sys
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "serve":
        sp = argparse.ArgumentParser(prog="seo_audit serve", description="Tableau de bord web local.")
        sp.add_argument("--port", type=int, default=8765)
        sp.add_argument("--root", type=Path, default=Path("."), help="dossier contenant rapports/ et data/")
        sargs = sp.parse_args(argv[1:])
        os.chdir(sargs.root)
        from .web.app import serve
        serve(port=sargs.port)
        return 0
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return asyncio.run(run(args))
