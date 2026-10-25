"""API de Vigie SEO (Vercel : api/index.py). Toutes les routes /api/* sauf /api/config et /api/worker/tick
exigent une session Supabase (Authorization: Bearer <access_token>) ; pas de cookie, donc pas de CSRF."""
import asyncio
import hmac
import json
import logging
import os
import time
from datetime import timedelta
from functools import wraps
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from flask import Flask, Response, abort, g, jsonify, redirect, request, send_from_directory

from .. import keywords as kw
from ..engine import run_tick, step_audit
from ..models import CATEGORY_LABELS, SEVERITY_LABELS
from ..ssrf import check_public_url
from ..urls import is_http, normalize_url, site_key
from . import crypto, google
from .dataforseo import LOCATIONS, DataForSEO, DataForSEOError
from .insights import analyze
from .seranking import SOURCES, SERanking, SERankingError
from . import traffic as traffic_mod
from . import fixer
from .github import GitHubApp, GitHubError
from . import emails, notify, tracking
from .plan import impact, tasks_from_audit, tasks_from_insights
from .auth import AuthError, verify_token
from .config import APP_NAME, Settings
from .db import Db
from .plans import PLANS, QuotaError, check_monthly, limits

log = logging.getLogger(__name__)
PUBLIC_DIR = Path(__file__).resolve().parents[2] / "public"
TEMPLATE = Path(__file__).resolve().parents[1] / "report_template.html"


