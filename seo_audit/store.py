"""Stockage SQLite local (data/seo.db) : positions Search Console, idées de mots-clés, concurrents."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path("data") / "seo.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS gsc_daily (
    site TEXT NOT NULL, date TEXT NOT NULL, query TEXT NOT NULL,
    clicks REAL, impressions REAL, ctr REAL, position REAL,
    PRIMARY KEY (site, date, query)
);
CREATE INDEX IF NOT EXISTS gsc_site_date ON gsc_daily (site, date);
CREATE TABLE IF NOT EXISTS keyword_ideas (
    seed TEXT NOT NULL, idea TEXT NOT NULL, kind TEXT NOT NULL, created TEXT NOT NULL,
    PRIMARY KEY (seed, idea)
);
CREATE TABLE IF NOT EXISTS competitor_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT, site TEXT NOT NULL, competitor TEXT NOT NULL,
    created TEXT NOT NULL, profile TEXT NOT NULL
);
"""


@contextmanager
def connect(path: Path | None = None):
    path = path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def save_gsc_rows(site: str, rows: list[dict]) -> int:
    with connect() as db:
        db.executemany(
            "INSERT OR REPLACE INTO gsc_daily VALUES (?,?,?,?,?,?,?)",
            [(site, r["date"], r["query"], r["clicks"], r["impressions"], r["ctr"], r["position"]) for r in rows])
    return len(rows)


def gsc_sites() -> list[str]:
    with connect() as db:
        return [r[0] for r in db.execute("SELECT DISTINCT site FROM gsc_daily ORDER BY site")]


def positions_summary(site: str, days: int = 28) -> dict:
    """Requêtes de la période courante comparées à la période précédente de même durée."""
    with connect() as db:
        last = db.execute("SELECT MAX(date) FROM gsc_daily WHERE site=?", (site,)).fetchone()[0]
        if not last:
            return {"site": site, "last_date": None, "queries": [], "daily": []}
        end = datetime.fromisoformat(last).date()
        cur_start = (end - timedelta(days=days - 1)).isoformat()
        prev_start = (end - timedelta(days=2 * days - 1)).isoformat()
        sql = """SELECT query, SUM(clicks) clicks, SUM(impressions) impressions,
                        SUM(position * impressions) / NULLIF(SUM(impressions), 0) position
                 FROM gsc_daily WHERE site=? AND date BETWEEN ? AND ? GROUP BY query"""
        cur = {r["query"]: dict(r) for r in db.execute(sql, (site, cur_start, last))}
        prev_end = (end - timedelta(days=days)).isoformat()
        prev = {r["query"]: dict(r) for r in db.execute(sql, (site, prev_start, prev_end))}
        queries = []
        for q, c in cur.items():
            p = prev.get(q)
            queries.append({
                "query": q, "clicks": c["clicks"], "impressions": c["impressions"],
                "ctr": c["clicks"] / c["impressions"] if c["impressions"] else 0,
                "position": round(c["position"], 1) if c["position"] is not None else None,
                "previous_position": round(p["position"], 1) if p and p["position"] is not None else None,
            })
        queries.sort(key=lambda x: -x["impressions"])
        daily = [dict(r) for r in db.execute(
            """SELECT date, SUM(clicks) clicks, SUM(impressions) impressions,
                      SUM(position * impressions) / NULLIF(SUM(impressions), 0) position
               FROM gsc_daily WHERE site=? AND date BETWEEN ? AND ? GROUP BY date ORDER BY date""",
            (site, cur_start, last))]
    return {"site": site, "last_date": last, "days": days, "queries": queries[:1000], "daily": daily}


def query_history(site: str, query: str) -> list[dict]:
    with connect() as db:
        return [dict(r) for r in db.execute(
            "SELECT date, clicks, impressions, position FROM gsc_daily WHERE site=? AND query=? ORDER BY date",
            (site, query))]


def save_ideas(seed: str, ideas: list[tuple[str, str]]) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    with connect() as db:
        db.executemany("INSERT OR REPLACE INTO keyword_ideas VALUES (?,?,?,?)",
                       [(seed, idea, kind, now) for idea, kind in ideas])


def list_ideas(seed: str) -> list[dict]:
    with connect() as db:
        return [dict(r) for r in db.execute(
            "SELECT idea, kind, created FROM keyword_ideas WHERE seed=? ORDER BY kind, idea", (seed,))]


def save_competitor(site: str, competitor: str, profile: dict) -> int:
    with connect() as db:
        cur = db.execute(
            "INSERT INTO competitor_snapshots (site, competitor, created, profile) VALUES (?,?,?,?)",
            (site, competitor, datetime.now().isoformat(timespec="seconds"), json.dumps(profile)))
        return cur.lastrowid


def list_competitors(site: str) -> list[dict]:
    with connect() as db:
        rows = db.execute("SELECT * FROM competitor_snapshots WHERE site=? ORDER BY id DESC", (site,))
        return [{**dict(r), "profile": json.loads(r["profile"])} for r in rows]
