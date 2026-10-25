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


def new_user(pg_dsn, plan=None, aal="aal1"):
    uid = str(uuid.uuid4())
    with psycopg.connect(pg_dsn) as c:
        c.execute("insert into auth.users values (%s, %s)", (uid, f"{uid[:8]}@exemple.fr"))
        if plan:
            c.execute("update profiles set plan=%s where user_id=%s", (plan, uid))
    token = jwt.encode({"sub": uid, "email": f"{uid[:8]}@exemple.fr", "aud": "authenticated",
                        "iss": f"{SUPA}/auth/v1", "exp": int(time.time()) + 3600, "aal": aal}, SECRET, algorithm="HS256")
    return uid, {"Authorization": f"Bearer {token}"}


def connect_google(client, headers, monkeypatch, refresh_token):
    """Parcours de connexion Search Console : URL Google signée, puis retour de Google sur /api/google/callback."""
    from urllib.parse import parse_qs, urlsplit
    monkeypatch.setattr(google, "exchange_code", lambda code, cid, cs, uri: {"refresh_token": refresh_token, "email": "moi@gmail.com"})
    url = client.get("/api/google/connect-url", headers=headers).get_json()["url"]
    q = parse_qs(urlsplit(url).query)
    assert "webmasters.readonly" in q["scope"][0] and q["access_type"] == ["offline"] and q["redirect_uri"][0].endswith("/api/google/callback")
    r = client.get(f"/api/google/callback?state={q['state'][0]}&code=abc")
    assert r.status_code == 302 and r.headers["Location"].endswith("google=ok")


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
    connect_google(client, h, monkeypatch, "rt-secret")
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
    r = client.post("/api/keywords", json={"seed": "tente"}, headers=h).get_json()
    assert {i["idea"] for i in r["ideas"]} == {"tente pas cher", "comment tente"} and r["market"] is None
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
    connect_google(client, h, monkeypatch, "rt")
    assert client.get("/api/positions/insights?site=sc-domain:exemple.fr", headers=h).status_code == 404  # pas suivie
    r = client.post("/api/positions/sync", json={"site": "sc-domain:exemple.fr", "days": 30}, headers=h).get_json()
    assert r["done"] and r["pages"]
    data = client.get("/api/positions/insights?site=sc-domain:exemple.fr", headers=h).get_json()
    ids = {i["id"] for i in data["insights"]}
    assert {"brand", "technical"} <= ids and data["overview"]["has_pages"]
    _, other = new_user(pg_dsn)
    assert client.get("/api/positions/insights?site=sc-domain:exemple.fr", headers=other).status_code == 404


