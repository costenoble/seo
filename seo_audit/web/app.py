"""Tableau de bord local : python -m seo_audit serve  (http://127.0.0.1:8765).

Lit les audits dans rapports/, stocke positions, idées et concurrents dans data/seo.db.
Serveur local uniquement : les requêtes dont l'hôte n'est pas local sont refusées, et les
écritures exigent du JSON (ce qui empêche une page web tierce de les déclencher)."""
import asyncio
import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from flask import Flask, abort, jsonify, redirect, request, send_from_directory

from .. import oauth, store
from ..urls import is_http, site_key

REPORTS = Path("rapports")
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
DIR_RE = re.compile(r"^[A-Za-z0-9._-]+-\d{8}-\d{4}$")


class Jobs:
    """Une seule tâche longue à la fois (audit ou analyse de concurrent), journal en mémoire."""

    def __init__(self):
        self.items: dict[str, dict] = {}
        self.lock = threading.Lock()

    def busy(self) -> bool:
        return any(j["status"] == "running" for j in self.items.values())

    def start(self, kind: str, label: str, fn) -> dict:
        job = {"id": uuid.uuid4().hex[:10], "kind": kind, "label": label, "status": "running",
               "log": [], "started": datetime.now().isoformat(timespec="seconds"), "result": None}
        self.items[job["id"]] = job

        class Handler(logging.Handler):
            def emit(self, record):
                job["log"].append(record.getMessage())
                del job["log"][:-200]

        def runner():
            handler = Handler()
            logger = logging.getLogger("seo_audit")
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
            try:
                job["result"] = fn()
                job["status"] = "done"
            except SystemExit as exc:
                job["status"], job["error"] = "failed", str(exc)
            except Exception as exc:
                job["status"], job["error"] = "failed", f"{type(exc).__name__}: {exc}"
            finally:
                logger.removeHandler(handler)

        threading.Thread(target=runner, daemon=True).start()
        return job


def audit_dirs() -> list[Path]:
    if not REPORTS.exists():
        return []
    return sorted((d for d in REPORTS.iterdir() if d.is_dir() and DIR_RE.match(d.name)
                   and (d / "audit.json").exists()), key=lambda d: d.name)


def dir_site(d: Path) -> str:
    return d.name.rsplit("-", 2)[0]


def load_summary(d: Path) -> dict:
    return json.loads((d / "audit.json").read_text(encoding="utf-8"))["summary"]


def brief(d: Path) -> dict:
    s = load_summary(d)
    return {"dir": d.name, "date": s["date"], "score": s["health_score"],
            "category_scores": s.get("category_scores", {}),
            "pages": s["pages_crawled"], "indexable": s["pages_indexable"],
            "issues_by_severity": s["issues_by_severity"],
            "comparison": {k: v for k, v in (s.get("comparison") or {}).items() if k in
                           ("score_delta", "new_count", "fixed_count")} or None}


