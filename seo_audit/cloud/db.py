"""Accès aux données de l'application. Le serveur contourne la RLS : chaque requête filtre
explicitement sur user_id, et les tests vérifient qu'un utilisateur ne voit jamais les données d'un autre."""
from datetime import timedelta

import psycopg
from psycopg.rows import dict_row

from ..competitors import summarize
from ..engine.pg_store import PgStore


def _brief(a: dict) -> dict:
    s = a.get("summary") or {}
    return {"id": str(a["id"]), "status": a["status"], "date": (a["finished_at"] or a["created_at"]).isoformat(),
            "url": a["url"], "score": s.get("health_score"), "category_scores": s.get("category_scores", {}),
            "pages": s.get("pages_crawled", a.get("pages_done", 0)), "indexable": s.get("pages_indexable"),
            "issues_by_severity": s.get("issues_by_severity", {}), "error": a.get("error"),
            "progress": {"done": a.get("pages_done", 0), "total": a.get("pages_total", 0), "phase": a["phase"]},
            "comparison": {k: v for k, v in (s.get("comparison") or {}).items()
                           if k in ("score_delta", "new_count", "fixed_count")} or None}


class Db:
    def __init__(self, dsn: str):
        self.dsn = dsn
        self.store = PgStore(dsn)

    def conn(self):
        return psycopg.connect(self.dsn, row_factory=dict_row, prepare_threshold=None)

    # -- profil, plan et consommation ------------------------------------------
    def profile(self, user: dict) -> dict:
        with self.conn() as c:
            c.execute("INSERT INTO profiles (user_id, email) VALUES (%s,%s) ON CONFLICT (user_id) DO NOTHING",
                      (user["id"], user.get("email")))
            return c.execute("SELECT plan, email, plan_renews_at, created_at FROM profiles WHERE user_id=%s",
                             (user["id"],)).fetchone()

    def usage_month(self, user_id: str) -> dict:
        with self.conn() as c:
            rows = c.execute("""SELECT kind, count(*) n FROM usage_events
                                WHERE user_id=%s AND created_at >= date_trunc('month', now()) GROUP BY kind""",
                             (user_id,)).fetchall()
        return {r["kind"]: r["n"] for r in rows}

    def add_usage(self, user_id: str, kind: str) -> None:
        with self.conn() as c:
            c.execute("INSERT INTO usage_events (user_id, kind) VALUES (%s,%s)", (user_id, kind))

    def running_count(self, user_id: str) -> int:
        with self.conn() as c:
            return c.execute("SELECT count(*) n FROM audits WHERE user_id=%s AND status IN ('queued','running')",
                             (user_id,)).fetchone()["n"]

    # -- audits ----------------------------------------------------------------------
    def list_sites(self, user_id: str) -> list[dict]:
        with self.conn() as c:
            rows = c.execute("""SELECT id, site, url, status, phase, summary, error, created_at, finished_at,
                                       pages_done, pages_total
                                FROM audits WHERE user_id=%s AND kind='site' ORDER BY created_at""",
                             (user_id,)).fetchall()
        groups: dict[str, list] = {}
        for r in rows:
            groups.setdefault(r["site"], []).append(r)
        out = []
        for site, audits in groups.items():
            done = [a for a in audits if a["status"] == "done"]
            latest = _brief(done[-1]) if done else None
            out.append({"key": site, "latest": latest, "audits": len(done),
                        "running": any(a["status"] in ("queued", "running") for a in audits),
                        "history": [{"id": str(a["id"]), "date": a["finished_at"].isoformat(),
                                     "score": a["summary"]["health_score"]} for a in done]})
        out.sort(key=lambda s: s["latest"]["date"] if s["latest"] else "9999", reverse=True)
        return out

    def site_audits(self, user_id: str, site: str) -> list[dict]:
        with self.conn() as c:
            rows = c.execute("""SELECT id, url, status, phase, summary, error, created_at, finished_at, pages_done, pages_total
                                FROM audits WHERE user_id=%s AND site=%s AND kind='site'
                                ORDER BY created_at DESC""", (user_id, site)).fetchall()
        return [_brief(r) for r in rows]

    def audit(self, user_id: str, audit_id: str) -> dict | None:
        try:
            with self.conn() as c:
                row = c.execute("""SELECT id, url, site, kind, parent_site, status, phase, summary, error, created_at,
                                          finished_at, pages_done, pages_total
                                   FROM audits WHERE id=%s AND user_id=%s""", (audit_id, user_id)).fetchone()
        except psycopg.errors.InvalidTextRepresentation:  # identifiant qui n'est pas un UUID
            return None
        return row

    def report(self, user_id: str, audit_id: str) -> dict | None:
        try:
            with self.conn() as c:
                row = c.execute("""SELECT a.summary, r.issues, r.pages FROM audits a
                                   JOIN audit_reports r ON r.audit_id = a.id
                                   WHERE a.id=%s AND a.user_id=%s""", (audit_id, user_id)).fetchone()
        except psycopg.errors.InvalidTextRepresentation:
            return None
        return dict(row) if row else None

    def delete_audit(self, user_id: str, audit_id: str) -> bool:
        with self.conn() as c:
            return c.execute("DELETE FROM audits WHERE id=%s AND user_id=%s", (audit_id, user_id)).rowcount == 1

    def competitors(self, user_id: str, site: str) -> dict:
        with self.conn() as c:
            own = c.execute("""SELECT a.summary, r.pages FROM audits a JOIN audit_reports r ON r.audit_id=a.id
                               WHERE a.user_id=%s AND a.site=%s AND a.kind='site' AND a.status='done'
                               ORDER BY a.finished_at DESC LIMIT 1""", (user_id, site)).fetchone()
            rows = c.execute("""SELECT DISTINCT ON (a.site) a.id, a.site, a.url, a.status, a.error, a.finished_at,
                                       a.created_at, a.summary, a.pages_done, a.pages_total, r.pages
                                FROM audits a LEFT JOIN audit_reports r ON r.audit_id=a.id
                                WHERE a.user_id=%s AND a.parent_site=%s AND a.kind='competitor'
                                ORDER BY a.site, a.created_at DESC""", (user_id, site)).fetchall()

        def profile(summary, pages):
            ok = [p for p in pages if p.get("status") == 200 and p.get("title_length") != ""]
            return {"url": summary["site"], "sitemap_urls": summary["sitemap_urls"], **summarize(ok)}

        return {"own": profile(own["summary"], own["pages"]) if own else None,
                "competitors": [{"id": str(r["id"]), "site": r["site"], "status": r["status"], "error": r["error"],
                                 "date": (r["finished_at"] or r["created_at"]).isoformat(),
                                 "progress": {"done": r["pages_done"], "total": r["pages_total"]},
                                 "profile": profile(r["summary"], r["pages"]) if r["pages"] is not None else None}
                                for r in rows]}

    # -- Google ------------------------------------------------------------------------
    def save_google_token(self, user_id: str, enc: str, email: str | None) -> None:
        with self.conn() as c:
            c.execute("""INSERT INTO google_tokens (user_id, refresh_token_enc, email) VALUES (%s,%s,%s)
                         ON CONFLICT (user_id) DO UPDATE SET refresh_token_enc=EXCLUDED.refresh_token_enc,
                         email=EXCLUDED.email, updated_at=now()""", (user_id, enc, email))

    def google_token(self, user_id: str) -> dict | None:
        with self.conn() as c:
            return c.execute("SELECT refresh_token_enc, email FROM google_tokens WHERE user_id=%s",
                             (user_id,)).fetchone()

    def delete_google_token(self, user_id: str) -> None:
        with self.conn() as c:
            c.execute("DELETE FROM google_tokens WHERE user_id=%s", (user_id,))

    # -- positions -------------------------------------------------------------------------
    def properties(self, user_id: str) -> list[dict]:
        with self.conn() as c:
            return [{"site": r["site"], "last_synced": r["last_synced"].isoformat() if r["last_synced"] else None}
                    for r in c.execute("SELECT site, last_synced FROM gsc_properties WHERE user_id=%s ORDER BY site",
                                       (user_id,))]

    def add_property(self, user_id: str, site: str) -> None:
        with self.conn() as c:
            c.execute("INSERT INTO gsc_properties (user_id, site) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                      (user_id, site))

    def mark_synced(self, user_id: str, site: str) -> None:
        with self.conn() as c:
            c.execute("UPDATE gsc_properties SET last_synced=current_date WHERE user_id=%s AND site=%s",
                      (user_id, site))

    def due_properties(self, limit: int = 20) -> list[dict]:
        """Propriétés pas encore synchronisées aujourd'hui, dont l'utilisateur a un jeton Google."""
        with self.conn() as c:
            return c.execute("""SELECT p.user_id::text, p.site, t.refresh_token_enc FROM gsc_properties p
                                JOIN google_tokens t ON t.user_id = p.user_id
                                WHERE p.last_synced IS NULL OR p.last_synced < current_date
                                ORDER BY p.last_synced NULLS FIRST LIMIT %s""", (limit,)).fetchall()

    def save_gsc_rows(self, user_id: str, site: str, rows: list[dict]) -> int:
        with self.conn() as c:
            c.cursor().executemany(
                """INSERT INTO gsc_daily (user_id, site, date, query, clicks, impressions, ctr, position)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (user_id, site, date, query) DO UPDATE SET clicks=EXCLUDED.clicks,
                   impressions=EXCLUDED.impressions, ctr=EXCLUDED.ctr, position=EXCLUDED.position""",
                [(user_id, site, r["date"], r["query"], r["clicks"], r["impressions"], r["ctr"], r["position"])
                 for r in rows])
        return len(rows)

    def positions_summary(self, user_id: str, site: str, days: int = 28) -> dict:
        """Requêtes de la période courante comparées à la période précédente de même durée."""
        with self.conn() as c:
            last = c.execute("SELECT max(date) d FROM gsc_daily WHERE user_id=%s AND site=%s",
                             (user_id, site)).fetchone()["d"]
            if not last:
                return {"site": site, "last_date": None, "days": days, "queries": [], "daily": []}
            cur_start, prev_end = last - timedelta(days=days - 1), last - timedelta(days=days)
            prev_start = last - timedelta(days=2 * days - 1)
            sql = """SELECT query, sum(clicks) clicks, sum(impressions) impressions,
                            sum(position * impressions) / nullif(sum(impressions), 0) position
                     FROM gsc_daily WHERE user_id=%s AND site=%s AND date BETWEEN %s AND %s GROUP BY query"""
            cur = c.execute(sql, (user_id, site, cur_start, last)).fetchall()
            prev = {r["query"]: r for r in c.execute(sql, (user_id, site, prev_start, prev_end))}
            daily = c.execute("""SELECT date, sum(clicks) clicks, sum(impressions) impressions,
                                        sum(position * impressions) / nullif(sum(impressions), 0) position
                                 FROM gsc_daily WHERE user_id=%s AND site=%s AND date BETWEEN %s AND %s
                                 GROUP BY date ORDER BY date""", (user_id, site, cur_start, last)).fetchall()
        queries = []
        for r in cur:
            p = prev.get(r["query"])
            queries.append({
                "query": r["query"], "clicks": r["clicks"], "impressions": r["impressions"],
                "ctr": r["clicks"] / r["impressions"] if r["impressions"] else 0,
                "position": round(r["position"], 1) if r["position"] is not None else None,
                "previous_position": round(p["position"], 1) if p and p["position"] is not None else None})
        queries.sort(key=lambda x: -x["impressions"])
        return {"site": site, "last_date": last.isoformat(), "days": days, "queries": queries[:1000],
                "daily": [{**d, "date": d["date"].isoformat()} for d in daily]}

    def query_history(self, user_id: str, site: str, query: str) -> list[dict]:
        with self.conn() as c:
            return [{**r, "date": r["date"].isoformat()} for r in c.execute(
                """SELECT date, clicks, impressions, position FROM gsc_daily
                   WHERE user_id=%s AND site=%s AND query=%s ORDER BY date""", (user_id, site, query))]

    def replace_gsc_pages(self, user_id: str, site: str, query_pages: list[dict], pages: list[dict]) -> None:
        with self.conn() as c:
            c.execute("DELETE FROM gsc_query_pages WHERE user_id=%s AND site=%s", (user_id, site))
            c.execute("DELETE FROM gsc_pages WHERE user_id=%s AND site=%s", (user_id, site))
            cur = c.cursor()
            cur.executemany("""INSERT INTO gsc_query_pages (user_id, site, query, page, clicks, impressions, position)
                               VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                            [(user_id, site, r["query"], r["page"], r["clicks"], r["impressions"], r["position"])
                             for r in query_pages])
            cur.executemany("""INSERT INTO gsc_pages (user_id, site, page, clicks, impressions, position)
                               VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                            [(user_id, site, r["page"], r["clicks"], r["impressions"], r["position"]) for r in pages])
            c.execute("UPDATE gsc_properties SET pages_synced_at=now() WHERE user_id=%s AND site=%s", (user_id, site))

    def gsc_pages_snapshot(self, user_id: str, site: str) -> tuple[list[dict], list[dict]]:
        with self.conn() as c:
            qp = c.execute("""SELECT query, page, clicks, impressions, position FROM gsc_query_pages
                              WHERE user_id=%s AND site=%s""", (user_id, site)).fetchall()
            pages = c.execute("""SELECT page, clicks, impressions, position FROM gsc_pages
                                 WHERE user_id=%s AND site=%s""", (user_id, site)).fetchall()
        return qp, pages

    def latest_report_for_site(self, user_id: str, site_key: str) -> dict | None:
        with self.conn() as c:
            row = c.execute("""SELECT r.issues, r.pages FROM audits a JOIN audit_reports r ON r.audit_id = a.id
                               WHERE a.user_id=%s AND a.site=%s AND a.kind='site' AND a.status='done'
                               ORDER BY a.finished_at DESC LIMIT 1""", (user_id, site_key)).fetchone()
        return dict(row) if row else None

    # -- mots-clés -------------------------------------------------------------------------
    def save_ideas(self, user_id: str, seed: str, ideas: list[tuple[str, str]]) -> None:
        with self.conn() as c:
            c.cursor().executemany(
                """INSERT INTO keyword_ideas (user_id, seed, idea, kind) VALUES (%s,%s,%s,%s)
                   ON CONFLICT (user_id, seed, idea) DO UPDATE SET kind=EXCLUDED.kind, created_at=now()""",
                [(user_id, seed, idea, kind) for idea, kind in ideas])

    def ideas(self, user_id: str, seed: str) -> list[dict]:
        with self.conn() as c:
            return [{"idea": r["idea"], "kind": r["kind"]} for r in c.execute(
                "SELECT idea, kind FROM keyword_ideas WHERE user_id=%s AND seed=%s ORDER BY kind, idea",
                (user_id, seed))]