def test_action_plan(client, pg_dsn, site):
    from datetime import date, timedelta
    uid, h = new_user(pg_dsn, plan="pro")
    audit_id, _ = run_audit(client, h, site + "/")
    plan = client.get("/api/plan/127.0.0.1", headers=h).get_json()
    fps = {t["fingerprint"]: t for t in plan["tasks"]}
    assert "audit:missing_title" in fps and "audit:http_4xx" in fps
    assert fps["audit:missing_title"]["action"].startswith("Rédigez un title")
    assert all(t["status"] == "todo" for t in plan["tasks"])
    assert client.get("/api/plan", headers=h).get_json()["127.0.0.1"] == len(plan["tasks"])

    # un nouvel audit où le title manquant est corrigé : la tâche se ferme toute seule
    with psycopg.connect(pg_dsn) as c:
        c.execute("""update audit_reports set issues = (select jsonb_agg(i) from jsonb_array_elements(issues) i
                     where i->>'code' <> 'missing_title') where audit_id=%s""", (audit_id,))
        c.execute("update audits set finished_at = now() + interval '1 minute' where id=%s", (audit_id,))
    t = {x["fingerprint"]: x for x in client.get("/api/plan/127.0.0.1", headers=h).get_json()["tasks"]}["audit:missing_title"]
    assert t["status"] == "done" and t["impact"]["count_now"] == 0 and t["impact"]["resolved_by_audit"]

    # marquer, ignorer, et un autre compte ne peut rien modifier
    other_id = fps["audit:http_4xx"]["id"]
    _, bob = new_user(pg_dsn)
    assert client.post(f"/api/plan/tasks/{other_id}", json={"status": "done"}, headers=bob).status_code == 404
    assert client.get("/api/plan/127.0.0.1", headers=bob).get_json()["tasks"] == []
    assert client.post(f"/api/plan/tasks/{other_id}", json={"status": "n'importe"}, headers=h).status_code == 400
    assert client.post(f"/api/plan/tasks/{other_id}", json={"status": "ignored"}, headers=h).status_code == 200

    # mesure d'impact d'une tâche Search Console : 28 jours avant / après
    done = date.today() - timedelta(days=12)
    with psycopg.connect(pg_dsn) as c:
        c.execute("insert into gsc_properties (user_id, site) values (%s, 'sc-domain:127.0.0.1')", (uid,))
        for d in range(1, 29):
            c.execute("insert into gsc_daily values (%s,'sc-domain:127.0.0.1',%s,'tente',0,10,0,20)", (uid, done - timedelta(days=d)))
        for d in range(1, 11):
            c.execute("insert into gsc_daily values (%s,'sc-domain:127.0.0.1',%s,'tente',1,20,.05,10)", (uid, done + timedelta(days=d)))
        c.execute("""insert into tasks (user_id, site, fingerprint, source, priority, title, queries, baseline, status, done_at)
                     values (%s,'127.0.0.1','gsc:quick:/tente','gsc','high','Optimiser /tente','["tente"]',
                             '{"property":"sc-domain:127.0.0.1"}','done',%s)""", (uid, done))
    t = {x["fingerprint"]: x for x in client.get("/api/plan/127.0.0.1", headers=h).get_json()["tasks"]}["gsc:quick:/tente"]
    assert t["impact"]["impressions_pct"] == 100 and t["impact"]["position_before"] == 20 and t["impact"]["position_after"] == 10
    assert t["impact"]["final"] is False


def test_keywords_with_market_data_and_shared_cache(pg_dsn, monkeypatch):
    import json
    from pathlib import Path
    from seo_audit import keywords
    from seo_audit.cloud import dataforseo
    fixture = json.loads((Path(__file__).parent / "fixtures" / "dfs_keyword_suggestions.json").read_text())
    calls = []

    def fake_post(self, path, body):
        calls.append(path)
        if "keyword_suggestions" in path:
            return fixture
        return {"total_count": 1, "items": [{"keyword_data": {"keyword": "logiciel devis", "keyword_info": {"search_volume": 1900, "cpc": 5.1},
                                                              "keyword_properties": {"keyword_difficulty": 25},
                                                              "search_intent_info": {"main_intent": "transactional"}}}]}
    monkeypatch.setattr(dataforseo.DataForSEO, "_post", fake_post)

    async def fake_auto(seed, **kw):
        return [("logiciel facturation gratuit", "suggestion"), ("logiciel facturation auto entrepreneur 2026", "suggestion")]
    monkeypatch.setattr(keywords, "autocomplete", fake_auto)
    client = cloud_app.create_app(settings(pg_dsn, dataforseo_login="l", dataforseo_password="p")).test_client()
    _, h = new_user(pg_dsn, plan="pro")
    r = client.post("/api/keywords", json={"seed": "logiciel facturation", "location": 2250}, headers=h).get_json()
    m = r["market"]
    assert m["total"] == fixture["total_count"] + 1 and not m["cached"]
    first = m["items"][0]
    assert first["keyword"] == "logiciel pour facturation gratuit" and first["volume"] == 3600 and first["difficulty"] == 60
    assert first["intent"] == "commercial" and len(first["trend"]) == 12 and first["cpc"] == 9.55
    assert any(i["keyword"] == "logiciel devis" and i["intent"] == "transactionnel" for i in m["items"])
    vol = {i["idea"]: i["volume"] for i in r["ideas"]}
    assert vol["logiciel facturation gratuit"] == 3600 and vol["logiciel facturation auto entrepreneur 2026"] is None
    # même recherche par un autre utilisateur : servie par le cache, aucun nouvel appel payant
    _, h2 = new_user(pg_dsn, plan="pro")
    r2 = client.post("/api/keywords", json={"seed": "Logiciel facturation", "location": 2250}, headers=h2).get_json()
    assert r2["market"]["cached"] and len(calls) == 2
    assert client.post("/api/keywords", json={"seed": "x y", "location": 9999}, headers=h).status_code == 400


