"""Performance via l'API PageSpeed Insights (Lighthouse hébergé + données terrain CrUX),
ou via Lighthouse en local (npx lighthouse, nécessite Node.js et Chrome)."""
import asyncio
import json
import logging

import httpx

log = logging.getLogger(__name__)
PSI_ENDPOINT = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"


def extract_metrics(data: dict, source: str) -> dict:
    lh = data.get("lighthouseResult", {})
    audits = lh.get("audits", {})
    score = lh.get("categories", {}).get("performance", {}).get("score")

    def lab(key):
        return audits.get(key, {}).get("numericValue")

    experience = data.get("loadingExperience") or {}
    field = experience.get("metrics", {})

    def pct(key):
        return field.get(key, {}).get("percentile")

    cls_field = pct("CUMULATIVE_LAYOUT_SHIFT_SCORE")
    return {
        "source": source,
        "score": round(score * 100) if score is not None else None,
        "lab": {
            "lcp_ms": lab("largest-contentful-paint"),
            "cls": lab("cumulative-layout-shift"),
            "tbt_ms": lab("total-blocking-time"),
            "fcp_ms": lab("first-contentful-paint"),
        },
        "field": {
            "lcp_ms": pct("LARGEST_CONTENTFUL_PAINT_MS"),
            "inp_ms": pct("INTERACTION_TO_NEXT_PAINT"),
            "cls": cls_field / 100 if cls_field is not None else None,
            "origin_fallback": bool(experience.get("origin_fallback")),
        },
    }


async def run_pagespeed(urls, *, api_key: str | None, strategy: str = "mobile",
                        concurrency: int = 2) -> dict[str, dict]:
    if not api_key:
        log.warning("Pas de clé PageSpeed (PSI_API_KEY) : quota très limité, erreurs 429 probables")
    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(timeout=120) as client:
        async def one(url):
            params = {"url": url, "strategy": strategy, "category": "performance"}
            if api_key:
                params["key"] = api_key
            async with sem:
                try:
                    r = await client.get(PSI_ENDPOINT, params=params)
                    r.raise_for_status()
                    return url, extract_metrics(r.json(), "pagespeed")
                except Exception as exc:
                    log.warning("PageSpeed %s : %s", url, exc)
                    return url, {"error": str(exc)[:200]}

        return dict(await asyncio.gather(*(one(u) for u in urls)))


async def run_lighthouse_local(urls, *, strategy: str = "mobile") -> dict[str, dict]:
    out = {}
    for url in urls:  # séquentiel : Lighthouse en parallèle fausse les mesures
        cmd = ["npx", "--yes", "lighthouse", url, "--output=json", "--output-path=stdout",
               "--only-categories=performance", "--quiet",
               "--chrome-flags=--headless=new --no-sandbox"]
        if strategy == "desktop":
            cmd.append("--preset=desktop")
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=180)
            if proc.returncode != 0:
                raise RuntimeError(stderr.decode(errors="replace")[-300:])
            out[url] = extract_metrics({"lighthouseResult": json.loads(stdout)}, "lighthouse")
        except Exception as exc:
            log.warning("Lighthouse %s : %s", url, exc)
            out[url] = {"error": str(exc)[:200]}
    return out
