"""JobStore en mémoire : tests et essais locaux, même contrat que PgStore."""
import itertools
import time

from ..urls import site_key


class MemoryStore:
    def __init__(self):
        self.audits: dict[str, dict] = {}
        self.pages: dict[str, dict[str, dict]] = {}
        self.reports: dict[str, dict] = {}
        self._ids = itertools.count(1)

    def create_audit(self, user_id: str, url: str, options: dict | None = None,
                     kind: str = "site", parent_site: str | None = None) -> str:
        audit_id = f"a{next(self._ids)}"
        self.audits[audit_id] = {
            "id": audit_id, "user_id": user_id, "url": url, "site": site_key(url), "status": "queued",
            "phase": "init", "options": options or {}, "robots_text": None, "sitemap_urls": [],
            "error": None, "created_at": time.time(), "started_at": None, "finished_at": None, "summary": None,
            "kind": kind, "parent_site": parent_site, "lease_until": 0, "lease_owner": None,
            "pages_done": 0, "pages_total": 0}
        self.pages[audit_id] = {}
        return audit_id

    def get_audit(self, audit_id):
        return dict(self.audits[audit_id])

    def update_audit(self, audit_id, **fields):
        self.audits[audit_id].update(fields)

    def acquire(self, audit_id, owner, seconds):
        a = self.audits[audit_id]
        if a["lease_until"] > time.time():
            return False
        a.update(lease_until=time.time() + seconds, lease_owner=owner)
        return True

    def release(self, audit_id, owner):
        a = self.audits[audit_id]
        if a["lease_owner"] == owner:
            a.update(lease_until=0, lease_owner=None)

    def active_audits(self):
        return [a["id"] for a in sorted(self.audits.values(), key=lambda a: a["created_at"])
                if a["status"] in ("queued", "running")]

    def add_pending(self, audit_id, items, max_pages):
        pages, added = self.pages[audit_id], 0
        for url, depth in items:
            if url in pages or len(pages) >= max_pages:
                continue
            pages[url] = {"state": "pending", "depth": depth, "result": None}
            added += 1
        return added

    def claim_pending(self, audit_id, limit):
        out = []
        for url, p in self.pages[audit_id].items():
            if p["state"] == "pending" and len(out) < limit:
                p["state"] = "claimed"
                out.append({"url": url, "depth": p["depth"]})
        return out

    def save_pages(self, audit_id, results):
        for url, data in results:
            self.pages[audit_id][url].update(state="done", result=data)

    def counts(self, audit_id):
        states = [p["state"] for p in self.pages[audit_id].values()]
        return {"pending": sum(s != "done" for s in states), "done": states.count("done"), "total": len(states)}

    def load_pages(self, audit_id):
        return [(u, p["result"]) for u, p in self.pages[audit_id].items() if p["state"] == "done"]

    def purge_pages(self, audit_id):
        self.pages[audit_id] = {}

    def save_report(self, audit_id, summary, issues, rows):
        self.reports[audit_id] = {"summary": summary, "issues": issues, "pages": rows}

    def previous_report(self, user_id, site, exclude_id):
        done = [a for a in self.audits.values() if a["user_id"] == user_id and a["site"] == site
                and a["status"] == "done" and a["id"] != exclude_id and a["kind"] == "site"]
        if not done:
            return None
        return self.reports[max(done, key=lambda a: a["finished_at"])["id"]]