def test_dataforseo_errors_are_readable(monkeypatch):
    from seo_audit.cloud import dataforseo

    class R:
        status_code, content = 200, b"x"
        def __init__(self, data): self._d = data
        def json(self): return self._d
    monkeypatch.setattr(dataforseo.httpx, "request", lambda *a, **k: R({"status_code": 40200, "status_message": "Payment Required"}))
    with pytest.raises(dataforseo.DataForSEOError, match="Crédit"):
        dataforseo.DataForSEO("l", "p").keyword_suggestions("x", 2250)


class FakeSerp:
    """Faux DataForSEO pour le suivi : tâches différées, prêtes au passage suivant."""
    def __init__(self):
        self.posted, self.ready, self.n = [], [], 0

    def keyword_volumes(self, keywords, location):
        return {k: 100 * (i + 1) for i, k in enumerate(keywords)}

    def post_serp_tasks(self, jobs, depth=30):
        ids = []
        for j in jobs:
            self.n += 1
            ids.append(f"t{self.n}")
            self.posted.append((f"t{self.n}", j["keyword"]))
        return ids

    def release(self):  # les relevés envoyés deviennent disponibles
        self.ready = [t for t, _ in self.posted]

    def serp_ready(self):
        return list(self.ready)

    def serp_result(self, task_id):
        self.ready.remove(task_id)
        kw = dict(self.posted)[task_id]
        mine = 4 if "facturation" in kw else None
        ranks = {"concurrent.fr": [1, "https://concurrent.fr/"], "autre.fr": [2, "https://autre.fr/"],
                 "wiki.fr": [3, "https://wiki.fr/x"]}
        if mine:
            ranks["exemple.fr"] = [mine, "https://www.exemple.fr/logiciel"]
        return {"ranks": ranks, "top": [[p, d, u, d] for d, (p, u) in sorted(ranks.items(), key=lambda x: x[1][0])]}


