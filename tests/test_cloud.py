import time
import uuid

import jwt
import psycopg
import pytest
from cryptography.fernet import Fernet

from seo_audit.cloud import app as cloud_app
from seo_audit.cloud import google
from seo_audit.cloud.config import Settings

from .site_de_test import start_server

SUPA = "https://projet.supabase.co"
SECRET = "secret-de-test-assez-long-pour-hs256-0123456789"


@pytest.fixture(scope="module")
def site():
    server, base = start_server()
    yield base
    server.shutdown()


def settings(pg_dsn, **kw):
    base = dict(database_url=pg_dsn, supabase_url=SUPA, supabase_anon_key="anon", supabase_jwt_secret=SECRET,
                token_key=Fernet.generate_key().decode(), cron_secret="cron", google_client_id="cid",
                google_client_secret="csec", step_budget=15, allow_private_urls=True)
    base.update(kw)
    return Settings(**base)


@pytest.fixture()
def client(pg_dsn):
    return cloud_app.create_app(settings(pg_dsn)).test_client()


def new_user(pg_dsn, plan=None):
    uid = str(uuid.uuid4())
    with psycopg.connect(pg_dsn) as c:
        c.execute("insert into auth.users values (%s, %s)", (uid, f"{uid[:8]}@exemple.fr"))
        if plan:
            c.execute("update profiles set plan=%s where user_id=%s", (plan, uid))
    token = jwt.encode({"sub": uid, "email": f"{uid[:8]}@exemple.fr", "aud": "authenticated",
                        "iss": f"{SUPA}/auth/v1", "exp": int(time.time()) + 3600}, SECRET, algorithm="HS256")
    return uid, {"Authorization": f"Bearer {token}"}


def run_audit(client, headers, url, path="/api/audits", body=None):
    r = client.post(path, json=body or {"url": url}, headers=headers)
    assert r.status_code == 201, r.get_json()
    audit_id = r.get_json()["id"]
    for _ in range(100):
        st = client.post(f"/api/audits/{audit_id}/step", headers=headers).get_json()
        if st["status"] in ("done", "failed"):
            return audit_id, st
    raise AssertionError("audit non terminé")


def test_auth_required_and_tokens_checked(client, pg_dsn):
    assert client.get("/api/config").status_code == 200
    assert client.get("/api/sites").status_code == 401
    bad = jwt.encode({"sub": "x", "aud": "authenticated", "iss": f"{SUPA}/auth/v1", "exp": int(time.time()) + 60},
                     "autre-secret-tout-aussi-long-0123456789abcdef", algorithm="HS256")
    assert client.get("/api/sites", headers={"Authorization": f"Bearer {bad}"}).status_code == 401
    expired = jwt.encode({"sub": "x", "aud": "authenticated", "iss": f"{SUPA}/auth/v1", "exp": 1}, SECRET, algorithm="HS256")
    assert client.get("/api/sites", headers={"Authorization": f"Bearer {expired}"}).status_code == 401
    other_project = jwt.encode({"sub": "x", "aud": "authenticated", "iss": "https://autre.supabase.co/auth/v1",
                                "exp": int(time.time()) + 60}, SECRET, algorithm="HS256")
    assert client.get("/api/sites", headers={"Authorization": f"Bearer {other_project}"}).status_code == 401


def test_full_audit_and_isolation(client, pg_dsn, site):
    _, alice = new_user(pg_dsn)
    _, bob = new_user(pg_dsn)
    audit_id, st = run_audit(client, alice, site + "/")
    assert st["status"] == "done" and st["score"] is not None
    sites = client.get("/api/sites", headers=alice).get_json()
    assert sites[0]["latest"]["id"] == audit_id
    rep = client.get(f"/api/audits/{audit_id}/report", headers=alice).get_json()
    assert any(i["code"] == "missing_title" for i in rep["issues"])
    html = client.get(f"/api/audits/{audit_id}/report.html", headers=alice)
    assert html.status_code == 200 and b"__DATA__" not in html.data
    # Bob ne voit rien d'Alice
    assert client.get("/api/sites", headers=bob).get_json() == []
    for path in (f"/api/audits/{audit_id}", f"/api/audits/{audit_id}/report", f"/api/audits/{audit_id}/report.html"):
        assert client.get(path, headers=bob).status_code == 404
    assert client.post(f"/api/audits/{audit_id}/step", headers=bob).status_code == 404
    assert client.delete(f"/api/audits/{audit_id}", headers=bob).status_code == 404
    assert client.get("/api/audits/pas-un-uuid", headers=alice).status_code == 404
    assert client.delete(f"/api/audits/{audit_id}", headers=alice).status_code == 200


