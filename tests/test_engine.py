import asyncio
import os
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

from seo_audit.checks import run_checks
from seo_audit.crawler import Crawler
from seo_audit.engine import run_tick, step_audit
from seo_audit.engine.memory_store import MemoryStore
from seo_audit.ssrf import check_public_url

from .site_de_test import start_server

MIGRATION = Path(__file__).parent.parent / "supabase" / "migrations" / "0001_audits.sql"
AUTH_STUB = """
create schema auth;
create table auth.users (id uuid primary key);
create function auth.uid() returns uuid language sql stable as $$ select nullif(current_setting('request.uid', true), '')::uuid $$;
create role authenticated; create role anon;
grant usage on schema auth to authenticated;
"""
USER = "11111111-1111-1111-1111-111111111111"


@pytest.fixture(scope="module")
def site():
    server, base = start_server()
    yield base
    server.shutdown()


def issue_keys(issues, base):
    return {(i.url.replace(base, ""), i.code) for i in issues}


def run_to_completion(store, audit_id, budget=0.05):
    for _ in range(2000):
        audit = asyncio.run(step_audit(store, audit_id, budget=budget))
        if audit["status"] in ("done", "failed"):
            return audit
    raise AssertionError("l'audit ne se termine pas")


def classic_issues(base):
    crawler = Crawler(base + "/", concurrency=3)
    asyncio.run(crawler.run())
    return run_checks(crawler, {})


def test_chunked_audit_matches_classic_crawl(site):
    store = MemoryStore()
    audit_id = store.create_audit(USER, site + "/", {"max_pages": 500})
    audit = run_to_completion(store, audit_id)
    assert audit["status"] == "done", audit["error"]
    report = store.reports[audit_id]
    engine = {(i["url"].replace(site, ""), i["code"]) for i in report["issues"]}
    expected = issue_keys(classic_issues(site), site)
    # les contrôles de liens externes ne tournent pas dans le moteur (étape à part)
    expected = {k for k in expected if k[1] != "broken_external_link"}
    assert engine == expected
    assert report["summary"]["health_score"] > 0 and not store.pages[audit_id]  # pages purgées


def test_second_audit_is_compared_with_first(site):
    store = MemoryStore()
    first = run_to_completion(store, store.create_audit(USER, site + "/", {"max_pages": 500}))
    second = run_to_completion(store, store.create_audit(USER, site + "/", {"max_pages": 500}))
    cmp = store.reports[second["id"]]["summary"]["comparison"]
    assert cmp["new_count"] == 0 and cmp["fixed_count"] == 0
    other_user = store.create_audit("22222222-2222-2222-2222-222222222222", site + "/", {"max_pages": 500})
    assert "comparison" not in store.reports[run_to_completion(store, other_user)["id"]]["summary"]


def test_tick_advances_all_active_audits(site):
    store = MemoryStore()
    ids = [store.create_audit(USER, site + "/", {"max_pages": 20}) for _ in range(2)]
    for _ in range(200):
        asyncio.run(run_tick(store, budget=10))
        if not store.active_audits():
            break
    assert all(store.get_audit(i)["status"] == "done" for i in ids)


def test_ssrf_guard_blocks_private_targets():
    store = MemoryStore()
    audit_id = store.create_audit(USER, "http://127.0.0.1:9/", {"max_pages": 5})
    for _ in range(50):
        audit = asyncio.run(step_audit(store, audit_id, budget=5, url_guard=check_public_url))
        if audit["status"] != "running":
            break
    issues = store.reports[audit_id]["issues"]
    assert any(i["code"] == "fetch_error" and "refusée" in i["detail"] for i in issues)


# -- Postgres réel -------------------------------------------------------------
@pytest.fixture(scope="module")
def pg_dsn(tmp_path_factory):
    if not (shutil.which("initdb") and shutil.which("pg_ctl")):
        pytest.skip("PostgreSQL local absent")
    root = tmp_path_factory.mktemp("pg")
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    subprocess.run(["initdb", "-D", str(root / "data"), "-U", "postgres", "-A", "trust"], check=True, capture_output=True)
    subprocess.run(["pg_ctl", "-D", str(root / "data"), "-o", f"-p {port} -c unix_socket_directories= -c listen_addresses=127.0.0.1",
                    "-l", str(root / "log"), "-w", "start"], check=True, capture_output=True)
    dsn = f"postgresql://postgres@127.0.0.1:{port}/postgres"
    subprocess.run(["psql", dsn, "-v", "ON_ERROR_STOP=1", "-q", "-c", AUTH_STUB, "-c", f"insert into auth.users values ('{USER}')",
                    "-c", "insert into auth.users values ('22222222-2222-2222-2222-222222222222')", "-f", str(MIGRATION)],
                   check=True, capture_output=True)
    yield dsn
    subprocess.run(["pg_ctl", "-D", str(root / "data"), "-m", "immediate", "stop"], capture_output=True)


def test_postgres_store_matches_memory(site, pg_dsn):
    from seo_audit.engine.pg_store import PgStore
    store = PgStore(pg_dsn)
    audit_id = store.create_audit(USER, site + "/", {"max_pages": 500})
    audit = run_to_completion(store, audit_id)
    assert audit["status"] == "done", audit["error"]
    mem = MemoryStore()
    mid = mem.create_audit(USER, site + "/", {"max_pages": 500})
    run_to_completion(mem, mid)
    key = lambda r: {(i["url"].replace(site, ""), i["code"]) for i in r["issues"]}
    with store._conn() as c:
        pg_report = c.execute("select issues from audit_reports where audit_id=%s", (audit_id,)).fetchone()
    assert key(pg_report) == key(mem.reports[mid])
    assert store.counts(audit_id)["total"] == 0  # pages purgées


def test_postgres_claims_are_exclusive_and_rls_isolates_users(site, pg_dsn):
    import psycopg
    from seo_audit.engine.pg_store import PgStore
    store = PgStore(pg_dsn)
    audit_id = store.create_audit(USER, "https://exemple.fr/", {"max_pages": 50})
    store.add_pending(audit_id, [(f"https://exemple.fr/p{i}", 1) for i in range(20)], 50)
    first, second = store.claim_pending(audit_id, 12), store.claim_pending(audit_id, 12)
    assert len(first) == 12 and len(second) == 8
    assert not {r["url"] for r in first} & {r["url"] for r in second}
    assert store.add_pending(audit_id, [(f"https://exemple.fr/q{i}", 1) for i in range(100)], 50) == 30  # plafond max_pages

    def visible(uid):
        with psycopg.connect(pg_dsn) as c:
            c.execute("set local role authenticated")
            c.execute("select set_config('request.uid', %s, true)", (uid,))
            return (c.execute("select count(*) from audits where id=%s", (audit_id,)).fetchone()[0],
                    c.execute("select count(*) from audit_pages").fetchone()[0] if False else 0)
    assert visible(USER)[0] == 1
    assert visible("22222222-2222-2222-2222-222222222222")[0] == 0
    with psycopg.connect(pg_dsn) as c:
        c.execute("set local role authenticated")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("select * from audit_pages")
