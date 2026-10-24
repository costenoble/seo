"""Alertes et résumé hebdomadaire : génération à partir des données, puis envoi groupé (planificateur)."""
import logging
import time
from datetime import datetime, timedelta, timezone

from . import emails

log = logging.getLogger(__name__)
ALERT_INTERVAL = timedelta(hours=1)   # au plus un e-mail d'alertes par heure et par utilisateur
DIGEST_HOUR_UTC = 6                   # lundi vers 7 h / 8 h à Paris


def week_start(now: datetime) -> datetime:
    monday = (now - timedelta(days=now.weekday())).replace(hour=DIGEST_HOUR_UTC, minute=0, second=0, microsecond=0)
    return monday if now >= monday else monday - timedelta(days=7)


# -- génération des alertes -----------------------------------------------------------------------
def generate_alerts(db) -> int:
    created = 0
    for a in db.audits_to_notify():
        c = (a["summary"] or {}).get("comparison") or {}
        link = f"/#/audit/{a['id']}"
        if (c.get("score_delta") or 0) <= -5:
            created += db.add_alert(a["user_id"], f"score:{a['id']}", "score_drop", "bad", a["site"],
                                    f"Score en baisse de {-c['score_delta']} points",
                                    f"Le dernier audit donne {a['summary']['health_score']}/100.", link)
        severe = [i for i in c.get("new", []) if i.get("severity") in ("critical", "high")]
        if severe:
            msgs = sorted({i["message"].split(" (")[0] for i in severe})
            created += db.add_alert(a["user_id"], f"new:{a['id']}", "new_critical", "bad", a["site"],
                                    f"{len(severe)} nouveau(x) problème(s) grave(s)", " · ".join(msgs[:3]), link)
        db.mark_audit_notified(a["id"])
    for m in db.rank_moves():
        prev, now = m["prev"], m["position"]
        link = f"/#/suivi/{m['site']}"
        if prev and prev <= 10 and (now is None or now > 10):
            args = ("rank_drop", "bad", f"« {m['keyword']} » sort du top 10", f"Position {prev} → {now or '> 30'}.")
        elif prev and now and now - prev >= 5:
            args = ("rank_drop", "bad", f"« {m['keyword']} » perd {now - prev} places", f"Position {prev} → {now}.")
        elif now and now <= 3 and (prev is None or prev > 3):
            args = ("rank_gain", "good", f"« {m['keyword']} » entre dans le top 3", f"Position {prev or '> 30'} → {now}.")
        elif now and now <= 10 and (prev is None or prev > 10):
            args = ("rank_gain", "good", f"« {m['keyword']} » entre dans le top 10", f"Position {prev or '> 30'} → {now}.")
        else:
            continue
        created += db.add_alert(m["user_id"], f"rank:{m['keyword_id']}:{m['date']}", args[0], args[1], m["site"], args[2], args[3], link)
    return created


# -- contenu du résumé hebdomadaire ---------------------------------------------------------------
def build_digest(db, user_id: str) -> dict | None:
    from .tracking import overview
    open_tasks = db.open_task_counts(user_id)
    projects = [{"site": s["key"], "score": s["latest"]["score"],
                 "delta": (s["latest"].get("comparison") or {}).get("score_delta"), "open": open_tasks.get(s["key"], 0)}
                for s in db.list_sites(user_id) if s["latest"]]
    gsc = []
    for p in db.properties(user_id):
        w = db.gsc_week(user_id, p["site"])
        if w:
            gsc.append({"site": p["site"], **w})
    tracking_ = []
    for site in db.rank_sites(user_id):
        kws = db.rank_keywords(user_id, site)
        ov = overview(site, kws, db.rank_history(user_id, site, 30), db.rank_latest(user_id, site),
                      db.rank_latest(user_id, site, 7), [])
        moves = [{"keyword": r["keyword"], "now": r["position"],
                  "change": (r["prev_week"] - r["position"]) if r["prev_week"] and r["position"] else None}
                 for r in ov["keywords"] if not r["pending"]]
        moves = sorted([m for m in moves if m["change"]], key=lambda m: -abs(m["change"]))[:4]
        k = ov["kpis"]
        if k["visibility"] is not None:
            tracking_.append({"site": site, "visibility": k["visibility"], "movers": moves,
                              "visibility_change": round(k["visibility"] - k["visibility_week"], 1) if k["visibility_week"] is not None else None})
    if not (projects or gsc or tracking_):
        return None
    return {"projects": projects, "gsc": gsc, "tracking": tracking_, "tasks": db.top_tasks(user_id, 3)}


def _name(row) -> str:
    if row.get("name"):
        return row["name"].split()[0]
    return (row.get("email") or "").split("@")[0] or "à vous"


# -- envoi (appelé par le planificateur) ------------------------------------------------------------
def run(db, settings, budget: float, now: datetime | None = None) -> dict:
    deadline = time.monotonic() + budget
    now = now or datetime.now(timezone.utc)
    stats = {"alerts_created": 0, "alert_emails": 0, "digests": 0, "errors": 0}
    try:
        stats["alerts_created"] = generate_alerts(db)
    except Exception:  # la génération ne doit jamais bloquer l'envoi
        log.exception("Génération des alertes")
    if not emails.enabled(settings):
        return stats
    for u in db.users_with_pending_alerts(now - ALERT_INTERVAL):
        if time.monotonic() > deadline:
            return stats
        items = db.pending_alerts(u["user_id"])
        if u["alerts_enabled"] and u["email"] and items:
            unsub = emails.unsubscribe_url(settings.app_url, settings.token_key, u["user_id"], "alerts")
            subject, html, text = emails.alerts(settings.app_url, _name(u), items, unsub)
            try:
                emails.send(settings, u["email"], subject, html, text, unsub)
                stats["alert_emails"] += 1
            except emails.EmailError as exc:
                log.warning("Alertes pour %s : %s", u["user_id"], exc)
                stats["errors"] += 1
                continue
        db.mark_alerts_sent(u["user_id"], [a["id"] for a in items], now)
    for u in db.users_due_digest(week_start(now), limit=10):
        if time.monotonic() > deadline:
            return stats
        data = build_digest(db, u["user_id"])
        if data and u["email"]:
            unsub = emails.unsubscribe_url(settings.app_url, settings.token_key, u["user_id"], "digest")
            subject, html, text = emails.digest(settings.app_url, _name(u), data, unsub)
            try:
                emails.send(settings, u["email"], subject, html, text, unsub)
                stats["digests"] += 1
            except emails.EmailError as exc:
                log.warning("Résumé pour %s : %s", u["user_id"], exc)
                stats["errors"] += 1
                continue
        db.mark_digest_sent(u["user_id"], now)
    return stats