def test_rank_tracking(pg_dsn, monkeypatch):
    from seo_audit.cloud import tracking
    fake = FakeSerp()
    app_ = cloud_app.create_app(settings(pg_dsn, dataforseo_login="l", dataforseo_password="p"))
    monkeypatch.setattr(cloud_app, "DataForSEO", lambda *a, **k: fake)
    client = app_.test_client()
    uid, h = new_user(pg_dsn, plan="pro")
    _, h2 = new_user(pg_dsn, plan="pro")
    with psycopg.connect(pg_dsn) as c:  # repartir d'un moteur vide
        c.execute("delete from serp_tasks"); c.execute("delete from data_cache where key like 'serp|%'")

    r = client.post("/api/tracking/www.exemple.fr/keywords", json={"keywords": "Logiciel de facturation\nfacture charpentier\n\n", "location": 2250}, headers=h)
    assert r.status_code == 201 and r.get_json()["added"] == 2 and r.get_json()["stats"]["posted"] == 2
    # un 2e utilisateur suit le même mot-clé : aucun nouveau relevé payé
    r2 = client.post("/api/tracking/autre-site.fr/keywords", json={"keywords": ["logiciel de facturation"]}, headers=h2)
    assert r2.get_json()["stats"]["posted"] == 0 and len(fake.posted) == 2

    pending = client.get("/api/tracking/exemple.fr", headers=h).get_json()
    assert pending["kpis"]["pending"] == 2 and pending["keywords"][0]["volume"] is not None

    db = cloud_app.Db(pg_dsn)
    fake.release()
    stats = tracking.run(db, fake, budget=20)
    assert stats["collected"] == 2
    d = client.get("/api/tracking/exemple.fr", headers=h).get_json()
    rows = {r["keyword"]: r for r in d["keywords"]}
    assert rows["logiciel de facturation"]["position"] == 4 and rows["facture charpentier"]["position"] is None
    assert rows["logiciel de facturation"]["url"] == "https://www.exemple.fr/logiciel"
    assert d["kpis"]["top10"] == 1 and d["kpis"]["out"] == 1 and d["kpis"]["visibility"] == round(.07 / (2 * .28) * 100, 1)
    assert set(d["suggested"]) >= {"concurrent.fr", "autre.fr"}
    # le relevé partagé a aussi servi au 2e utilisateur
    other = client.get("/api/tracking/autre-site.fr", headers=h2).get_json()
    assert other["kpis"]["pending"] == 0 and other["keywords"][0]["position"] is None

    assert client.post("/api/tracking/exemple.fr/competitors", json={"domain": "https://www.concurrent.fr/page"}, headers=h).status_code == 201
    d = client.get("/api/tracking/exemple.fr", headers=h).get_json()
    comp = {c["domain"]: c for c in d["competitors"]}
    assert comp["concurrent.fr"]["top10"] == 2 and comp["exemple.fr"]["you"]
    detail = client.get(f"/api/tracking/keywords/{rows['logiciel de facturation']['id']}", headers=h).get_json()
    assert detail["series"]["exemple.fr"][0][1] == 4 and detail["series"]["concurrent.fr"][0][1] == 1 and detail["top"][0][1] == "concurrent.fr"

    # isolation, quota et suppression
    assert client.get(f"/api/tracking/keywords/{rows['logiciel de facturation']['id']}", headers=h2).status_code == 404
    assert client.delete(f"/api/tracking/keywords/{rows['logiciel de facturation']['id']}", headers=h2).status_code == 404
    _, free = new_user(pg_dsn)
    many = "\n".join(f"mot {i}" for i in range(11))
    assert client.post("/api/tracking/exemple.fr/keywords", json={"keywords": many}, headers=free).status_code == 402
    assert client.post("/api/tracking/pas un domaine/keywords", json={"keywords": "x"}, headers=h).status_code == 400
    assert client.delete(f"/api/tracking/keywords/{rows['facture charpentier']['id']}", headers=h).status_code == 200


