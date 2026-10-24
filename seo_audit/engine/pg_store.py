"""JobStore Postgres (Supabase). Utilise une connexion directe ou le pooleur Supabase (mode transaction) :
les requêtes préparées sont donc désactivées. Le moteur se connecte avec un rôle qui contourne la RLS."""
from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from ..urls import site_key

AUDIT_FIELDS = {"status", "phase", "options", "robots_text", "sitemap_urls", "error", "started_at",
                "finished_at", "summary", "pages_done", "pages_total"}
JSON_FIELDS = {"options", "sitemap_urls", "summary"}
CLAIM_TIMEOUT = "2 minutes"  # une ligne « claimed » plus ancienne est reprise (instance morte)


class PgStore:
    def __init__(self, dsn: str):
        self.dsn = dsn

    def _conn(self):
        return psycopg.connect(self.dsn, row_factory=dict_row, prepare_threshold=None)

    def create_audit(self, user_id: str, url: str, options: dict | None = None,
                     kind: str = "site", parent_site: str | None = None) -> str:
        with self._conn() as c:
            row = c.execute("INSERT INTO audits (user_id, url, site, options, kind, parent_site) "
                            "VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
                            (user_id, url, site_key(url), Jsonb(options or {}), kind, parent_site)).fetchone()
        return str(row["id"])

    def acquire(self, audit_id, owner, seconds):
        with self._conn() as c:
            return c.execute("""UPDATE audits SET lease_until = now() + make_interval(secs => %s), lease_owner=%s
                                WHERE id=%s AND (lease_until IS NULL OR lease_until < now()) RETURNING id""",
                             (seconds, owner, audit_id)).fetchone() is not None

    def release(self, audit_id, owner):
        with self._conn() as c:
            c.execute("UPDATE audits SET lease_until=NULL, lease_owner=NULL WHERE id=%s AND lease_owner=%s",
                      (audit_id, owner))

    def get_audit(self, audit_id):
        with self._conn() as c:
            row = c.execute("SELECT * FROM audits WHERE id=%s", (audit_id,)).fetchone()
        row["id"], row["user_id"] = str(row["id"]), str(row["user_id"])
        return row

    def update_audit(self, audit_id, **fields):
        bad = set(fields) - AUDIT_FIELDS
        if bad:
            raise ValueError(f"champs inconnus : {bad}")
        values = []
        for k, v in fields.items():
            if k.endswith("_at") and isinstance(v, (int, float)):
                v = datetime.fromtimestamp(v, timezone.utc)
            values.append(Jsonb(v) if k in JSON_FIELDS and v is not None else v)
        sets = ", ".join(f"{k}=%s" for k in fields)
        with self._conn() as c:
            c.execute(f"UPDATE audits SET {sets} WHERE id=%s", (*values, audit_id))

    def active_audits(self):
        with self._conn() as c:
            return [str(r["id"]) for r in c.execute(
                "SELECT id FROM audits WHERE status IN ('queued','running') ORDER BY created_at")]

    def add_pending(self, audit_id, items, max_pages):
        added = 0
        with self._conn() as c:
            total = c.execute("SELECT count(*) AS n FROM audit_pages WHERE audit_id=%s", (audit_id,)).fetchone()["n"]
            for url, depth in items:
                if total >= max_pages:
                    break
                cur = c.execute("INSERT INTO audit_pages (audit_id, url, depth) VALUES (%s,%s,%s) "
                                "ON CONFLICT DO NOTHING", (audit_id, url, depth))
                added += cur.rowcount
                total += cur.rowcount
        return added

    def claim_pending(self, audit_id, limit):
        with self._conn() as c:
            return c.execute(f"""
                UPDATE audit_pages SET state='claimed', claimed_at=now()
                WHERE audit_id=%s AND url IN (
                    SELECT url FROM audit_pages WHERE audit_id=%s
                      AND (state='pending' OR (state='claimed' AND claimed_at < now() - interval '{CLAIM_TIMEOUT}'))
                    ORDER BY created_at, url LIMIT %s FOR UPDATE SKIP LOCKED)
                RETURNING url, depth""", (audit_id, audit_id, limit)).fetchall()

    def save_pages(self, audit_id, results):
        with self._conn() as c:
            c.cursor().executemany(
                "UPDATE audit_pages SET state='done', result=%s WHERE audit_id=%s AND url=%s",
                [(Jsonb(data), audit_id, url) for url, data in results])

    def counts(self, audit_id):
        with self._conn() as c:
            r = c.execute("""SELECT count(*) FILTER (WHERE state <> 'done') AS pending,
                                    count(*) FILTER (WHERE state = 'done') AS done, count(*) AS total
                             FROM audit_pages WHERE audit_id=%s""", (audit_id,)).fetchone()
        return dict(r)

    def load_pages(self, audit_id):
        with self._conn() as c:
            return [(r["url"], r["result"]) for r in c.execute(
                "SELECT url, result FROM audit_pages WHERE audit_id=%s AND state='done'", (audit_id,))]

    def purge_pages(self, audit_id):
        with self._conn() as c:
            c.execute("DELETE FROM audit_pages WHERE audit_id=%s", (audit_id,))

    def save_report(self, audit_id, summary, issues, rows):
        with self._conn() as c:
            c.execute("""INSERT INTO audit_reports (audit_id, issues, pages) VALUES (%s,%s,%s)
                         ON CONFLICT (audit_id) DO UPDATE SET issues=EXCLUDED.issues, pages=EXCLUDED.pages""",
                      (audit_id, Jsonb(issues), Jsonb(rows)))

    def previous_report(self, user_id, site, exclude_id):
        with self._conn() as c:
            row = c.execute("""SELECT a.summary, r.issues FROM audits a JOIN audit_reports r ON r.audit_id = a.id
                               WHERE a.user_id=%s AND a.site=%s AND a.status='done' AND a.id <> %s
                                 AND a.kind = 'site'
                               ORDER BY a.finished_at DESC LIMIT 1""", (user_id, site, exclude_id)).fetchone()
        return {"summary": row["summary"], "issues": row["issues"]} if row else None
