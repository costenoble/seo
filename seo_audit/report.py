"""Sorties : JSON complet, CSV (pages et problèmes) et rapport HTML autonome."""
import csv
import json
from collections import Counter
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from .models import CATEGORY_LABELS, SEVERITIES, SEVERITY_LABELS, Issue

PENALTY = {"critical": 40, "high": 20, "medium": 8, "low": 2, "info": 0}


def health_score(results, issues: list[Issue]) -> int:
    """100 = aucun problème. Pénalité moyenne par page, plafonnée à 100 par page."""
    pages = [u for u, p in results.items() if not p.blocked_by_robots]
    if not pages:
        return 0
    per_page = Counter()
    for i in issues:
        per_page[i.url] += PENALTY[i.severity]
    total = sum(min(100, per_page[u]) for u in pages)
    return round(100 - total / len(pages))


def category_scores(results, issues: list[Issue]) -> dict[str, int]:
    """Même calcul que health_score, restreint à une catégorie (100 = aucun problème)."""
    pages = [u for u, p in results.items() if not p.blocked_by_robots]
    scores = {}
    for cat in CATEGORY_LABELS:
        if not pages:
            scores[cat] = 0
            continue
        per_page = Counter()
        for i in issues:
            if i.category == cat:
                per_page[i.url] += PENALTY[i.severity]
        scores[cat] = round(100 - sum(min(100, per_page[u]) for u in pages) / len(pages))
    return scores


def _schema_types(d) -> set[str]:
    types = set()
    for obj in d.json_ld:
        for item in (obj.get("@graph") if isinstance(obj, dict) and isinstance(obj.get("@graph"), list) else [obj]):
            t = item.get("@type") if isinstance(item, dict) else None
            types.update([t] if isinstance(t, str) else [x for x in t or [] if isinstance(x, str)])
    return types


def page_rows(results, issues):
    counts = Counter(i.url for i in issues)
    rows = []
    for url, p in results.items():
        d = p.parsed
        rows.append({
            "url": url,
            "status": p.status if not p.blocked_by_robots else "robots.txt",
            "depth": p.depth,
            "indexable": p.indexable,
            "in_sitemap": p.in_sitemap,
            "title": d.title if d else "",
            "title_length": len(d.title) if d else "",
            "description_length": len(d.description) if d else "",
            "h1_count": len([h for h in d.h1 if h]) if d else "",
            "word_count": d.word_count if d else "",
            "canonical": p.canonical or "",
            "schema_types": ",".join(sorted(_schema_types(d))) if d else "",
            "hreflang_count": len(d.hreflangs) if d else "",
            "redirect_to": p.redirect_to or "",
            "ttfb_ms": p.ttfb_ms,
            "inlinks": None,  # rempli plus bas
            "perf_score": (p.performance or {}).get("score"),
            "gsc_coverage": (p.gsc or {}).get("coverageState"),
            "issues": counts.get(url, 0),
            "error": p.error or "",
        })
    return rows


def build_report(crawler, issues: list[Issue], meta: dict, previous: dict | None = None):
    """Résumé, problèmes triés et lignes de pages ; `previous` est le contenu d'un audit.json antérieur."""
    results = crawler.results
    issues = sorted(issues, key=lambda i: (SEVERITIES.index(i.severity), i.code, i.url))
    rows = page_rows(results, issues)
    for r in rows:
        r["inlinks"] = len(crawler.inlinks.get(r["url"], ()))

    sev_counts = Counter(i.severity for i in issues)
    summary = {
        "site": crawler.start_url,
        "date": datetime.now().isoformat(timespec="minutes"),
        "pages_crawled": len(results),
        "pages_indexable": sum(1 for p in results.values() if p.indexable),
        "sitemap_urls": len(crawler.sitemap_urls),
        "health_score": health_score(results, issues),
        "category_scores": category_scores(results, issues),
        "issues_by_severity": {s: sev_counts.get(s, 0) for s in SEVERITIES},
        **meta,
    }
    if previous:
        from .compare import compare_data
        summary["comparison"] = compare_data(previous, issues, summary)
    return summary, issues, rows


def write_reports(crawler, issues: list[Issue], out_dir: Path, meta: dict, previous: Path | None = None) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    prev_data = json.loads(previous.read_text(encoding="utf-8")) if previous else None
    summary, issues, rows = build_report(crawler, issues, meta, prev_data)
    paths = {}

    paths["json"] = out_dir / "audit.json"
    paths["json"].write_text(json.dumps({
        "summary": summary,
        "issues": [asdict(i) for i in issues],
        "pages": rows,
    }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    paths["issues_csv"] = out_dir / "problemes.csv"
    with paths["issues_csv"].open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["url", "categorie", "code", "severite", "message", "detail"])
        for i in issues:
            w.writerow([i.url, CATEGORY_LABELS.get(i.category, i.category), i.code,
                        SEVERITY_LABELS[i.severity], i.message, i.detail])

    paths["pages_csv"] = out_dir / "pages.csv"
    with paths["pages_csv"].open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["url"], delimiter=";")
        w.writeheader()
        w.writerows(rows)

    paths["html"] = out_dir / "rapport.html"
    payload = {
        "summary": summary,
        "issues": [asdict(i) for i in issues],
        "pages": rows,
        "labels": {"severity": SEVERITY_LABELS, "category": CATEGORY_LABELS},
    }
    data = json.dumps(payload, ensure_ascii=False, default=str).replace("</", "<\\/")
    template = (Path(__file__).parent / "report_template.html").read_text(encoding="utf-8")
    paths["html"].write_text(template.replace("__DATA__", data), encoding="utf-8")
    return paths