def test_ssrf_blocked_in_production(pg_dsn):
    client = cloud_app.create_app(settings(pg_dsn, allow_private_urls=False)).test_client()
    _, h = new_user(pg_dsn)
    for url in ("http://127.0.0.1:8080/", "http://169.254.169.254/latest/meta-data", "localhost", "file:///etc/passwd"):
        assert client.post("/api/audits", json={"url": url}, headers=h).status_code == 400, url


def test_quotas(client, pg_dsn, site):
    uid, h = new_user(pg_dsn)
    assert client.post("/api/audits", json={"url": site + "/"}, headers=h).status_code == 201
    r = client.post("/api/audits", json={"url": site + "/"}, headers=h)  # 1 audit en cours max en gratuit
    assert r.status_code == 402 and r.get_json()["quota"]
    with psycopg.connect(pg_dsn) as c:
        c.execute("update audits set status='done' where user_id=%s", (uid,))
        c.execute("insert into usage_events (user_id, kind) select %s, 'audit' from generate_series(1, 4)", (uid,))
    assert client.post("/api/audits", json={"url": site + "/"}, headers=h).status_code == 402  # 5 par mois
    me = client.get("/api/me", headers=h).get_json()
    assert me["plan"] == "gratuit" and me["usage"]["audit"] == 5
    # le nombre de pages est plafonné par le plan
    uid2, h2 = new_user(pg_dsn)
    client.post("/api/audits", json={"url": site + "/", "max_pages": 99999}, headers=h2)
    with psycopg.connect(pg_dsn) as c:
        assert c.execute("select options->>'max_pages' from audits where user_id=%s", (uid2,)).fetchone()[0] == "100"


def test_tick_requires_secret_and_advances(client, pg_dsn, site):
    assert client.post("/api/worker/tick").status_code == 401
    assert client.get("/api/worker/tick", headers={"Authorization": "Bearer faux"}).status_code == 401
    _, h = new_user(pg_dsn)
    audit_id = client.post("/api/audits", json={"url": site + "/"}, headers=h).get_json()["id"]
    for _ in range(30):
        client.get("/api/worker/tick", headers={"Authorization": "Bearer cron"})
        if client.get(f"/api/audits/{audit_id}", headers=h).get_json()["status"] == "done":
            break
    assert client.get(f"/api/audits/{audit_id}", headers=h).get_json()["status"] == "done"


def test_competitors(client, pg_dsn, site):
    _, h = new_user(pg_dsn, plan="pro")
    run_audit(client, h, site + "/")
    assert client.post("/api/competitors", json={"site": "inconnu.fr", "url": "https://exemple.org"}, headers=h).status_code == 400
    # le faux site joue le rôle du concurrent via son autre nom d'hôte
    rival = site.replace("127.0.0.1", "localhost") + "/"
    run_audit(client, h, rival, "/api/competitors", {"site": "127.0.0.1", "url": rival})
    data = client.get("/api/competitors/127.0.0.1", headers=h).get_json()
    assert data["own"]["pages"] > 0 and data["competitors"][0]["profile"]["pages"] > 0


def test_google_search_console(client, pg_dsn, monkeypatch):
    uid, h = new_user(pg_dsn)
    monkeypatch.setattr(google, "access_token", lambda rt, cid, cs: "acc-" + rt)
    monkeypatch.setattr(google, "list_sites", lambda token: [{"site": "sc-domain:exemple.fr", "level": "siteOwner"}])
    rows = [{"date": f"2026-03-{d:02d}", "query": "randonnée", "clicks": 1, "impressions": 10, "ctr": .1,
             "position": 10 - (d > 15) * 4} for d in range(1, 31)]
    monkeypatch.setattr(google, "search_analytics", lambda token, site, start, end, offset=0: rows if offset == 0 else [])

    def pages_down(*a, **kw):
        raise google.GoogleError("Quota Google dépassé")
    monkeypatch.setattr(google, "search_analytics_by", pages_down)  # l'import doit réussir quand même
    assert client.get("/api/google/sites", headers=h).status_code == 409  # pas encore connecté
    assert client.post("/api/google/token", json={"refresh_token": "rt-secret"}, headers=h).status_code == 200
    with psycopg.connect(pg_dsn) as c:
        stored = c.execute("select refresh_token_enc from google_tokens where user_id=%s", (uid,)).fetchone()[0]
    assert "rt-secret" not in stored  # chiffré
    assert client.post("/api/positions/sync", json={"site": "sc-domain:autre.fr"}, headers=h).status_code == 403
    r = client.post("/api/positions/sync", json={"site": "sc-domain:exemple.fr", "days": 30}, headers=h).get_json()
    assert r["done"] and r["imported"] == 30 and r["pages"] is False
    summary = client.get("/api/positions/summary?site=sc-domain:exemple.fr&days=15", headers=h).get_json()
    assert summary["queries"][0]["position"] == 6 and summary["queries"][0]["previous_position"] == 10
    _, other = new_user(pg_dsn)
    assert client.get("/api/positions/summary?site=sc-domain:exemple.fr", headers=other).get_json()["queries"] == []
    # import quotidien par le planificateur : la propriété est déjà à jour aujourd'hui, rien à refaire
    assert client.get("/api/worker/tick", headers={"Authorization": "Bearer cron"}).get_json()["gsc"] == []
    assert client.post("/api/google/disconnect", headers=h, json={}).get_json() == {"connected": False}


