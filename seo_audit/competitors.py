"""Profil comparable d'un site (le nôtre ou un concurrent) à partir des lignes de pages d'un audit."""
import json
from collections import Counter
from pathlib import Path
from statistics import mean


def summarize(rows: list[dict]) -> dict:
    """Profil agrégé à partir de lignes de pages (crawl d'un concurrent ou pages.csv de notre audit)."""
    n = len(rows)
    if not n:
        return {"pages": 0}
    types = Counter(t for r in rows for t in str(r.get("schema_types") or "").split(",") if t)
    num = lambda k: [r[k] for r in rows if isinstance(r.get(k), (int, float)) and r.get(k) is not None]
    avg = lambda k: round(mean(num(k)), 1) if num(k) else None
    return {
        "pages": n,
        "avg_title_length": avg("title_length"),
        "avg_description_length": avg("description_length"),
        "pct_with_description": round(100 * sum(1 for r in rows if (r.get("description_length") or 0) > 0) / n),
        "pct_single_h1": round(100 * sum(1 for r in rows if r.get("h1_count") == 1) / n),
        "avg_word_count": avg("word_count"),
        "pct_with_schema": round(100 * sum(1 for r in rows if r.get("schema_types")) / n),
        "pct_with_hreflang": round(100 * sum(1 for r in rows if (r.get("hreflang_count") or 0) > 0) / n),
        "avg_ttfb_ms": avg("ttfb_ms"),
        "schema_types": dict(types.most_common(8)),
    }


def own_profile(audit_json: Path) -> dict:
    data = json.loads(audit_json.read_text(encoding="utf-8"))
    rows = [r for r in data["pages"] if r.get("status") == 200 and r.get("title_length") != ""]
    return {"url": data["summary"]["site"], "sitemap_urls": data["summary"]["sitemap_urls"], **summarize(rows)}
