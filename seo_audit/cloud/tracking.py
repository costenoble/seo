"""Suivi de positions : relevés quotidiens en mode différé chez DataForSEO, partagés entre utilisateurs.

À chaque passage du planificateur : 1) on récupère les relevés prêts et on les applique à tous les
mots-clés suivis concernés ; 2) on lance les relevés du jour qui manquent (un seul par mot-clé, pays et
appareil, même si plusieurs utilisateurs le suivent)."""
import logging
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone

from .dataforseo import DataForSEOError, domain_key
from .insights import EXPECTED_CTR

log = logging.getLogger(__name__)
STALE_HOURS = 6  # une tâche sans résultat après ce délai est abandonnée puis relancée


def today() -> date:
    return datetime.now(timezone.utc).date()


def serp_key(keyword: str, location: int, device: str, day) -> str:
    return f"serp|{location}|{device}|{day.isoformat() if hasattr(day, 'isoformat') else day}|{keyword.lower()}"


def apply_serp(db, keyword: str, location: int, device: str, day, serp: dict) -> int:
    rows = db.rank_due_rows(keyword, location, device, day)
    for r in rows:
        entry = serp["ranks"].get(domain_key(r["site"]))
        db.save_rank_check(r["id"], day, entry[0] if entry else None, entry[1] if entry else None, serp["ranks"])
    return len(rows)


def run(db, client, budget: float) -> dict:
    deadline = time.monotonic() + budget
    day, stats = today(), {"collected": 0, "from_cache": 0, "posted": 0}
    try:
        db.purge_stale_serp_tasks(STALE_HOURS)
        pending = db.serp_tasks_pending()
        if pending:
            for task_id in client.serp_ready():
                if time.monotonic() > deadline:
                    return stats
                row = pending.get(task_id)
                if not row:
                    continue
                serp = client.serp_result(task_id)
                if serp is None:
                    continue
                db.cache_put(serp_key(row["keyword"], row["location_code"], row["device"], row["date"]), serp)
                db.delete_serp_task(task_id)
                apply_serp(db, row["keyword"], row["location_code"], row["device"], row["date"], serp)
                stats["collected"] += 1
        to_post = []
        for g in db.rank_due_groups(day, limit=100):
            cached = db.cache_get(serp_key(g["keyword"], g["location_code"], g["device"], day), max_age_days=2)
            if cached:
                apply_serp(db, g["keyword"], g["location_code"], g["device"], day, cached)
                stats["from_cache"] += 1
            else:
                to_post.append(g)
        if to_post and time.monotonic() < deadline:
            for g, task_id in zip(to_post, client.post_serp_tasks(to_post)):
                if task_id:
                    db.add_serp_task(task_id, g["keyword"], g["location_code"], g["device"], day)
                    stats["posted"] += 1
    except DataForSEOError as exc:
        log.warning("Suivi de positions : %s", exc)
        stats["error"] = str(exc)
    return stats


def visibility(positions: list) -> float | None:
    """Part des clics potentiels captés (0 à 100), comme l'indice de visibilité de Semrush."""
    if not positions:
        return None
    return round(sum(EXPECTED_CTR.get(p, .01 if p and p <= 20 else 0) for p in positions if p)
                 / (len(positions) * EXPECTED_CTR[1]) * 100, 1)


def overview(site: str, keywords: list[dict], history: list[dict], latest: dict, week_ago: dict,
             competitors: list[str]) -> dict:
    """keywords : lignes rank_keywords ; history : [{keyword_id, date, position}] sur 30 jours ;
    latest / week_ago : {keyword_id: ranks} ; competitors : domaines suivis."""
    by_kw = {}
    for h in history:
        by_kw.setdefault(str(h["keyword_id"]), []).append(h)
    me = domain_key(site)
    rows, now_pos, week_pos = [], [], []
    for k in keywords:
        kid = str(k["id"])
        hist = sorted(by_kw.get(kid, []), key=lambda h: h["date"])
        last = hist[-1] if hist else None

        def at(days):
            if not last:
                return None
            target = last["date"] - timedelta(days=days)
            prev = [h for h in hist if h["date"] <= target]
            return prev[-1]["position"] if prev else None
        ranks = latest.get(kid) or {}
        rows.append({
            "id": kid, "keyword": k["keyword"], "location": k["location_code"], "device": k["device"],
            "volume": k["volume"], "pending": last is None, "date": last["date"].isoformat() if last else None,
            "position": last["position"] if last else None, "url": (ranks.get(me) or [None, None])[1],
            "prev_day": at(1), "prev_week": at(7),
            "history": [[h["date"].isoformat(), h["position"]] for h in hist],
            "competitors": {c: (ranks.get(c) or [None])[0] for c in competitors}})
        if last:
            now_pos.append(last["position"])
            if any(h["date"] <= last["date"] - timedelta(days=7) for h in hist):  # au moins 7 jours d'historique
                week_pos.append(at(7))
    checked = [r for r in rows if not r["pending"]]

    def summary(domain):
        cur = [(latest.get(r["id"]) or {}).get(domain, [None])[0] for r in checked]
        old = [(week_ago.get(r["id"]) or {}).get(domain, [None])[0] for r in checked if week_ago.get(r["id"])]
        ranked = [p for p in cur if p]
        return {"domain": domain, "visibility": visibility(cur), "visibility_week": visibility(old) if old else None,
                "avg_position": round(sum(ranked) / len(ranked), 1) if ranked else None,
                "top10": sum(1 for p in ranked if p <= 10)}
    seen = Counter()
    for r in checked:
        for d, (pos, _url) in (latest.get(r["id"]) or {}).items():
            if pos and pos <= 10 and d != me and d not in competitors:
                seen[d] += 1
    ranked = [p for p in now_pos if p]
    return {
        "site": site, "keywords": rows,
        "kpis": {"visibility": visibility(now_pos), "visibility_week": visibility(week_pos) if week_pos else None,
                 "avg_position": round(sum(ranked) / len(ranked), 1) if ranked else None,
                 "top3": sum(1 for p in ranked if p <= 3), "top10": sum(1 for p in ranked if p <= 10),
                 "top30": sum(1 for p in ranked if p <= 30), "out": len(checked) - len(ranked),
                 "pending": len(rows) - len(checked)},
        "competitors": [summary(me) | {"you": True}] + [summary(c) for c in competitors],
        "suggested": [d for d, n in seen.most_common(6) if n >= (2 if len(checked) > 2 else 1)],
    }