def create_app() -> Flask:
    app = Flask(__name__, static_folder=None)
    jobs = Jobs()
    static = Path(__file__).parent / "static"

    @app.before_request
    def guard():
        if urlsplit("//" + (request.host or "")).hostname not in LOCAL_HOSTS:
            abort(403)
        if request.method in ("POST", "PUT", "DELETE"):
            origin = request.headers.get("Origin")
            if origin and urlsplit(origin).hostname not in {"127.0.0.1", "localhost", "::1"}:
                abort(403)
            if not request.is_json:
                abort(415)

    @app.after_request
    def headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Cache-Control"] = "no-store"
        return resp

    # -- pages statiques ---------------------------------------------------
    @app.get("/")
    def index():
        return send_from_directory(static, "index.html")

    @app.get("/static/<path:name>")
    def assets(name):
        return send_from_directory(static, name)

    @app.get("/reports/<name>/<path:filename>")
    def report_file(name, filename):
        if not DIR_RE.match(name):
            abort(404)
        return send_from_directory(REPORTS.resolve() / name, filename)

    # -- audits -------------------------------------------------------------
    @app.get("/api/sites")
    def sites():
        groups: dict[str, list[Path]] = {}
        for d in audit_dirs():
            groups.setdefault(dir_site(d), []).append(d)
        out = []
        for key, dirs in groups.items():
            history = [brief(d) for d in dirs]
            latest = history[-1]
            out.append({"key": key, "latest": latest, "audits": len(history),
                        "history": [{"dir": h["dir"], "date": h["date"], "score": h["score"]} for h in history]})
        out.sort(key=lambda s: s["latest"]["date"], reverse=True)
        return jsonify(out)

    @app.get("/api/sites/<key>")
    def site(key):
        dirs = [d for d in audit_dirs() if dir_site(d) == key]
        if not dirs:
            abort(404)
        return jsonify({"key": key, "audits": [brief(d) for d in reversed(dirs)]})

    @app.get("/api/audits/<name>")
    def audit(name):
        if not DIR_RE.match(name) or not (REPORTS / name / "audit.json").exists():
            abort(404)
        return app.response_class((REPORTS / name / "audit.json").read_text(encoding="utf-8"),
                                  mimetype="application/json")

    @app.post("/api/audits")
    def start_audit():
        body = request.get_json()
        url = (body.get("url") or "").strip()
        if "://" not in url:
            url = "https://" + url
        if not is_http(url) or not urlsplit(url).hostname:
            return jsonify(error="URL invalide"), 400
        if jobs.busy():
            return jsonify(error="Une tâche est déjà en cours"), 409
        max_pages = max(1, min(int(body.get("max_pages") or 200), 5000))

        def run():
            from ..cli import build_parser, run as run_audit
            out = REPORTS / f"{site_key(url)}-{datetime.now():%Y%m%d-%H%M}"
            args = build_parser().parse_args([
                url, "--max-pages", str(max_pages), "--output", str(out),
                *(["--check-external"] if body.get("check_external") else [])])
            asyncio.run(run_audit(args))
            return {"dir": out.name, "site": site_key(url)}

        return jsonify(jobs.start("audit", url, run)), 202

    @app.get("/api/jobs/<job_id>")
    def job(job_id):
        j = jobs.items.get(job_id)
        if not j:
            abort(404)
        return jsonify(j)

    # -- positions ----------------------------------------------------------
    @app.get("/api/positions")
    def positions_sites():
        return jsonify(store.gsc_sites())

    @app.get("/api/positions/summary")
    def positions():
        site = request.args.get("site", "")
        return jsonify(store.positions_summary(site, max(7, min(int(request.args.get("days", 28)), 180))))

    @app.get("/api/positions/history")
    def positions_history():
        return jsonify(store.query_history(request.args.get("site", ""), request.args.get("query", "")))

    @app.post("/api/positions/sync")
    def positions_sync():
        body = request.get_json()
        site_url = (body.get("site") or "").strip()
        creds = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        creds = creds if creds and Path(creds).is_file() else None
        if not site_url:
            return jsonify(error="Propriété Search Console manquante"), 400
        if not oauth.status()["connected"] and not creds:
            return jsonify(error="Connectez d'abord votre compte Google Search Console"), 400
        if oauth.status()["connected"]:
            creds = None  # la connexion de l'utilisateur prime sur un éventuel compte de service
        if jobs.busy():
            return jsonify(error="Une tâche est déjà en cours"), 409

        def run():
            from ..tracking import sync_positions
            return {"rows": asyncio.run(sync_positions(site_url, creds, int(body.get("days") or 90))),
                    "site": site_url}

        return jsonify(jobs.start("positions", site_url, run)), 202

    # -- connexion Google (OAuth) ---------------------------------------------
    @app.get("/api/google/status")
    def google_status():
        return jsonify(oauth.status())

    @app.post("/api/google/client")
    def google_client():
        try:
            oauth.save_client(request.get_json().get("json") or "")
        except (ValueError, AttributeError) as exc:
            return jsonify(error=str(exc)), 400
        return jsonify(oauth.status())

    @app.post("/api/google/connect")
    def google_connect():
        redirect_uri = request.host_url.rstrip("/") + "/oauth/callback"
        try:
            return jsonify(url=oauth.begin_login(redirect_uri))
        except ValueError as exc:
            return jsonify(error=str(exc)), 400

    @app.get("/oauth/callback")
    def google_callback():
        if request.args.get("error"):
            return redirect("/#/positions?google=" + ("refuse" if request.args["error"] == "access_denied" else "erreur"))
        try:
            oauth.finish_login(request.args.get("state", ""), request.args.get("code", ""))
        except (ValueError, httpx.HTTPError):
            return redirect("/#/positions?google=erreur")
        return redirect("/#/positions?google=ok")

    @app.post("/api/google/disconnect")
    def google_disconnect():
        oauth.disconnect()
        return jsonify(oauth.status())

    @app.get("/api/google/sites")
    def google_sites():
        try:
            return jsonify(oauth.list_properties())
        except PermissionError as exc:
            return jsonify(error=str(exc)), 401
        except httpx.HTTPError as exc:
            return jsonify(error=f"Google injoignable : {exc}"), 502
        except Exception as exc:
            return jsonify(error=f"Erreur Google : {exc}"), 502

    # -- mots-clés ----------------------------------------------------------
    @app.post("/api/keywords")
    def keywords():
        from .. import keywords as kw
        body = request.get_json()
        seed = " ".join((body.get("seed") or "").split())[:80]
        if len(seed) < 2:
            return jsonify(error="Mot-clé trop court"), 400
        ideas = asyncio.run(kw.autocomplete(seed))
        site_url = body.get("site")
        if site_url:
            ideas += kw.from_search_console(seed, store.positions_summary(site_url, 90)["queries"])
        merged = {}
        for idea, kind in ideas:
            merged.setdefault(idea, kind)
        store.save_ideas(seed, list(merged.items()))
        return jsonify(store.list_ideas(seed))

    # -- concurrents --------------------------------------------------------
    @app.get("/api/competitors/<key>")
    def competitors(key):
        from ..competitors import own_profile
        dirs = [d for d in audit_dirs() if dir_site(d) == key]
        own = own_profile(dirs[-1] / "audit.json") if dirs else None
        return jsonify({"own": own, "snapshots": store.list_competitors(key)})

    @app.post("/api/competitors")
    def competitor_add():
        body = request.get_json()
        key, url = body.get("site", ""), (body.get("url") or "").strip()
        if "://" not in url:
            url = "https://" + url
        if not key or not is_http(url) or not urlsplit(url).hostname:
            return jsonify(error="Paramètres invalides"), 400
        if jobs.busy():
            return jsonify(error="Une tâche est déjà en cours"), 409

        def run():
            from ..competitors import profile_site
            profile = asyncio.run(profile_site(url, max_pages=min(int(body.get("max_pages") or 30), 100)))
            store.save_competitor(key, url, profile)
            return {"competitor": url}

        return jsonify(jobs.start("competitor", url, run)), 202

    return app


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    print(f"Tableau de bord : http://{host}:{port}  (Ctrl+C pour arrêter)")
    create_app().run(host=host, port=port, threaded=True)