def test_domain_analysis_and_gap(pg_dsn, monkeypatch):
    calls = []

    class FakeSER:
        def __init__(self, key): pass
        def overview(self, d, src): calls.append(("overview", d)); return {"keywords": 1200, "traffic": 3400, "value": 900, "new": 40, "lost": 12, "up": 30, "down": 8, "distribution": [10, 20, 30, 40, 50]}
        def keywords(self, d, src): calls.append(("keywords", d)); return [{"keyword": "devis artisan", "position": 3, "prev": 5, "volume": 900, "cpc": 2.1, "difficulty": 30, "traffic": 120, "url": "https://x.fr/devis", "intent": "commercial", "features": [], "their_position": None}]
        def competitors(self, d, src): calls.append(("competitors", d)); return [{"domain": "rival.fr", "common": 300, "missing": 150, "keywords": 450, "traffic": 2000}]
        def backlinks(self, d): calls.append(("backlinks", d)); return {"backlinks": 500, "refdomains": 80, "dofollow_refdomains": 60, "authority": 35, "dofollow_pct": 75, "anchors": [["x", 10]], "pages": [["https://x.fr/", 40]]}
        def gap(self, comp, mine, src): calls.append(("gap", comp, mine)); return [{"keyword": "logiciel devis", "volume": 1900, "difficulty": 25, "cpc": 5, "position": 4, "url": "https://rival.fr/", "intent": "commercial", "traffic": 0, "prev": None, "features": [], "their_position": None}]
    monkeypatch.setattr(cloud_app, "SERanking", FakeSER)
    client = cloud_app.create_app(settings(pg_dsn, seranking_api_key="k")).test_client()
    uid, h = new_user(pg_dsn)
    with psycopg.connect(pg_dsn) as c:
        c.execute("delete from data_cache where key like 'ser|%'")
    d = client.get("/api/domain?domain=https://www.X.fr/page&location=2250", headers=h).get_json()
    assert d["domain"] == "x.fr" and d["overview"]["traffic"] == 3400 and d["backlinks"]["authority"] == 35 and not d["cached"]
    assert d["competitors"][0]["domain"] == "rival.fr" and d["keywords"][0]["keyword"] == "devis artisan"
    # même domaine par un autre utilisateur : tout vient du cache, pas de quota consommé
    _, h2 = new_user(pg_dsn)
    d2 = client.get("/api/domain?domain=x.fr", headers=h2).get_json()
    assert d2["cached"] and len(calls) == 4
    assert client.get("/api/me", headers=h2).get_json()["usage"]["domain"] == 0
    g = client.get("/api/domain/gap?competitor=rival.fr&mine=x.fr", headers=h).get_json()
    assert g["keywords"][0]["keyword"] == "logiciel devis" and calls[-1] == ("gap", "rival.fr", "x.fr")
    assert client.get("/api/me", headers=h).get_json()["usage"]["domain"] == 2
    # quota gratuit : 5 analyses par mois
    with psycopg.connect(pg_dsn) as c:
        c.execute("insert into usage_events (user_id, kind) select %s, 'domain' from generate_series(1, 3)", (uid,))
    assert client.get("/api/domain?domain=nouveau.fr", headers=h).status_code == 402
    assert client.get("/api/domain?domain=x.fr&location=1", headers=h).status_code == 400
    assert client.get("/api/domain?domain=pas un domaine", headers=h).status_code == 400
    # sans clé configurée : message clair
    off = cloud_app.create_app(settings(pg_dsn)).test_client()
    assert off.get("/api/domain?domain=x.fr", headers=h).status_code == 503


