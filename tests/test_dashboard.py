import json

import pytest

from seo_audit import store
from seo_audit.competitors import summarize
from seo_audit.keywords import expansions, from_search_console
from seo_audit.web import app as webapp


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(webapp, "REPORTS", tmp_path / "rapports")
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "data" / "seo.db")
    return webapp.create_app().test_client()


def fake_audit(root, name, score):
    d = root / "rapports" / name
    d.mkdir(parents=True)
    (d / "audit.json").write_text(json.dumps({"summary": {
        "date": "2026-01-01T10:00", "health_score": score, "pages_crawled": 3, "pages_indexable": 2,
        "sitemap_urls": 1, "issues_by_severity": {"critical": 0, "high": 1, "medium": 0, "low": 0, "info": 0},
        "category_scores": {"http": score}}, "issues": [], "pages": []}))


def test_sites_and_history(client, tmp_path):
    fake_audit(tmp_path, "exemple.fr-20260101-1000", 70)
    fake_audit(tmp_path, "exemple.fr-20260201-1000", 80)
    sites = client.get("/api/sites").get_json()
    assert sites[0]["key"] == "exemple.fr" and sites[0]["audits"] == 2 and sites[0]["latest"]["score"] == 80
    assert client.get("/api/audits/exemple.fr-20260101-1000").status_code == 200


def test_path_traversal_and_guards(client):
    assert client.get("/api/audits/..%2F..%2Fetc").status_code == 404
    assert client.get("/reports/..%2F/x", follow_redirects=True).status_code == 404
    assert client.get("/api/sites", headers={"Host": "evil.example"}).status_code == 403
    assert client.post("/api/audits", data="url=x").status_code == 415
    assert client.post("/api/audits", json={"url": "x"}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/audits", json={"url": "ftp://exemple.fr"}).status_code == 400


def test_positions_summary(client):
    rows = [{"date": f"2026-03-{d:02d}", "query": "randonnée", "clicks": 1, "impressions": 10, "ctr": .1,
             "position": 10 - (d > 15) * 4} for d in range(1, 31)]
    store.save_gsc_rows("sc-domain:exemple.fr", rows)
    data = client.get("/api/positions/summary?site=sc-domain:exemple.fr&days=15").get_json()
    q = data["queries"][0]
    assert q["position"] == 6 and q["previous_position"] == 10
    assert len(data["daily"]) == 15 and store.gsc_sites() == ["sc-domain:exemple.fr"]


def test_keywords_helpers_and_competitor_summary():
    assert len(expansions("sac")) > 30 and ("comment sac", "question") in expansions("sac")
    ideas = from_search_console("sac dos", [{"query": "sac à dos 40l"}, {"query": "tente"}, {"query": "sac dos"}])
    assert ideas == [("sac à dos 40l", "search_console")]
    p = summarize([{"title_length": 40, "description_length": 100, "h1_count": 1, "word_count": 300, "schema_types": "Article"},
                   {"title_length": 20, "description_length": 0, "h1_count": 0, "word_count": 100, "schema_types": ""}])
    assert p["pct_with_description"] == 50 and p["pct_with_schema"] == 50 and p["avg_word_count"] == 200


def test_google_oauth_flow(client, monkeypatch):
    from urllib.parse import parse_qs, urlsplit

    from seo_audit import oauth
    monkeypatch.setattr(oauth, "DATA_DIR", store.DB_PATH.parent)
    monkeypatch.setattr(oauth, "BUNDLED_CLIENT", store.DB_PATH.parent / "absent.json")
    monkeypatch.delenv("VIGIE_GOOGLE_CLIENT_ID", raising=False)
    assert client.get("/api/google/status").get_json() == {"configured": False, "connected": False, "email": None}
    assert client.post("/api/google/connect", json={}).status_code == 400
    assert client.post("/api/google/client", json={"json": "pas du json"}).status_code == 400
    client.post("/api/google/client", json={"json": json.dumps({"installed": {"client_id": "id", "client_secret": "sec"}})})
    url = client.post("/api/google/connect", json={}).get_json()["url"]
    q = parse_qs(urlsplit(url).query)
    assert q["code_challenge_method"] == ["S256"] and "webmasters.readonly" in q["scope"][0]
    # un state inconnu est refusé (CSRF) ; le bon state échange le code
    assert "google=erreur" in client.get("/oauth/callback?state=faux&code=x").headers["Location"]

    class Resp:
        status_code, content = 200, b"{}"
        def json(self): return {"refresh_token": "r", "scope": "s"}
    monkeypatch.setattr(oauth.httpx, "post", lambda *a, **k: Resp())
    done = client.get(f"/oauth/callback?state={q['state'][0]}&code=abc")
    assert "google=ok" in done.headers["Location"]
    assert client.get("/api/google/status").get_json()["connected"] is True
    assert oct(oauth.token_path().stat().st_mode & 0o777) == "0o600"