def test_keywords_quota(client, pg_dsn, monkeypatch):
    from seo_audit import keywords
    async def fake(seed, **kw):
        return [(seed + " pas cher", "variante"), ("comment " + seed, "question")]
    monkeypatch.setattr(keywords, "autocomplete", fake)
    uid, h = new_user(pg_dsn)
    ideas = client.post("/api/keywords", json={"seed": "tente"}, headers=h).get_json()
    assert {i["idea"] for i in ideas} == {"tente pas cher", "comment tente"}
    with psycopg.connect(pg_dsn) as c:
        c.execute("insert into usage_events (user_id, kind) select %s, 'keywords' from generate_series(1, 9)", (uid,))
    assert client.post("/api/keywords", json={"seed": "sac"}, headers=h).status_code == 402


def test_account_deletion(pg_dsn, monkeypatch):
    client = cloud_app.create_app(settings(pg_dsn, supabase_service_role_key="srk")).test_client()
    uid, h = new_user(pg_dsn)
    calls = []

    class Resp:
        status_code = 200
    monkeypatch.setattr(cloud_app.httpx, "delete", lambda url, **kw: calls.append((url, kw["headers"])) or Resp())
    assert client.post("/api/me/delete", json={}, headers=h).status_code == 400
    assert client.post("/api/me/delete", json={"confirm": "SUPPRIMER"}, headers=h).status_code == 200
    assert calls[0][0].endswith(f"/auth/v1/admin/users/{uid}") and calls[0][1]["apikey"] == "srk"


def test_plan_granted_before_signup(client, pg_dsn):
    email = f"{uuid.uuid4().hex[:8]}@cstlab.dev"
    with psycopg.connect(pg_dsn) as c:
        c.execute("insert into plan_grants (email, plan) values (%s, 'agence')", (email.upper(),))
        uid = str(uuid.uuid4())
        c.execute("insert into auth.users values (%s, %s)", (uid, email))
        assert c.execute("select plan from profiles where user_id=%s", (uid,)).fetchone()[0] == "agence"


def test_insights_endpoint(client, pg_dsn, monkeypatch):
    uid, h = new_user(pg_dsn)
    monkeypatch.setattr(google, "access_token", lambda rt, cid, cs: "acc")
    monkeypatch.setattr(google, "list_sites", lambda token: [{"site": "sc-domain:exemple.fr", "level": "siteOwner"}])
    monkeypatch.setattr(google, "search_analytics", lambda token, site, start, end, offset=0: [
        {"date": "2026-03-01", "query": "exemple", "clicks": 5, "impressions": 200, "ctr": .02, "position": 4.0}] if offset == 0 else [])

    def by(token, site, start, end, dims, offset=0):
        if offset:
            return []
        if dims == ["page"]:
            return [{"page": "https://exemple.fr/", "clicks": 5, "impressions": 200, "position": 4.0},
                    {"page": "http://exemple.fr/", "clicks": 0, "impressions": 20, "position": 8.0}]
        return [{"query": "exemple", "page": "https://exemple.fr/", "clicks": 5, "impressions": 200, "position": 4.0}]
    monkeypatch.setattr(google, "search_analytics_by", by)
    client.post("/api/google/token", json={"refresh_token": "rt"}, headers=h)
    assert client.get("/api/positions/insights?site=sc-domain:exemple.fr", headers=h).status_code == 404  # pas suivie
    r = client.post("/api/positions/sync", json={"site": "sc-domain:exemple.fr", "days": 30}, headers=h).get_json()
    assert r["done"] and r["pages"]
    data = client.get("/api/positions/insights?site=sc-domain:exemple.fr", headers=h).get_json()
    ids = {i["id"] for i in data["insights"]}
    assert {"brand", "technical"} <= ids and data["overview"]["has_pages"]
    _, other = new_user(pg_dsn)
    assert client.get("/api/positions/insights?site=sc-domain:exemple.fr", headers=other).status_code == 404