def test_alerts_digest_and_unsubscribe(pg_dsn, monkeypatch, site):
    from datetime import date, datetime, timedelta, timezone
    from seo_audit.cloud import emails, notify
    sent = []
    monkeypatch.setattr(emails, "send", lambda cfg, to, subject, html, text, unsub="": sent.append((to, subject, html, text)) or "id")
    st = settings(pg_dsn, resend_api_key="re_test", email_from="Vigie SEO <vigie@exemple.fr>", app_url="https://app.test")
    client = cloud_app.create_app(st).test_client()
    db = cloud_app.Db(pg_dsn)
    uid, h = new_user(pg_dsn, plan="pro")
    with psycopg.connect(pg_dsn) as c:  # ne garder que les données de cet utilisateur dans le moteur d'envoi
        c.execute("update audits set notified_at = now() where notified_at is null")
        c.execute("insert into notification_settings (user_id, last_digest_at) select user_id, now() from profiles on conflict (user_id) do update set last_digest_at = now()")
        c.execute("update alerts set sent_at = now() where sent_at is null")

    # 1) un audit terminé avec un score en baisse et des problèmes graves → alertes
    audit_id, _ = run_audit(client, h, site + "/")
    with psycopg.connect(pg_dsn) as c:
        c.execute("""update audits set summary = jsonb_set(summary, '{comparison}', '{"score_delta": -8, "new": [{"severity": "high", "message": "Erreur client (404)", "url": "u", "code": "http_4xx"}], "fixed": []}')
                     where id=%s""", (audit_id,))
    # 2) un mot-clé suivi qui sort du top 10 entre hier et aujourd'hui
    with psycopg.connect(pg_dsn) as c:
        kid = c.execute("insert into rank_keywords (user_id, site, keyword) values (%s,'127.0.0.1','tente') returning id", (uid,)).fetchone()[0]
        c.execute("insert into rank_checks values (%s, current_date - 1, 7, null, '{}'), (%s, current_date, 14, null, '{}')", (kid, kid))
    stats = notify.run(db, st, budget=20)
    assert stats["alerts_created"] == 3 and stats["alert_emails"] == 1
    to, subject, html, text = sent[-1]
    assert to.endswith("@exemple.fr") and "3 alertes" in subject
    assert "Score en baisse de 8 points" in html and "sort du top 10" in html and "https://app.test/api/unsubscribe?u=" in html
    # pas de second e-mail dans l'heure, ni de doublon d'alerte
    assert notify.run(db, st, budget=20)["alert_emails"] == 0 and len(sent) == 1

    # 3) résumé hebdomadaire : dû une fois par semaine, lundi matin
    monday = notify.week_start(datetime.now(timezone.utc))
    db.set_notification_settings(uid, last_digest_at=monday - timedelta(days=1))
    stats = notify.run(db, st, budget=20, now=monday + timedelta(hours=1))
    assert stats["digests"] == 1 and "Votre semaine SEO" in sent[-1][1] and "127.0.0.1" in sent[-1][2]
    assert notify.run(db, st, budget=20, now=monday + timedelta(hours=2))["digests"] == 0

    # 4) désinscription par lien signé, et lien falsifié refusé
    url = emails.unsubscribe_url("https://app.test", st.token_key, uid, "digest").replace("https://app.test", "")
    assert client.get(url.replace("&t=", "&t=x")).status_code == 400
    assert client.get(url).status_code == 200
    assert client.get("/api/notifications", headers=h).get_json()["weekly_digest"] is False
    db.set_notification_settings(uid, last_digest_at=monday - timedelta(days=1))
    assert notify.run(db, st, budget=20, now=monday + timedelta(hours=3))["digests"] == 0

    # 5) e-mail de test depuis le compte, limité à 3 par heure
    for _ in range(3):
        assert client.post("/api/notifications/test", headers=h, json={}).status_code == 200
    assert client.post("/api/notifications/test", headers=h, json={}).status_code == 429
    assert sent[-1][1].startswith("[Test]")


def test_week_start_is_monday_morning():
    from datetime import datetime, timezone
    from seo_audit.cloud.notify import week_start
    wed = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)
    assert week_start(wed) == datetime(2026, 10, 26, 6, tzinfo=timezone.utc)
    early_monday = datetime(2026, 10, 26, 5, tzinfo=timezone.utc)
    assert week_start(early_monday) == datetime(2026, 10, 19, 6, tzinfo=timezone.utc)


def test_smtp_sending(monkeypatch):
    from seo_audit.cloud import emails
    boxes = []

    class FakeSMTP:
        def __init__(self, host, port, context=None, timeout=None): boxes.append({"host": host, "port": port})
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def login(self, user, pwd): boxes[-1]["login"] = (user, pwd)
        def send_message(self, msg): boxes[-1]["msg"] = msg
    monkeypatch.setattr(emails.smtplib, "SMTP_SSL", FakeSMTP)
    st = Settings(database_url="x", supabase_url="s", supabase_anon_key="a", smtp_host="smtp.hostinger.com",
                  smtp_user="moi@cstlab.dev", smtp_password="secret", email_from="Vigie SEO <vigieseo@cstlab.dev>",
                  resend_api_key="re_ignoree")
    assert emails.enabled(st)
    emails.send(st, "client@exemple.fr", "Sujet", '<img src="cid:vigie-logo"><p>Bonjour</p>', "Bonjour", "https://app/api/unsubscribe?u=1")
    b = boxes[0]
    assert b["host"] == "smtp.hostinger.com" and b["port"] == 465 and b["login"] == ("moi@cstlab.dev", "secret")
    m = b["msg"]
    assert m["From"] == "Vigie SEO <vigieseo@cstlab.dev>" and m["To"] == "client@exemple.fr"
    assert m["List-Unsubscribe"] == "<https://app/api/unsubscribe?u=1>" and m["Message-ID"].endswith("@cstlab.dev>")
    assert [p.get_content_type() for p in m.iter_parts()] == ["text/plain", "multipart/related"]
    html_part = m.get_body(("html",))
    logo = [p for p in m.walk() if p.get_content_type() == "image/png"][0]
    assert logo["Content-ID"] == "<vigie-logo>" and "cid:vigie-logo" in html_part.get_content()