def property_site(prop: str) -> str:
    """Clé de projet d'une propriété Search Console : sc-domain:kompta.pro → kompta.pro."""
    host = prop.split(":", 1)[1] if prop.startswith("sc-domain:") else prop
    return site_key(host if "://" in host else "https://" + host)


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
            if g.profile["mfa"] and g.user["aal"] != "aal2":  # 2FA activée : rien sans le code, même en contournant l'interface
                return jsonify(error="Double authentification requise", mfa_required=True), 401
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
                       plans=PLANS, missing=settings.missing(),
                       version=(os.environ.get("VERCEL_GIT_COMMIT_SHA") or "dev")[:12])

    @app.route("/api/worker/tick", methods=["GET", "POST"])
    def tick():
        """Appelé chaque minute par pg_cron (Supabase) et une fois par jour par Vercel Cron."""
        expected = f"Bearer {settings.cron_secret}"
        if not settings.cron_secret or not hmac.compare_digest(request.headers.get("Authorization", ""), expected):
            return jsonify(error="Non autorisé"), 401
        start = time.monotonic()
        touched = asyncio.run(run_tick(db.store, budget=settings.step_budget * 0.6, url_guard=guard))
        ranks = tracking.run(db, _dfs(), budget=max(5.0, settings.step_budget - (time.monotonic() - start) - 10)) \
            if settings.dataforseo_login else None
        mails = notify.run(db, settings, budget=max(3.0, settings.step_budget - (time.monotonic() - start) - 8))
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
        return jsonify(audits=touched, gsc=synced, ranks=ranks, emails=mails)

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

    # -- GitHub et corrections automatiques ----------------------------------------------------------
    def _gh() -> GitHubApp:
        if not (settings.github_app_id and settings.github_private_key):
            abort(503, "Connexion GitHub pas encore configurée")
        return GitHubApp(settings.github_app_id, settings.github_private_key, settings.github_client_id, settings.github_client_secret)

    def _github_urls(uid: str) -> dict:
        """Adresses de connexion GitHub, signées pour l'utilisateur (le retour ne lie que le compte qui a lancé la connexion)."""
        from urllib.parse import urlencode
        state = emails.sign(settings.token_key, uid, "github")
        return {"connect_url": "https://github.com/login/oauth/authorize?" + urlencode(
                    {"client_id": settings.github_client_id, "redirect_uri": f"{settings.app_url}/api/github/callback", "state": state}),
                "install_url": f"https://github.com/apps/{settings.github_app_slug}/installations/new?" + urlencode({"state": state})}

    @app.get("/api/github/status")
    @authed
    def github_status():
        configured = bool(settings.github_app_id and settings.github_private_key and settings.github_app_slug and settings.github_client_id)
        return jsonify(configured=configured, ai=bool(settings.anthropic_api_key),
                       **(_github_urls(user_id()) if configured else {"connect_url": None, "install_url": None}),
                       installations=db.installations(user_id()), projects=db.project_repos(user_id()))

    @app.get("/api/github/callback")
    def github_callback():
        """Retour de GitHub (identification ou installation) : on transmet le code à l'application, qui le valide connectée."""
        from urllib.parse import urlencode
        q = {k: request.args.get(k, "") for k in ("code", "installation_id", "state")}
        if not q["code"] and settings.github_client_id:
            # mise à jour d'une installation existante : GitHub n'envoie pas toujours de code, on demande l'identification
            return redirect("https://github.com/login/oauth/authorize?" + urlencode(
                {"client_id": settings.github_client_id, "redirect_uri": f"{settings.app_url}/api/github/callback", "state": q["state"]}))
        return redirect("/#/connexions?" + urlencode({"github_code": q["code"], "state": q["state"]}))

    @app.post("/api/github/link")
    @authed
    def github_link():
        """Lie à l'utilisateur les installations auxquelles son compte GitHub a réellement accès (vérifié chez GitHub).
        Aucune installation : l'application renvoie vers la page d'installation de l'app GitHub."""
        data = body()
        code = (data.get("code") or "").strip()
        if not code:
            abort(400, "Code GitHub manquant")
        if not emails.verify(settings.token_key, user_id(), "github", data.get("state") or ""):
            abort(400, "Lien de connexion GitHub invalide, recommencez depuis Vigie SEO")
        try:
            found = _gh().user_installations(code)
        except GitHubError as exc:
            abort(502, str(exc))
        for inst in found:
            db.save_installation(user_id(), inst["id"], inst["account"])
        return jsonify(installations=db.installations(user_id()), **_github_urls(user_id()))

    @app.post("/api/github/disconnect")
    @authed
    def github_disconnect():
        db.delete_installations(user_id())
        return jsonify(disconnected=True)

    @app.get("/api/github/repos")
    @authed
    def github_repos():
        gh, out = _gh(), []
        try:
            for inst in db.installations(user_id()):
                out += [{**r, "installation_id": inst["installation_id"]} for r in gh.repositories(inst["installation_id"])]
        except GitHubError as exc:
            abort(502, str(exc))
        return jsonify(repos=out, projects=db.project_repos(user_id()), sites=[s["key"] for s in db.list_sites(user_id())])

    @app.post("/api/github/project")
    @authed
    def github_project():
        data = body()
        site, repo = (data.get("site") or "").strip(), (data.get("repo") or "").strip()
        if not site:
            abort(400, "Projet manquant")
        if not repo:
            db.set_project_repo(user_id(), site, 0, None)
            return jsonify(linked=False)
        gh = _gh()
        for inst in db.installations(user_id()):  # le dépôt doit être accessible par une installation de l'utilisateur
            try:
                repos = {r["full_name"] for r in gh.repositories(inst["installation_id"])}
            except GitHubError:
                continue
            if repo in repos:
                db.set_project_repo(user_id(), site, inst["installation_id"], repo)
                return jsonify(linked=True)
        abort(403, "Ce dépôt n'est pas accessible avec vos installations GitHub")

    @app.post("/api/fixes")
    @authed
    def fix_create():
        """Prépare la correction d'une tâche et ouvre la pull request (dans la même requête, moins d'une minute)."""
        if not settings.anthropic_api_key:
            abort(503, "Rédaction automatique pas encore configurée")
        task = db.task(user_id(), body().get("task_id", ""))
        if not task:
            abort(404, "Tâche introuvable")
        if not fixer.fixable(task["fingerprint"]):
            abort(400, "Cette tâche ne peut pas encore être corrigée automatiquement")
        link = db.project_repo(user_id(), task["site"])
        if not link:
            abort(409, "Associez d'abord un dépôt GitHub à ce projet (page Connexions)")
        check_monthly(g.profile["plan"], "fix", db.usage_month(user_id()).get("fix", 0))
        fix_id = db.create_fix(user_id(), str(task["id"]), task["site"], link["repo"])
        db.add_usage(user_id(), "fix")
        try:
            res = fixer.run_fix(db, settings, user_id(), {**task, "id": str(task["id"])}, link, _gh())
        except (fixer.FixError, GitHubError) as exc:
            db.finish_fix(fix_id, "failed", error=str(exc)[:500])
            return jsonify(id=fix_id, status="failed", error=str(exc)), 200
        except Exception as exc:  # erreur imprévue : on la consigne sans exposer de détail technique
            log.exception("Correction automatique %s", fix_id)
            db.finish_fix(fix_id, "failed", error="Erreur inattendue")
            return jsonify(id=fix_id, status="failed", error="Erreur inattendue, réessayez"), 200
        db.finish_fix(fix_id, res["status"], **{k: v for k, v in res.items() if k != "status"})
        return jsonify(id=fix_id, **res)

    def _fix_repo(fix: dict):
        """Dépôt d'une correction, avec un jeton d'une installation de l'utilisateur qui y a accès."""
        from .github import Repo
        gh = _gh()
        link = db.project_repo(user_id(), fix["site"])
        insts = [link["installation_id"]] if link and link["repo"] == fix["repo"] else []
        insts += [i["installation_id"] for i in db.installations(user_id()) if i["installation_id"] not in insts]
        for inst in insts:
            try:
                if any(r["full_name"] == fix["repo"] for r in gh.repositories(inst)):
                    return Repo(gh.installation_token(inst), fix["repo"])
            except GitHubError:
                continue
        abort(403, "Ce dépôt n'est plus accessible avec vos installations GitHub")

    def _own_fix(fix_id: str) -> dict:
        fix = db.fix(user_id(), fix_id)
        if not fix or fix["status"] != "done" or not fix["pr_number"]:
            abort(404, "Correction introuvable")
        return fix

    @app.get("/api/fixes/<fix_id>")
    @authed
    def fix_detail(fix_id):
        """Relecture : modifications de la pull request et son état chez GitHub (fusionnée ailleurs, fermée…)."""
        fix = _own_fix(fix_id)
        repo = _fix_repo(fix)
        try:
            pr, files = repo.pull(fix["pr_number"]), repo.pull_files(fix["pr_number"])
        except GitHubError as exc:
            abort(502, str(exc))
        if pr["merged"] or pr["state"] == "closed":
            db.mark_fix(fix_id, merged=pr["merged"], closed=not pr["merged"])
        return jsonify(id=fix_id, summary=fix["summary"], pr=pr, files=files)

    @app.post("/api/fixes/<fix_id>/merge")
    @authed
    def fix_merge(fix_id):
        """Validation par l'utilisateur : fusion de la pull request, la tâche passe en « faite » (mesure de l'effet)."""
        fix = _own_fix(fix_id)
        repo, sha = _fix_repo(fix), None
        try:
            pr = repo.pull(fix["pr_number"])
            if not pr["merged"]:
                if pr["state"] != "open":
                    abort(409, "Cette pull request a été fermée sur GitHub")
                sha = repo.merge(fix["pr_number"], pr["title"])
        except GitHubError as exc:
            if " 405 " in str(exc) or " 409 " in str(exc):
                abort(409, "GitHub refuse la fusion (conflit avec d'autres modifications ou vérifications en échec). "
                           "Ouvrez la pull request sur GitHub pour voir le détail.")
            abort(502, str(exc))
        db.mark_fix(fix_id, merged=True)
        db.set_task_status(user_id(), str(fix["task_id"]), "done")
        try:
            repo.delete_branch(fix["branch"])
        except GitHubError:
            pass
        return jsonify(merged=True, commit=sha and sha[:7], commit_url=sha and f"https://github.com/{fix['repo']}/commit/{sha}",
                       base=pr["base"], repo=fix["repo"])

    @app.post("/api/fixes/<fix_id>/close")
    @authed
    def fix_close(fix_id):
        """Refus : la pull request est fermée, rien n'est modifié sur le site ; la tâche reste à faire."""
        fix = _own_fix(fix_id)
        repo = _fix_repo(fix)
        try:
            if repo.pull(fix["pr_number"])["state"] == "open":
                repo.close_pr(fix["pr_number"])
            repo.delete_branch(fix["branch"])
        except GitHubError as exc:
            if " 404 " not in str(exc) and " 422 " not in str(exc):
                abort(502, str(exc))
        db.mark_fix(fix_id, closed=True)
        return jsonify(closed=True)

    # -- e-mails -------------------------------------------------------------------------------------
    @app.get("/api/notifications")
    @authed
    def notifications():
        return jsonify(**db.notification_settings(user_id()), email=g.user["email"], enabled=emails.enabled(settings),
                       test_mode=not settings.smtp_host and "resend.dev" in settings.email_from, sender=settings.email_from)

    @app.post("/api/notifications")
    @authed
    def notifications_update():
        data = body()
        values = {k: bool(data[k]) for k in ("weekly_digest", "alerts") if k in data}
        if values:
            db.set_notification_settings(user_id(), **values)
        return jsonify(**db.notification_settings(user_id()))

    @app.post("/api/notifications/test")
    @authed
    def notifications_test():
        """Envoie tout de suite le résumé hebdomadaire à l'utilisateur (3 fois par heure au plus)."""
        if not emails.enabled(settings):
            abort(503, "E-mails non configurés")
        with db.conn() as c:
            recent = c.execute("""SELECT count(*) n FROM usage_events WHERE user_id=%s AND kind='email_test'
                                  AND created_at > now() - interval '1 hour'""", (user_id(),)).fetchone()["n"]
        if recent >= 3:
            abort(429, "Trois e-mails de test par heure au maximum")
        data = notify.build_digest(db, user_id()) or {"projects": [], "gsc": [], "tracking": [], "tasks": []}
        name = (g.user.get("name") or "").split(" ")[0] or (g.user["email"] or "").split("@")[0]
        unsub = emails.unsubscribe_url(settings.app_url, settings.token_key, user_id(), "digest")
        subject, html, text = emails.digest(settings.app_url, name, data, unsub)
        try:
            emails.send(settings, g.user["email"], "[Test] " + subject, html, text, unsub)
        except emails.EmailError as exc:
            abort(502, f"Envoi refusé : {exc}")
        db.add_usage(user_id(), "email_test")
        return jsonify(sent=True, to=g.user["email"])

    @app.route("/api/unsubscribe", methods=["GET", "POST"])
    def unsubscribe():
        """Lien de désinscription des e-mails (sans connexion) ; POST pour le désabonnement en un clic des messageries."""
        uid, kind, token = request.args.get("u", ""), request.args.get("k", ""), request.args.get("t", "")
        if kind not in ("digest", "alerts") or not emails.verify(settings.token_key, uid, kind, token):
            return Response("Lien invalide.", status=400, mimetype="text/plain")
        db.set_notification_settings(uid, **({"weekly_digest": False} if kind == "digest" else {"alerts": False}))
        label = "le résumé hebdomadaire" if kind == "digest" else "les alertes"
        return Response(f"""<!doctype html><html lang="fr"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Désinscription</title><body style="font-family:system-ui,sans-serif;background:#faf4f2;display:grid;place-items:center;min-height:90vh;margin:0">
<div style="background:#fff;border-radius:24px;padding:32px;max-width:420px;text-align:center"><h1 style="font-size:22px">C'est noté</h1>
<p>Vous ne recevrez plus {label} de Vigie SEO.</p><p><a href="/#/compte">Gérer mes e-mails</a></p></div></body></html>""", mimetype="text/html")

    # -- compte -------------------------------------------------------------------------------
    @app.get("/api/me")
    @authed
    def me():
        usage = db.usage_month(user_id())
        return jsonify(email=g.user["email"], plan=g.profile["plan"], limits=g.limits, mfa=g.profile["mfa"],
                       usage={k: usage.get(k, 0) for k in ("audit", "competitor", "keywords", "domain", "fix")},
                       running=db.running_count(user_id()), google=db.google_token(user_id()) is not None,
                       autofix=bool(settings.anthropic_api_key and settings.github_app_id), repos=db.project_repos(user_id()))

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
    def _connections(uid: str) -> dict:
        """Outils branchés sur chaque projet : dépôt GitHub, Search Console, Google Analytics."""
        out: dict = {}
        for site, repo in db.project_repos(uid).items():
            out.setdefault(site, {})["github"] = repo
        for p in db.properties(uid):
            out.setdefault(property_site(p["site"]), {})["gsc"] = True
        for link in db.ga4_links(uid):
            if link.get("site"):
                out.setdefault(link["site"], {})["ga4"] = True
        return out

    @app.get("/api/sites")
    @authed
    def sites():
        conns = _connections(user_id())
        return jsonify([{**s, "connections": conns.get(s["key"], {})} for s in db.list_sites(user_id())])

    @app.get("/api/sites/<key>")
    @authed
    def site(key):
        audits = db.site_audits(user_id(), key)
        if not audits:
            abort(404, "Projet introuvable")
        return jsonify(key=key, audits=audits, connections=_connections(user_id()).get(key, {}))

    @app.delete("/api/sites/<key>")
    @authed
    def site_delete(key):
        if not db.delete_project(user_id(), key):
            abort(404, "Projet introuvable")
        return jsonify(deleted=True)

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
        scopes = (tok or {}).get("scopes", "")
        return jsonify(connected=tok is not None, email=tok["email"] if tok else None,
                       gsc=google.SCOPE in scopes, ga4=google.GA4_SCOPE in scopes,
                       properties=db.properties(user_id()), max_properties=g.limits["gsc_properties"])

    # Connexion Search Console indépendante de l'identifiant Vigie SEO : OAuth Google côté serveur.
    def _google_redirect_uri() -> str:
        return settings.app_url + "/api/google/callback"

    def _sign_state(uid: str) -> str:
        payload = f"{uid}.{int(time.time()) + 900}.{os.urandom(6).hex()}"
        return payload + "." + emails.sign(settings.token_key, payload, "google-oauth")

    def _read_state(state: str) -> str | None:
        try:
            uid, exp, nonce, mac = state.split(".")
        except ValueError:
            return None
        if int(exp) < time.time() or not emails.verify(settings.token_key, f"{uid}.{exp}.{nonce}", "google-oauth", mac):
            return None
        return uid

    @app.get("/api/google/connect-url")
    @authed
    def google_connect_url():
        if not settings.google_client_id:
            abort(503, "Connexion Google non configurée")
        wanted = [google.SCOPES[t] for t in (request.args.get("tools") or "gsc").split(",") if t in google.SCOPES]
        return jsonify(url=google.auth_url(settings.google_client_id, _google_redirect_uri(), _sign_state(user_id()),
                                           request.args.get("hint", ""), wanted or [google.SCOPE]))

    @app.get("/api/google/callback")
    def google_callback():
        """Retour de Google après autorisation ; l'identité est portée par le paramètre state signé (15 minutes)."""
        uid = _read_state(request.args.get("state", ""))
        if not uid:
            return redirect("/#/connexions?google=expire")
        if request.args.get("error"):
            return redirect("/#/connexions?google=refuse")
        try:
            res = google.exchange_code(request.args.get("code", ""), settings.google_client_id,
                                       settings.google_client_secret, _google_redirect_uri())
        except google.GoogleError as exc:
            log.warning("Connexion Search Console : %s", exc)
            return redirect("/#/connexions?google=erreur")
        previous = (db.google_token(uid) or {}).get("scopes", "")
        scopes = " ".join(sorted(set(previous.split()) | set(res.get("scope", "").split())))
        db.save_google_token(uid, crypto.encrypt(settings.token_key, res["refresh_token"]), res["email"], scopes)
        return redirect("/#/connexions?google=ok")

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

    # -- Google Analytics 4 -------------------------------------------------------------------------
    def _ga4_access() -> str:
        tok = db.google_token(user_id())
        if not tok or google.GA4_SCOPE not in tok.get("scopes", ""):
            raise google.GoogleError("Google Analytics n'est pas connecté", reconnect=True)
        return _access()

    @app.get("/api/ga4/properties")
    @authed
    def ga4_properties():
        links = {l["property_id"]: l for l in db.ga4_links(user_id())}
        props = google.ga4_properties(_ga4_access())
        return jsonify(properties=[{**p, "site": (links.get(p["property_id"]) or {}).get("site")} for p in props],
                       projects=[s["key"] for s in db.list_sites(user_id())])

    @app.post("/api/ga4/link")
    @authed
    def ga4_link():
        data = body()
        prop = (data.get("property_id") or "").strip()
        if not prop.startswith("properties/") or not prop.split("/")[1].isdigit():
            abort(400, "Propriété GA4 invalide")
        site = (data.get("site") or "").strip() or None
        db.link_ga4(user_id(), prop, (data.get("display_name") or "")[:200], site)
        return jsonify(linked=True)

    @app.get("/api/ga4/traffic")
    @authed
    def ga4_traffic():
        prop = request.args.get("property", "")
        if not prop.startswith("properties/") or not prop.split("/")[1].isdigit():
            abort(400, "Propriété GA4 invalide")
        days = max(7, min(int(request.args.get("days", 28)), 90))
        access = _ga4_access()
        key = f"ga4|{user_id()}|{prop}|{days}|{time.strftime('%Y-%m-%d')}"
        raw = None if request.args.get("refresh") else db.cache_get(key, 1)
        if raw is None:
            raw = google.ga4_traffic(access, prop, days)
            db.cache_put(key, raw)
        link = next((l for l in db.ga4_links(user_id()) if l["property_id"] == prop), None)
        site = link and link["site"]
        gsc_pages, audit = [], None
        if site:
            audit = db.latest_report_for_site(user_id(), site)
            for p in db.properties(user_id()):
                if property_site(p["site"]) == site:
                    gsc_pages += db.gsc_pages_snapshot(user_id(), p["site"])[1]
        return jsonify(property=prop, site=site, **traffic_mod.build(raw, gsc_pages, audit))

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

    @app.post("/api/positions/remove")
    @authed
    def positions_remove():
        """Ne plus suivre une propriété Search Console et effacer ses données importées."""
        if not db.delete_property(user_id(), (body().get("site") or "").strip()):
            abort(404, "Propriété non suivie")
        return jsonify(deleted=True)

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
        audit = db.latest_report_for_site(user_id(), property_site(site))
        return jsonify(analyze(site, queries, query_pages, pages, audit, recent))

    # -- plan d'action --------------------------------------------------------------------------
    def _analysis(prop: str, site: str, audit) -> dict:
        query_pages, pages = db.gsc_pages_snapshot(user_id(), prop)
        return analyze(prop, db.positions_summary(user_id(), prop, 90)["queries"], query_pages, pages, audit,
                       db.positions_summary(user_id(), prop, 28)["queries"])

    @app.get("/api/plan")
    @authed
    def plan_overview():
        return jsonify(db.open_task_counts(user_id()))

    @app.get("/api/plan/<site>")
    @authed
    def plan(site):
        """Met à jour le plan (nouvelles recommandations, problèmes d'audit corrigés) puis le renvoie."""
        report = db.latest_report_for_site(user_id(), site)
        counts = {}
        if report:
            audit_tasks = tasks_from_audit(report)
            counts = {t["fingerprint"]: t["baseline"]["count"] for t in audit_tasks}
            db.upsert_tasks(user_id(), site, audit_tasks)
            db.sync_audit_tasks(user_id(), site, counts, report["finished_at"])
        props = [p["site"] for p in db.properties(user_id()) if property_site(p["site"]) == site]
        for prop in props:
            gsc_tasks = tasks_from_insights(_analysis(prop, site, report))
            for t in gsc_tasks:
                t["baseline"]["property"] = prop
            db.upsert_tasks(user_id(), site, gsc_tasks)
        tasks = db.list_tasks(user_id(), site)
        last_dates = {p: db.gsc_last_date(user_id(), p) for p in props}
        out = []
        for t in tasks:
            item = {**t, "id": str(t["id"]), "impact": None,
                    "created_at": t["created_at"].isoformat(), "done_at": t["done_at"].isoformat() if t["done_at"] else None}
            if t["status"] == "done" and t["source"] == "audit":
                item["impact"] = {"count_before": t["baseline"].get("count"), "count_now": counts.get(t["fingerprint"], 0),
                                  "resolved_by_audit": t["resolved_by"] == "audit"}
            elif t["status"] == "done" and t["queries"] and t["baseline"].get("property") in last_dates:
                prop, last = t["baseline"]["property"], last_dates[t["baseline"]["property"]]
                done = t["done_at"].date()
                if last and last > done:
                    before = db.gsc_window(user_id(), prop, t["queries"], done - timedelta(days=28), done - timedelta(days=1))
                    after = db.gsc_window(user_id(), prop, t["queries"], done + timedelta(days=1),
                                          min(done + timedelta(days=28), last))
                    item["impact"] = impact(before, after, (last - done).days)
            out.append(item)
        fixes = db.latest_fixes(user_id(), site)
        for item in out:
            item["fixable"] = bool(fixer.fixable(item["fingerprint"]))
            item["fix"] = fixes.get(item["id"])
        return jsonify(site=site, properties=props, audit_date=report["finished_at"].isoformat() if report else None,
                       tasks=out, repo=db.project_repos(user_id()).get(site),
                       autofix=bool(settings.anthropic_api_key and settings.github_app_id))

    @app.post("/api/plan/tasks/<task_id>")
    @authed
    def plan_task(task_id):
        status = body().get("status")
        if status not in ("todo", "done", "ignored"):
            abort(400, "Statut invalide")
        if not db.set_task_status(user_id(), task_id, status):
            abort(404, "Tâche introuvable")
        return jsonify(status=status)

    # -- mots-clés -------------------------------------------------------------------------------
    def _dfs() -> DataForSEO:
        return DataForSEO(settings.dataforseo_login, settings.dataforseo_password)

    # -- suivi de positions -------------------------------------------------------------------------
    def _domain(raw: str) -> str:
        raw = (raw or "").strip().lower()
        host = urlsplit(raw if "://" in raw else "https://" + raw).hostname or ""
        if "." not in host or len(host) > 253:
            abort(400, "Domaine invalide")
        return site_key("https://" + host)

    @app.get("/api/tracking")
    @authed
    def tracking_sites():
        return jsonify(sites=db.rank_sites(user_id()), projects=[s["key"] for s in db.list_sites(user_id())],
                       used=db.rank_count(user_id()), limit=g.limits["tracked_keywords"],
                       enabled=bool(settings.dataforseo_login), locations=LOCATIONS)

    @app.get("/api/tracking/<site>")
    @authed
    def tracking_overview(site):
        site = _domain(site)
        competitors = db.rank_competitors(user_id(), site)
        data = tracking.overview(site, db.rank_keywords(user_id(), site), db.rank_history(user_id(), site, 30),
                                 db.rank_latest(user_id(), site), db.rank_latest(user_id(), site, 7), competitors)
        return jsonify({**data, "used": db.rank_count(user_id()), "limit": g.limits["tracked_keywords"]})

    @app.post("/api/tracking/<site>/keywords")
    @authed
    def tracking_add(site):
        if not settings.dataforseo_login:
            abort(503, "Suivi de positions indisponible : service de données non configuré")
        site, data = _domain(site), body()
        location, device = int(data.get("location") or 2250), data.get("device") or "desktop"
        if location not in LOCATIONS or device not in ("desktop", "mobile"):
            abort(400, "Pays ou appareil invalide")
        raw = data.get("keywords") or []
        raw = raw.splitlines() if isinstance(raw, str) else raw
        keywords = list(dict.fromkeys(" ".join(str(k).lower().split())[:80] for k in raw if str(k).strip()))[:200]
        if not keywords:
            abort(400, "Aucun mot-clé")
        room = g.limits["tracked_keywords"] - db.rank_count(user_id())
        if len(keywords) > room:
            raise QuotaError(f"Votre offre {g.limits['label']} permet de suivre {g.limits['tracked_keywords']} mots-clés "
                             f"({max(room, 0)} restant(s)).")
        volumes = {}
        try:
            volumes = _dfs().keyword_volumes(keywords, location)
        except DataForSEOError as exc:
            log.warning("Volumes des mots-clés suivis : %s", exc)
        added = db.add_rank_keywords(user_id(), site, keywords, location, device, volumes)
        stats = tracking.run(db, _dfs(), budget=8)  # lance tout de suite les relevés du jour
        return jsonify(added=added, stats=stats), 201

    @app.delete("/api/tracking/<site>")
    @authed
    def tracking_site_delete(site):
        if not db.delete_rank_site(user_id(), _domain(site)):
            abort(404, "Aucun mot-clé suivi pour ce domaine")
        return jsonify(deleted=True)

    @app.delete("/api/tracking/keywords/<keyword_id>")
    @authed
    def tracking_delete(keyword_id):
        if not db.delete_rank_keyword(user_id(), keyword_id):
            abort(404, "Mot-clé introuvable")
        return jsonify(deleted=True)

    @app.get("/api/tracking/keywords/<keyword_id>")
    @authed
    def tracking_keyword(keyword_id):
        k = db.rank_keyword(user_id(), keyword_id)
        if not k:
            abort(404, "Mot-clé introuvable")
        domains = [k["site"]] + db.rank_competitors(user_id(), k["site"])
        checks = db.rank_keyword_checks(keyword_id)
        series = {d: [[c["date"].isoformat(), (c["ranks"].get(d) or [None])[0]] for c in checks] for d in domains}
        top = None
        if checks:
            serp = db.cache_get(tracking.serp_key(k["keyword"], k["location_code"], k["device"], checks[-1]["date"]), 40)
            top = serp["top"] if serp else None
        return jsonify(keyword=k["keyword"], site=k["site"], location=k["location_code"], device=k["device"],
                       volume=k["volume"], series=series, top=top)

    @app.post("/api/tracking/<site>/competitors")
    @authed
    def tracking_competitor_add(site):
        site, domain = _domain(site), _domain(body().get("domain"))
        if domain == site:
            abort(400, "C'est votre propre site")
        if len(db.rank_competitors(user_id(), site)) >= 10:
            abort(400, "10 concurrents au maximum par site")
        db.add_rank_competitor(user_id(), site, domain)
        return jsonify(added=domain), 201

    @app.delete("/api/tracking/<site>/competitors/<domain>")
    @authed
    def tracking_competitor_delete(site, domain):
        db.delete_rank_competitor(user_id(), _domain(site), _domain(domain))
        return jsonify(deleted=True)

    # -- analyse de domaines (SE Ranking) ---------------------------------------------------------
    def _ser_cached(kind: str, key: str, fetch, days: int = 7):
        cache_key = f"ser|{kind}|{key}"
        data = db.cache_get(cache_key, days)
        if data is not None:
            return data, True
        data = fetch()
        db.cache_put(cache_key, data)
        return data, False

    def _domain_params():
        if not settings.seranking_api_key:
            abort(503, "Analyse de domaines indisponible : service de données non configuré")
        location = int(request.args.get("location") or 2250)
        if location not in SOURCES:
            abort(400, "Pays non pris en charge")
        return SERanking(settings.seranking_api_key), SOURCES[location], location

    @app.get("/api/domain")
    @authed
    def domain_analysis():
        client, source, location = _domain_params()
        domain = _domain(request.args.get("domain"))
        try:
            parts, fresh = {}, False
            for kind, fetch in (("overview", lambda: client.overview(domain, source)),
                                ("keywords", lambda: client.keywords(domain, source)),
                                ("competitors", lambda: client.competitors(domain, source)),
                                ("backlinks", lambda: client.backlinks(domain))):
                key = f"{domain}" if kind == "backlinks" else f"{source}|{domain}"
                if not fresh and db.cache_get(f"ser|{kind}|{key}", 7) is None:
                    # une analyse non encore en cache consomme le quota mensuel
                    check_monthly(g.profile["plan"], "domain", db.usage_month(user_id()).get("domain", 0))
                    fresh = True
                parts[kind], _ = _ser_cached(kind, key, fetch)
        except SERankingError as exc:
            abort(502, str(exc))
        if fresh:
            db.add_usage(user_id(), "domain")
        return jsonify(domain=domain, location=location, cached=not fresh, **parts)

    @app.get("/api/domain/gap")
    @authed
    def domain_gap():
        client, source, location = _domain_params()
        competitor, mine = _domain(request.args.get("competitor")), _domain(request.args.get("mine"))
        key = f"{source}|{competitor}|{mine}"
        try:
            if db.cache_get(f"ser|gap|{key}", 7) is None:
                check_monthly(g.profile["plan"], "domain", db.usage_month(user_id()).get("domain", 0))
                db.add_usage(user_id(), "domain")
            rows, cached = _ser_cached("gap", key, lambda: client.gap(competitor, mine, source))
        except SERankingError as exc:
            abort(502, str(exc))
        return jsonify(competitor=competitor, mine=mine, location=location, cached=cached, keywords=rows)

    def _market(kind: str, seed: str, location: int) -> tuple[dict, bool]:
        """Données DataForSEO, servies depuis le cache partagé (30 jours) quand c'est possible."""
        key = f"{kind}|{location}|fr|{seed.lower()}"
        cached = db.cache_get(key)
        if cached is not None:
            return cached, True
        data = getattr(_dfs(), kind)(seed, location)
        db.cache_put(key, data)
        return data, False

    @app.get("/api/keywords/config")
    @authed
    def keywords_config():
        return jsonify(market=bool(settings.dataforseo_login), locations=LOCATIONS)

    @app.post("/api/keywords")
    @authed
    def keywords():
        data = body()
        seed = " ".join((data.get("seed") or "").split())[:80]
        if len(seed) < 2:
            abort(400, "Mot-clé trop court")
        location = int(data.get("location") or 2250)
        if location not in LOCATIONS:
            abort(400, "Pays non pris en charge")
        check_monthly(g.profile["plan"], "keywords", db.usage_month(user_id()).get("keywords", 0))

        # Idées gratuites : autocomplétion Google et vraies requêtes Search Console
        ideas = asyncio.run(kw.autocomplete(seed))
        site = data.get("site")
        gsc_queries = db.positions_summary(user_id(), site, 90)["queries"] if site else []
        ideas += kw.from_search_console(seed, gsc_queries)
        merged = {}
        for idea, kind in ideas:
            merged.setdefault(idea, kind)

        # Données de marché (DataForSEO) : suggestions + mots-clés associés, dédoublonnés
        market = None
        if settings.dataforseo_login:
            try:
                sugg, cached1 = _market("keyword_suggestions", seed, location)
                rel, cached2 = _market("related_keywords", seed, location)
                items, seen = [], set()
                for item in sugg["items"] + rel["items"]:
                    k = (item.get("keyword") or "").lower()
                    if k and k not in seen:
                        seen.add(k)
                        items.append(item)
                market = {"total": sugg["total"] + rel["total"], "items": items, "cached": cached1 and cached2}
            except DataForSEOError as exc:
                market = {"error": str(exc), "items": []}

        positions = {q["query"].lower(): q["position"] for q in gsc_queries}
        volumes = {}
        for item in (market or {}).get("items", []):
            item["position"] = positions.get(item["keyword"].lower())
            volumes[item["keyword"].lower()] = item["volume"]
        db.add_usage(user_id(), "keywords")
        db.save_ideas(user_id(), seed, list(merged.items()))
        return jsonify(seed=seed, location=location, market=market,
                       ideas=[{"idea": i, "kind": k, "volume": volumes.get(i.lower()),
                               "position": positions.get(i.lower())} for i, k in sorted(merged.items())])

    # -- fichiers statiques (développement local ; en production, Vercel sert public/) -------------
    if settings.serve_static:
        @app.get("/")
        def index():
            return send_from_directory(PUBLIC_DIR, "index.html")

        @app.get("/<path:name>")
        def static_files(name):
            return send_from_directory(PUBLIC_DIR, name)

    return app
