"""API de Vigie SEO (Vercel : api/index.py). Toutes les routes /api/* sauf /api/config et /api/worker/tick
exigent une session Supabase (Authorization: Bearer <access_token>) ; pas de cookie, donc pas de CSRF."""
import asyncio
import hmac
import json
import logging
import time
from functools import wraps
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from flask import Flask, Response, abort, g, jsonify, request, send_from_directory

from .. import keywords as kw
from ..engine import run_tick, step_audit
from ..models import CATEGORY_LABELS, SEVERITY_LABELS
from ..ssrf import check_public_url
from ..urls import is_http, normalize_url, site_key
from . import crypto, google
from .insights import analyze
from .auth import AuthError, verify_token
from .config import APP_NAME, Settings
from .db import Db
from .plans import PLANS, QuotaError, check_monthly, limits

log = logging.getLogger(__name__)
PUBLIC_DIR = Path(__file__).resolve().parents[2] / "public"
TEMPLATE = Path(__file__).resolve().parents[1] / "report_template.html"


def create_app(settings: Settings | None = None) -> Flask:
    settings = settings or Settings.from_env()
    app = Flask(__name__, static_folder=None)
    app.json.sort_keys = False  # garder l'ordre des offres (gratuit, pro, agence)
    db = Db(settings.database_url)
    guard = None if settings.allow_private_urls else check_public_url

    @app.errorhandler(QuotaError)
    def quota(exc):
        return jsonify(error=str(exc), quota=True), 402

    @app.errorhandler(google.GoogleError)
    def google_error(exc):
        return jsonify(error=str(exc), reconnect=exc.reconnect), 409 if exc.reconnect else 502

    @app.errorhandler(Exception)
    def unexpected(exc):
        if hasattr(exc, "code") and hasattr(exc, "description"):  # erreurs HTTP de Flask
            return jsonify(error=exc.description), exc.code
        log.exception("Erreur inattendue")
        return jsonify(error="Erreur interne, réessayez plus tard"), 500

    @app.after_request
    def headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        if request.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    def authed(fn):
        @wraps(fn)
        def wrapper(*a, **kw_):
            header = request.headers.get("Authorization", "")
            try:
                g.user = verify_token(header[7:] if header.startswith("Bearer ") else "", settings)
            except AuthError as exc:
                return jsonify(error=str(exc)), 401
            g.profile = db.profile(g.user)
            g.limits = limits(g.profile["plan"])
            return fn(*a, **kw_)
        return wrapper

    def body() -> dict:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400, "Corps JSON attendu")
        return data

    def clean_url(raw: str) -> str:
        raw = (raw or "").strip()[:2000]
        if "://" not in raw:
            raw = "https://" + raw
        if not is_http(raw) or not urlsplit(raw).hostname:
            abort(400, "Adresse invalide")
        if guard:
            try:
                guard(raw)
            except ValueError as exc:
                abort(400, f"Adresse refusée : {exc}")
        return normalize_url(raw)

    def user_id() -> str:
        return g.user["id"]

    # -- public --------------------------------------------------------------------------
    @app.get("/api/config")
    def config():
        return jsonify(app_name=APP_NAME, supabase_url=settings.supabase_url,
                       supabase_anon_key=settings.supabase_anon_key, google_scope=google.SCOPE,
                       plans=PLANS, missing=settings.missing())

    @app.route("/api/worker/tick", methods=["GET", "POST"])
    def tick():
        """Appelé chaque minute par pg_cron (Supabase) et une fois par jour par Vercel Cron."""
        expected = f"Bearer {settings.cron_secret}"
        if not settings.cron_secret or not hmac.compare_digest(request.headers.get("Authorization", ""), expected):
            return jsonify(error="Non autorisé"), 401
        start = time.monotonic()
        touched = asyncio.run(run_tick(db.store, budget=settings.step_budget, url_guard=guard))
        synced = []
        while time.monotonic() - start < settings.step_budget - 10:  # imports Search Console quotidiens
            due = db.due_properties(limit=1)
            if not due:
                break
            p = due[0]
            try:
                _sync_recent(p["user_id"], p["site"], p["refresh_token_enc"])
                synced.append(p["site"])
            except Exception as exc:  # une propriété en erreur ne bloque pas les autres
                log.warning("Import quotidien %s : %s", p["site"], exc)
            db.mark_synced(p["user_id"], p["site"])
        return jsonify(audits=touched, gsc=synced)

    def _sync_recent(uid, site, enc):
        token = crypto.decrypt(settings.token_key, enc)
        if not token:
            return
        access = google.access_token(token, settings.google_client_id, settings.google_client_secret)
        start, end = google.date_range(5)
        offset = 0
        while True:
            rows = google.search_analytics(access, site, start, end, offset)
            db.save_gsc_rows(uid, site, rows)
            if len(rows) < google.PAGE_SIZE:
                break
            offset += google.PAGE_SIZE
        _sync_pages(uid, site, access)

    def _sync_pages(uid, site, access):
        """Instantané requête × page et page sur 90 jours (au plus 2 × 25 000 couples requête-page)."""
        start, end = google.date_range(90)
        query_pages = []
        for offset in (0, google.PAGE_SIZE):
            rows = google.search_analytics_by(access, site, start, end, ["query", "page"], offset)
            query_pages += rows
            if len(rows) < google.PAGE_SIZE:
                break
        pages = google.search_analytics_by(access, site, start, end, ["page"])
        db.replace_gsc_pages(uid, site, query_pages, pages)

    # -- compte -------------------------------------------------------------------------------
    @app.get("/api/me")
    @authed
    def me():
        usage = db.usage_month(user_id())
        return jsonify(email=g.user["email"], plan=g.profile["plan"], limits=g.limits,
                       usage={k: usage.get(k, 0) for k in ("audit", "competitor", "keywords")},
                       running=db.running_count(user_id()), google=db.google_token(user_id()) is not None)

    @app.post("/api/me/delete")
    @authed
    def delete_account():
        if body().get("confirm") != "SUPPRIMER":
            abort(400, "Confirmation manquante")
        tok = db.google_token(user_id())
        if tok and (rt := crypto.decrypt(settings.token_key, tok["refresh_token_enc"])):
            google.revoke(rt)
        if not settings.supabase_service_role_key:
            abort(503, "Suppression indisponible : SUPABASE_SERVICE_ROLE_KEY non configurée")
        r = httpx.delete(f"{settings.supabase_url}/auth/v1/admin/users/{user_id()}", timeout=20, headers={
            "apikey": settings.supabase_service_role_key,
            "Authorization": f"Bearer {settings.supabase_service_role_key}"})
        if r.status_code >= 300:
            abort(502, "La suppression du compte a échoué")
        return jsonify(deleted=True)  # toutes les tables sont en ON DELETE CASCADE

    # -- audits -------------------------------------------------------------------------------
    @app.get("/api/sites")
    @authed
    def sites():
        return jsonify(db.list_sites(user_id()))

    @app.get("/api/sites/<key>")
    @authed
    def site(key):
        audits = db.site_audits(user_id(), key)
        if not audits:
            abort(404, "Projet introuvable")
        return jsonify(key=key, audits=audits)

    def _start_audit(url: str, kind: str, max_pages: int, parent_site: str | None = None):
        if db.running_count(user_id()) >= g.limits["running"]:
            raise QuotaError(f"{g.limits['running']} audit(s) en cours au maximum avec le plan {g.limits['label']}.")
        usage = db.usage_month(user_id())
        check_monthly(g.profile["plan"], kind, usage.get(kind, 0))
        options = {"max_pages": max_pages, "max_depth": 10, "concurrency": 2 if kind == "competitor" else 5}
        audit_id = db.store.create_audit(user_id(), url, options, kind="competitor" if kind == "competitor" else "site",
                                         parent_site=parent_site)
        db.add_usage(user_id(), kind)
        return audit_id

    @app.post("/api/audits")
    @authed
    def create_audit():
        data = body()
        url = clean_url(data.get("url"))
        max_pages = max(1, min(int(data.get("max_pages") or g.limits["max_pages"]), g.limits["max_pages"]))
        audit_id = _start_audit(url, "audit", max_pages)
        return jsonify(id=audit_id, site=site_key(url)), 201

    @app.get("/api/audits/<audit_id>")
    @authed
    def audit_status(audit_id):
        a = db.audit(user_id(), audit_id)
        if not a:
            abort(404, "Audit introuvable")
        return jsonify(_public_audit(a))

    def _public_audit(a):
        return {"id": str(a["id"]), "site": a["site"], "url": a["url"], "kind": a["kind"], "status": a["status"],
                "phase": a["phase"], "error": a["error"], "progress": {"done": a["pages_done"], "total": a["pages_total"]},
                "score": (a["summary"] or {}).get("health_score")}

    @app.post("/api/audits/<audit_id>/step")
    @authed
    def audit_step(audit_id):
        """Fait avancer un audit pendant que l'utilisateur regarde (en plus du planificateur)."""
        if not db.audit(user_id(), audit_id):
            abort(404, "Audit introuvable")
        asyncio.run(step_audit(db.store, audit_id, budget=min(settings.step_budget, 25), url_guard=guard))
        return jsonify(_public_audit(db.audit(user_id(), audit_id)))

    @app.get("/api/audits/<audit_id>/report")
    @authed
    def audit_report(audit_id):
        rep = db.report(user_id(), audit_id)
        if not rep:
            abort(404, "Rapport introuvable")
        return jsonify(rep)

    @app.get("/api/audits/<audit_id>/report.html")
    @authed
    def audit_report_html(audit_id):
        rep = db.report(user_id(), audit_id)
        if not rep:
            abort(404, "Rapport introuvable")
        payload = {**rep, "labels": {"severity": SEVERITY_LABELS, "category": CATEGORY_LABELS}}
        data = json.dumps(payload, ensure_ascii=False, default=str).replace("</", "<\\/")
        html = TEMPLATE.read_text(encoding="utf-8").replace("__DATA__", data)
        return Response(html, mimetype="text/html", headers={
            "Content-Disposition": f'attachment; filename="rapport-{rep["summary"]["site"].split("//")[-1].strip("/")}.html"'})

    @app.delete("/api/audits/<audit_id>")
    @authed
    def audit_delete(audit_id):
        if not db.audit(user_id(), audit_id) or not db.delete_audit(user_id(), audit_id):
            abort(404, "Audit introuvable")
        return jsonify(deleted=True)

    # -- concurrents ------------------------------------------------------------------------------
    @app.get("/api/competitors/<key>")
    @authed
    def competitors(key):
        return jsonify(db.competitors(user_id(), key))

    @app.post("/api/competitors")
    @authed
    def competitor_add():
        data = body()
        parent = (data.get("site") or "").strip()
        if not db.site_audits(user_id(), parent):
            abort(400, "Projet inconnu : auditez d'abord votre site")
        url = clean_url(data.get("url"))
        if site_key(url) == parent:
            abort(400, "C'est votre propre site")
        audit_id = _start_audit(url, "competitor", g.limits["competitor_pages"], parent_site=parent)
        return jsonify(id=audit_id), 201

    # -- Search Console ---------------------------------------------------------------------------
    def _access() -> str:
        tok = db.google_token(user_id())
        rt = tok and crypto.decrypt(settings.token_key, tok["refresh_token_enc"])
        if not rt:
            raise google.GoogleError("Search Console non connectée", reconnect=True)
        return google.access_token(rt, settings.google_client_id, settings.google_client_secret)

    @app.get("/api/google/status")
    @authed
    def google_status():
        tok = db.google_token(user_id())
        return jsonify(connected=tok is not None, email=tok["email"] if tok else None,
                       properties=db.properties(user_id()), max_properties=g.limits["gsc_properties"])

    @app.post("/api/google/token")
    @authed
    def google_token():
        """Le navigateur transmet le jeton de rafraîchissement reçu de Google à la connexion (une seule fois)."""
        data = body()
        rt = (data.get("refresh_token") or "").strip()
        if not rt or len(rt) > 2048:
            abort(400, "Jeton manquant")
        google.access_token(rt, settings.google_client_id, settings.google_client_secret)  # vérifie qu'il marche
        db.save_google_token(user_id(), crypto.encrypt(settings.token_key, rt), g.user["email"])
        return jsonify(connected=True)

    @app.post("/api/google/disconnect")
    @authed
    def google_disconnect():
        tok = db.google_token(user_id())
        if tok and (rt := crypto.decrypt(settings.token_key, tok["refresh_token_enc"])):
            google.revoke(rt)
        db.delete_google_token(user_id())
        return jsonify(connected=False)

    @app.get("/api/google/sites")
    @authed
    def google_sites():
        return jsonify(google.list_sites(_access()))

    @app.get("/api/positions/summary")
    @authed
    def positions_summary():
        days = max(7, min(int(request.args.get("days", 28)), 180))
        return jsonify(db.positions_summary(user_id(), request.args.get("site", ""), days))

    @app.get("/api/positions/history")
    @authed
    def positions_history():
        return jsonify(db.query_history(user_id(), request.args.get("site", ""), request.args.get("query", "")))

    @app.post("/api/positions/sync")
    @authed
    def positions_sync():
        """Import par tranches de 25 000 lignes : le navigateur rappelle avec `offset` tant que done=false."""
        data = body()
        site = (data.get("site") or "").strip()
        days = max(7, min(int(data.get("days") or 90), 480))
        offset = max(0, int(data.get("offset") or 0))
        known = {p["site"] for p in db.properties(user_id())}
        if site not in known and len(known) >= g.limits["gsc_properties"]:
            raise QuotaError(f"{g.limits['gsc_properties']} propriété(s) suivie(s) au maximum avec le plan "
                             f"{g.limits['label']}.")
        access = _access()
        if site not in {s["site"] for s in google.list_sites(access)}:
            abort(403, "Cette propriété n'est pas accessible avec votre compte Google")
        db.add_property(user_id(), site)
        start, end = google.date_range(days)
        t0 = time.monotonic()
        imported = 0
        while time.monotonic() - t0 < settings.step_budget - 10:
            rows = google.search_analytics(access, site, start, end, offset)
            imported += db.save_gsc_rows(user_id(), site, rows)
            offset += len(rows)
            if len(rows) < google.PAGE_SIZE:
                db.mark_synced(user_id(), site)
                pages = time.monotonic() - t0 < settings.step_budget / 2  # sinon le navigateur appelle /sync-pages
                if pages:
                    try:
                        _sync_pages(user_id(), site, access)
                    except (google.GoogleError, httpx.HTTPError) as exc:  # les positions sont déjà enregistrées
                        log.warning("Analyse des pages %s : %s", site, exc)
                        pages = False
                return jsonify(done=True, imported=imported, offset=offset, pages=pages)
        return jsonify(done=False, imported=imported, offset=offset)

    @app.post("/api/positions/sync-pages")
    @authed
    def positions_sync_pages():
        site = (body().get("site") or "").strip()
        if site not in {p["site"] for p in db.properties(user_id())}:
            abort(404, "Propriété non suivie")
        _sync_pages(user_id(), site, _access())
        return jsonify(done=True)

    @app.get("/api/positions/insights")
    @authed
    def positions_insights():
        site = request.args.get("site", "")
        if site not in {p["site"] for p in db.properties(user_id())}:
            abort(404, "Propriété non suivie")
        queries = db.positions_summary(user_id(), site, 90)["queries"]
        recent = db.positions_summary(user_id(), site, 28)["queries"]
        query_pages, pages = db.gsc_pages_snapshot(user_id(), site)
        host = site.split(":", 1)[1] if site.startswith("sc-domain:") else site
        audit = db.latest_report_for_site(user_id(), site_key(host if "://" in host else "https://" + host))
        return jsonify(analyze(site, queries, query_pages, pages, audit, recent))

    # -- mots-clés -------------------------------------------------------------------------------
    @app.post("/api/keywords")
    @authed
    def keywords():
        data = body()
        seed = " ".join((data.get("seed") or "").split())[:80]
        if len(seed) < 2:
            abort(400, "Mot-clé trop court")
        check_monthly(g.profile["plan"], "keywords", db.usage_month(user_id()).get("keywords", 0))
        ideas = asyncio.run(kw.autocomplete(seed))
        site = data.get("site")
        if site:
            ideas += kw.from_search_console(seed, db.positions_summary(user_id(), site, 90)["queries"])
        merged = {}
        for idea, kind in ideas:
            merged.setdefault(idea, kind)
        db.add_usage(user_id(), "keywords")
        db.save_ideas(user_id(), seed, list(merged.items()))
        return jsonify(db.ideas(user_id(), seed))

    # -- fichiers statiques (développement local ; en production, Vercel sert public/) -------------
    if settings.serve_static:
        @app.get("/")
        def index():
            return send_from_directory(PUBLIC_DIR, "index.html")

        @app.get("/<path:name>")
        def static_files(name):
            return send_from_directory(PUBLIC_DIR, name)

    return app