def test_delete_project_tracking_and_property(client, pg_dsn, site):
    uid, h = new_user(pg_dsn, plan="pro")
    _, bob = new_user(pg_dsn, plan="pro")
    run_audit(client, h, site + "/")
    client.get("/api/plan/127.0.0.1", headers=h)
    with psycopg.connect(pg_dsn) as c:
        c.execute("insert into rank_keywords (user_id, site, keyword) values (%s, 'exemple.fr', 'tente')", (uid,))
        c.execute("insert into gsc_properties (user_id, site) values (%s, 'sc-domain:exemple.fr')", (uid,))
        c.execute("insert into gsc_daily values (%s, 'sc-domain:exemple.fr', current_date, 'tente', 1, 10, .1, 5)", (uid,))
    # un autre compte ne peut rien supprimer
    assert client.delete("/api/sites/127.0.0.1", headers=bob).status_code == 404
    assert client.delete("/api/sites/127.0.0.1", headers=h).status_code == 200
    assert client.get("/api/sites", headers=h).get_json() == [] and client.get("/api/plan/127.0.0.1", headers=h).get_json()["tasks"] == []
    assert client.delete("/api/tracking/exemple.fr", headers=bob).status_code == 404
    assert client.delete("/api/tracking/exemple.fr", headers=h).status_code == 200
    assert client.post("/api/positions/remove", json={"site": "sc-domain:exemple.fr"}, headers=bob).status_code == 404
    assert client.post("/api/positions/remove", json={"site": "sc-domain:exemple.fr"}, headers=h).status_code == 200
    with psycopg.connect(pg_dsn) as c:
        assert c.execute("select count(*) from gsc_daily where user_id=%s", (uid,)).fetchone()[0] == 0
        assert c.execute("select count(*) from rank_keywords where user_id=%s", (uid,)).fetchone()[0] == 0


def test_google_callback_rejects_forged_or_refused(client, pg_dsn, monkeypatch):
    uid, h = new_user(pg_dsn)
    assert "google=expire" in client.get("/api/google/callback?state=faux&code=x").headers["Location"]
    forged = f"{uid}.{int(time.time()) + 600}.abcd.signaturebidon"
    assert "google=expire" in client.get(f"/api/google/callback?state={forged}&code=x").headers["Location"]
    from urllib.parse import parse_qs, urlsplit
    state = parse_qs(urlsplit(client.get("/api/google/connect-url", headers=h).get_json()["url"]).query)["state"][0]
    assert "google=refuse" in client.get(f"/api/google/callback?state={state}&error=access_denied").headers["Location"]
    assert client.get("/api/google/status", headers=h).get_json()["connected"] is False


def test_two_factor_enforced_by_server(client, pg_dsn):
    uid, h = new_user(pg_dsn)
    assert client.get("/api/sites", headers=h).status_code == 200
    with psycopg.connect(pg_dsn) as c:
        c.execute("insert into auth.mfa_factors (user_id, status) values (%s, 'verified')", (uid,))
    r = client.get("/api/sites", headers=h)                     # session sans le code : refusée
    assert r.status_code == 401 and r.get_json()["mfa_required"]
    import jwt as _jwt
    aal2 = _jwt.encode({"sub": uid, "aud": "authenticated", "iss": f"{SUPA}/auth/v1", "exp": int(time.time()) + 600, "aal": "aal2"},
                       SECRET, algorithm="HS256")
    ok = client.get("/api/me", headers={"Authorization": f"Bearer {aal2}"})
    assert ok.status_code == 200 and ok.get_json()["mfa"] is True
