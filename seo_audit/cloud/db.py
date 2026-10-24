"""Accès aux données de l'application. Le serveur contourne la RLS : chaque requête filtre
explicitement sur user_id, et les tests vérifient qu'un utilisateur ne voit jamais les données d'un autre."""
from datetime import timedelta

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

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
            c.execute("INSERT INTO profiles (user_id, email, name) VALUES (%s,%s,%s) ON CONFLICT (user_id) DO NOTHING",
                      (user["id"], user.get("email"), user.get("name")))
            if user.get("name"):
                c.execute("UPDATE profiles SET name=%s WHERE user_id=%s AND name IS DISTINCT FROM %s",
                          (user["name"], user["id"], user["name"]))
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

    def delete_project(self, user_id: str, site: str) -> int:
        """Supprime un projet : ses audits, ceux de ses concurrents et son plan d'action."""
        with self.conn() as c:
            n = c.execute("""DELETE FROM audits WHERE user_id=%s AND ((kind='site' AND site=%s) OR parent_site=%s)""",
                          (user_id, site, site)).rowcount
            c.execute("DELETE FROM tasks WHERE user_id=%s AND site=%s", (user_id, site))
            return n

    def delete_rank_site(self, user_id: str, site: str) -> int:
        with self.conn() as c:
            n = c.execute("DELETE FROM rank_keywords WHERE user_id=%s AND site=%s", (user_id, site)).rowcount
            c.execute("DELETE FROM rank_competitors WHERE user_id=%s AND site=%s", (user_id, site))
            return n

    def delete_property(self, user_id: str, site: str) -> int:
        with self.conn() as c:
            n = c.execute("DELETE FROM gsc_properties WHERE user_id=%s AND site=%s", (user_id, site)).rowcount
            for table in ("gsc_daily", "gsc_query_pages", "gsc_pages"):
                c.execute(f"DELETE FROM {table} WHERE user_id=%s AND site=%s", (user_id, site))
            return n

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
            row = c.execute("""SELECT r.issues, r.pages, a.finished_at FROM audits a JOIN audit_reports r ON r.audit_id = a.id
                               WHERE a.user_id=%s AND a.site=%s AND a.kind='site' AND a.status='done'
                               ORDER BY a.finished_at DESC LIMIT 1""", (user_id, site_key)).fetchone()
        return dict(row) if row else None

    # -- plan d'action ---------------------------------------------------------------------
    TASK_COLS = "id, site, fingerprint, source, priority, title, detail, action, pages, queries, baseline, " \
                "status, resolved_by, created_at, done_at"

    def upsert_tasks(self, user_id: str, site: str, tasks: list[dict]) -> None:
        """Ajoute les nouvelles recommandations ; ne touche pas aux tâches existantes (statut conservé)."""
        if not tasks:
            return
        with self.conn() as c:
            c.cursor().executemany(
                """INSERT INTO tasks (user_id, site, fingerprint, source, priority, title, detail, action, pages,
                                      queries, baseline)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (user_id, site, fingerprint) DO NOTHING""",
                [(user_id, site, t["fingerprint"], t["source"], t["priority"], t["title"], t["detail"], t["action"],
                  Jsonb(t["pages"]), Jsonb(t["queries"]), Jsonb(t["baseline"])) for t in tasks])

    def sync_audit_tasks(self, user_id: str, site: str, current: dict, audit_finished_at) -> None:
        """Ferme les problèmes d'audit disparus au dernier audit, rouvre ceux qui sont revenus.
        current : {fingerprint: nombre de pages concernées} pour l'audit terminé à audit_finished_at."""
        with self.conn() as c:
            c.execute("""UPDATE tasks SET status='done', resolved_by='audit', done_at=%s, updated_at=now()
                         WHERE user_id=%s AND site=%s AND source='audit' AND status='todo' AND created_at < %s
                           AND NOT (fingerprint = ANY(%s))""",
                      (audit_finished_at, user_id, site, audit_finished_at, list(current)))
            for fp, count in current.items():
                c.execute("""UPDATE tasks SET status='todo', resolved_by=NULL, done_at=NULL, updated_at=now(),
                                    baseline = jsonb_set(baseline, '{count}', to_jsonb(%s::int))
                             WHERE user_id=%s AND site=%s AND fingerprint=%s AND status='done' AND done_at < %s""",
                          (count, user_id, site, fp, audit_finished_at))

    def list_tasks(self, user_id: str, site: str) -> list[dict]:
        with self.conn() as c:
            return c.execute(f"""SELECT {self.TASK_COLS} FROM tasks WHERE user_id=%s AND site=%s
                                 ORDER BY CASE status WHEN 'todo' THEN 0 WHEN 'done' THEN 1 ELSE 2 END,
                                          CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, created_at""",
                             (user_id, site)).fetchall()

    def set_task_status(self, user_id: str, task_id: str, status: str) -> bool:
        try:
            with self.conn() as c:
                return c.execute("""UPDATE tasks SET status=%s, updated_at=now(),
                                           done_at = CASE WHEN %s = 'done' THEN now() END,
                                           resolved_by = CASE WHEN %s = 'done' THEN 'user' END
                                    WHERE id=%s AND user_id=%s""",
                                 (status, status, status, task_id, user_id)).rowcount == 1
        except psycopg.errors.InvalidTextRepresentation:
            return False

    def open_task_counts(self, user_id: str) -> dict:
        with self.conn() as c:
            return {r["site"]: r["n"] for r in c.execute(
                "SELECT site, count(*) n FROM tasks WHERE user_id=%s AND status='todo' GROUP BY site", (user_id,))}

    def gsc_last_date(self, user_id: str, site: str):
        with self.conn() as c:
            return c.execute("SELECT max(date) d FROM gsc_daily WHERE user_id=%s AND site=%s", (user_id, site)).fetchone()["d"]

    def gsc_window(self, user_id: str, site: str, queries: list[str], start, end) -> dict:
        """Totaux sur une période pour un ensemble de requêtes ; days = jours de données disponibles."""
        with self.conn() as c:
            r = c.execute("""SELECT count(DISTINCT date) days, coalesce(sum(clicks), 0) clicks,
                                    coalesce(sum(impressions), 0) impressions,
                                    sum(position * impressions) / nullif(sum(impressions), 0) position
                             FROM gsc_daily WHERE user_id=%s AND site=%s AND query = ANY(%s) AND date BETWEEN %s AND %s""",
                          (user_id, site, queries, start, end)).fetchone()
            days = c.execute("SELECT count(DISTINCT date) n FROM gsc_daily WHERE user_id=%s AND site=%s AND date BETWEEN %s AND %s",
                             (user_id, site, start, end)).fetchone()["n"]
        return {"days": days, "clicks": r["clicks"], "impressions": r["impressions"],
                "position": round(r["position"], 1) if r["position"] is not None else None}

    # -- suivi de positions -------------------------------------------------------------------------
    def rank_keywords(self, user_id: str, site: str) -> list[dict]:
        with self.conn() as c:
            return c.execute("""SELECT id, keyword, location_code, device, volume, last_checked, created_at
                                FROM rank_keywords WHERE user_id=%s AND site=%s ORDER BY volume DESC NULLS LAST, keyword""",
                             (user_id, site)).fetchall()

    def rank_sites(self, user_id: str) -> dict:
        with self.conn() as c:
            return {r["site"]: r["n"] for r in c.execute(
                "SELECT site, count(*) n FROM rank_keywords WHERE user_id=%s GROUP BY site", (user_id,))}

    def rank_count(self, user_id: str) -> int:
        with self.conn() as c:
            return c.execute("SELECT count(*) n FROM rank_keywords WHERE user_id=%s", (user_id,)).fetchone()["n"]

    def add_rank_keywords(self, user_id: str, site: str, keywords: list[str], location: int, device: str,
                          volumes: dict) -> int:
        with self.conn() as c:
            cur = c.cursor()
            cur.executemany("""INSERT INTO rank_keywords (user_id, site, keyword, location_code, device, volume)
                               VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                            [(user_id, site, k, location, device, volumes.get(k)) for k in keywords])
            return cur.rowcount

    def delete_rank_keyword(self, user_id: str, keyword_id: str) -> bool:
        try:
            with self.conn() as c:
                return c.execute("DELETE FROM rank_keywords WHERE id=%s AND user_id=%s", (keyword_id, user_id)).rowcount == 1
        except psycopg.errors.InvalidTextRepresentation:
            return False

    def rank_keyword(self, user_id: str, keyword_id: str) -> dict | None:
        try:
            with self.conn() as c:
                return c.execute("SELECT * FROM rank_keywords WHERE id=%s AND user_id=%s", (keyword_id, user_id)).fetchone()
        except psycopg.errors.InvalidTextRepresentation:
            return None

    def rank_history(self, user_id: str, site: str, days: int = 30) -> list[dict]:
        with self.conn() as c:
            return c.execute("""SELECT c.keyword_id, c.date, c.position FROM rank_checks c
                                JOIN rank_keywords k ON k.id = c.keyword_id
                                WHERE k.user_id=%s AND k.site=%s AND c.date > current_date - %s""",
                             (user_id, site, days)).fetchall()

    def rank_latest(self, user_id: str, site: str, before_days: int = 0) -> dict:
        """{keyword_id: ranks} du relevé le plus récent (au moins `before_days` jours avant le dernier relevé)."""
        with self.conn() as c:
            rows = c.execute("""SELECT DISTINCT ON (c.keyword_id) c.keyword_id, c.ranks FROM rank_checks c
                                JOIN rank_keywords k ON k.id = c.keyword_id
                                WHERE k.user_id=%s AND k.site=%s
                                  AND c.date <= (SELECT max(c2.date) FROM rank_checks c2 WHERE c2.keyword_id = c.keyword_id) - %s
                                ORDER BY c.keyword_id, c.date DESC""", (user_id, site, before_days)).fetchall()
        return {str(r["keyword_id"]): r["ranks"] for r in rows}

    def rank_keyword_checks(self, keyword_id: str) -> list[dict]:
        with self.conn() as c:
            return c.execute("SELECT date, position, url, ranks FROM rank_checks WHERE keyword_id=%s ORDER BY date",
                             (keyword_id,)).fetchall()

    def rank_competitors(self, user_id: str, site: str) -> list[str]:
        with self.conn() as c:
            return [r["domain"] for r in c.execute(
                "SELECT domain FROM rank_competitors WHERE user_id=%s AND site=%s ORDER BY created_at", (user_id, site))]

    def add_rank_competitor(self, user_id: str, site: str, domain: str) -> None:
        with self.conn() as c:
            c.execute("INSERT INTO rank_competitors (user_id, site, domain) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                      (user_id, site, domain))

    def delete_rank_competitor(self, user_id: str, site: str, domain: str) -> None:
        with self.conn() as c:
            c.execute("DELETE FROM rank_competitors WHERE user_id=%s AND site=%s AND domain=%s", (user_id, site, domain))

    # moteur (tous utilisateurs confondus)
    def rank_due_groups(self, day, limit: int = 100) -> list[dict]:
        with self.conn() as c:
            return c.execute("""SELECT k.keyword, k.location_code, k.device FROM rank_keywords k
                                WHERE (k.last_checked IS NULL OR k.last_checked < %s)
                                  AND NOT EXISTS (SELECT 1 FROM serp_tasks t WHERE t.keyword = k.keyword
                                       AND t.location_code = k.location_code AND t.device = k.device AND t.date = %s)
                                GROUP BY 1, 2, 3 LIMIT %s""", (day, day, limit)).fetchall()

    def rank_due_rows(self, keyword: str, location: int, device: str, day) -> list[dict]:
        with self.conn() as c:
            return c.execute("""SELECT id, site FROM rank_keywords WHERE keyword=%s AND location_code=%s AND device=%s
                                AND (last_checked IS NULL OR last_checked < %s)""", (keyword, location, device, day)).fetchall()

    def save_rank_check(self, keyword_id, day, position, url, ranks) -> None:
        with self.conn() as c:
            c.execute("""INSERT INTO rank_checks (keyword_id, date, position, url, ranks) VALUES (%s,%s,%s,%s,%s)
                         ON CONFLICT (keyword_id, date) DO UPDATE SET position=EXCLUDED.position, url=EXCLUDED.url,
                         ranks=EXCLUDED.ranks""", (keyword_id, day, position, url, Jsonb(ranks)))
            c.execute("UPDATE rank_keywords SET last_checked=%s WHERE id=%s", (day, keyword_id))

    def serp_tasks_pending(self) -> dict:
        with self.conn() as c:
            return {r["task_id"]: r for r in c.execute("SELECT * FROM serp_tasks")}

    def add_serp_task(self, task_id, keyword, location, device, day) -> None:
        with self.conn() as c:
            c.execute("""INSERT INTO serp_tasks (task_id, keyword, location_code, device, date) VALUES (%s,%s,%s,%s,%s)
                         ON CONFLICT DO NOTHING""", (task_id, keyword, location, device, day))

    def delete_serp_task(self, task_id) -> None:
        with self.conn() as c:
            c.execute("DELETE FROM serp_tasks WHERE task_id=%s", (task_id,))

    def purge_stale_serp_tasks(self, hours: int) -> None:
        with self.conn() as c:
            c.execute("DELETE FROM serp_tasks WHERE created_at < now() - make_interval(hours => %s)", (hours,))

    # -- notifications -------------------------------------------------------------------------------
    def notification_settings(self, user_id: str) -> dict:
        with self.conn() as c:
            row = c.execute("SELECT weekly_digest, alerts FROM notification_settings WHERE user_id=%s", (user_id,)).fetchone()
        return dict(row) if row else {"weekly_digest": True, "alerts": True}

    def set_notification_settings(self, user_id: str, **values) -> None:
        cols = [k for k in ("weekly_digest", "alerts", "last_digest_at", "last_alert_email_at") if k in values]
        with self.conn() as c:
            c.execute(f"""INSERT INTO notification_settings (user_id, {", ".join(cols)}) VALUES (%s, {", ".join(["%s"] * len(cols))})
                          ON CONFLICT (user_id) DO UPDATE SET {", ".join(f"{k}=EXCLUDED.{k}" for k in cols)}, updated_at=now()""",
                      (user_id, *[values[k] for k in cols]))

    def audits_to_notify(self) -> list[dict]:
        with self.conn() as c:
            c.execute("UPDATE audits SET notified_at=now() WHERE notified_at IS NULL AND status='failed'")
            return c.execute("""SELECT id::text, user_id::text, site, summary FROM audits
                                WHERE notified_at IS NULL AND status='done' AND kind='site' ORDER BY finished_at LIMIT 50""").fetchall()

    def mark_audit_notified(self, audit_id: str) -> None:
        with self.conn() as c:
            c.execute("UPDATE audits SET notified_at=now() WHERE id=%s", (audit_id,))

    def add_alert(self, user_id, key, kind, tone, site, title, detail="", link="") -> int:
        with self.conn() as c:
            return c.execute("""INSERT INTO alerts (user_id, dedupe_key, kind, tone, site, title, detail, link)
                                VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (user_id, dedupe_key) DO NOTHING""",
                             (user_id, key, kind, tone, site, title, detail, link)).rowcount

    def rank_moves(self) -> list[dict]:
        """Relevés d'hier et d'aujourd'hui comparés au jour précédent."""
        with self.conn() as c:
            return c.execute("""SELECT k.id::text keyword_id, k.user_id::text, k.site, k.keyword, c.date, c.position, p.position prev
                                FROM rank_checks c JOIN rank_keywords k ON k.id = c.keyword_id
                                JOIN rank_checks p ON p.keyword_id = c.keyword_id AND p.date = c.date - 1
                                WHERE c.date >= current_date - 1""").fetchall()

    def users_with_pending_alerts(self, sent_before) -> list[dict]:
        with self.conn() as c:
            return c.execute("""SELECT a.user_id::text, p.email, p.name, coalesce(s.alerts, true) alerts_enabled
                                FROM alerts a JOIN profiles p ON p.user_id = a.user_id
                                LEFT JOIN notification_settings s ON s.user_id = a.user_id
                                WHERE a.sent_at IS NULL AND (s.last_alert_email_at IS NULL OR s.last_alert_email_at < %s)
                                GROUP BY 1, 2, 3, 4 LIMIT 50""", (sent_before,)).fetchall()

    def pending_alerts(self, user_id: str) -> list[dict]:
        with self.conn() as c:
            return c.execute("""SELECT id, kind, tone, site, title, detail, link FROM alerts
                                WHERE user_id=%s AND sent_at IS NULL ORDER BY tone, created_at LIMIT 20""", (user_id,)).fetchall()

    def mark_alerts_sent(self, user_id: str, ids: list, now) -> None:
        with self.conn() as c:
            c.execute("UPDATE alerts SET sent_at=%s WHERE user_id=%s AND id = ANY(%s)", (now, user_id, ids))
        self.set_notification_settings(user_id, last_alert_email_at=now)

    def users_due_digest(self, week_start, limit: int = 10) -> list[dict]:
        with self.conn() as c:
            return c.execute("""SELECT p.user_id::text, p.email, p.name FROM profiles p
                                LEFT JOIN notification_settings s ON s.user_id = p.user_id
                                WHERE coalesce(s.weekly_digest, true) AND coalesce(s.last_digest_at, p.created_at) < %s
                                ORDER BY coalesce(s.last_digest_at, p.created_at) LIMIT %s""", (week_start, limit)).fetchall()

    def mark_digest_sent(self, user_id: str, now) -> None:
        self.set_notification_settings(user_id, last_digest_at=now)

    def gsc_week(self, user_id: str, site: str) -> dict | None:
        with self.conn() as c:
            last = c.execute("SELECT max(date) d FROM gsc_daily WHERE user_id=%s AND site=%s", (user_id, site)).fetchone()["d"]
            if not last:
                return None
            sql = """SELECT coalesce(sum(clicks),0) clicks, coalesce(sum(impressions),0) impressions FROM gsc_daily
                     WHERE user_id=%s AND site=%s AND date BETWEEN %s AND %s"""
            cur = c.execute(sql, (user_id, site, last - timedelta(days=6), last)).fetchone()
            prev = c.execute(sql, (user_id, site, last - timedelta(days=13), last - timedelta(days=7))).fetchone()

        def pct(a, b):
            return round((a - b) / b * 100) if b else None
        return {"clicks": round(cur["clicks"]), "impressions": round(cur["impressions"]),
                "clicks_pct": pct(cur["clicks"], prev["clicks"]), "impressions_pct": pct(cur["impressions"], prev["impressions"])}

    def top_tasks(self, user_id: str, n: int = 3) -> list[dict]:
        with self.conn() as c:
            return c.execute("""SELECT title, site, priority FROM tasks WHERE user_id=%s AND status='todo'
                                ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, created_at
                                LIMIT %s""", (user_id, n)).fetchall()

    # -- cache des données payantes ----------------------------------------------------------
    def cache_get(self, key: str, max_age_days: int = 30):
        with self.conn() as c:
            row = c.execute("SELECT data FROM data_cache WHERE key=%s AND fetched_at > now() - make_interval(days => %s)",
                            (key, max_age_days)).fetchone()
        return row["data"] if row else None

    def cache_put(self, key: str, data) -> None:
        with self.conn() as c:
            c.execute("""INSERT INTO data_cache (key, data) VALUES (%s,%s)
                         ON CONFLICT (key) DO UPDATE SET data=EXCLUDED.data, fetched_at=now()""", (key, Jsonb(data)))

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

