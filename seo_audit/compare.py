"""Comparaison entre deux audits (audit.json) : problèmes nouveaux, corrigés, évolution des scores."""
import json
from pathlib import Path


def find_previous(reports_dir: Path, site_prefix: str, exclude: Path) -> Path | None:
    """Dernier audit du même site (dossiers « <site>-<date> » triés par nom)."""
    candidates = sorted(d for d in reports_dir.glob(f"{site_prefix}-*")
                        if d.is_dir() and d != exclude and (d / "audit.json").exists())
    return candidates[-1] / "audit.json" if candidates else None


def compare_audits(previous_path: Path, issues, summary: dict) -> dict:
    return compare_data(json.loads(previous_path.read_text(encoding="utf-8")), issues, summary,
                        source=str(previous_path))


def compare_data(prev: dict, issues, summary: dict, source: str = "") -> dict:
    """`prev` : contenu d'un audit.json (clés summary et issues)."""
    key = lambda i: (i["url"], i["code"])
    old = {key(i): i for i in prev["issues"]}
    new = {key({"url": i.url, "code": i.code}): i for i in issues}
    added = [k for k in new if k not in old]
    fixed = [k for k in old if k not in new]
    ps = prev["summary"]
    cats = {c: summary["category_scores"][c] - ps.get("category_scores", {}).get(c, summary["category_scores"][c])
            for c in summary["category_scores"]}
    return {
        "previous_date": ps.get("date"),
        "previous_file": source,
        "score_delta": summary["health_score"] - ps.get("health_score", summary["health_score"]),
        "category_deltas": cats,
        "new_count": len(added),
        "fixed_count": len(fixed),
        "new": [{"url": u, "code": c, "severity": new[(u, c)].severity, "message": new[(u, c)].message}
                for u, c in added[:500]],
        "fixed": [{"url": u, "code": c, "severity": old[(u, c)]["severity"], "message": old[(u, c)]["message"]}
                  for u, c in fixed[:500]],
    }
