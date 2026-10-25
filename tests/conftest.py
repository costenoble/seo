import shutil
import socket
import subprocess
from pathlib import Path

import pytest

MIGRATIONS = sorted((Path(__file__).parent.parent / "supabase" / "migrations").glob("*.sql"))
# Ce que Supabase fournit d'office : schéma auth, auth.uid(), rôles anon et authenticated.
AUTH_STUB = """
create schema auth;
create table auth.users (id uuid primary key, email text);
create table auth.mfa_factors (id uuid primary key default gen_random_uuid(), user_id uuid, status text);
create function auth.uid() returns uuid language sql stable as $$ select nullif(current_setting('request.uid', true), '')::uuid $$;
create role authenticated; create role anon;
grant usage on schema auth to authenticated;
"""
USER = "11111111-1111-1111-1111-111111111111"
USER2 = "22222222-2222-2222-2222-222222222222"


@pytest.fixture(scope="session")
def pg_dsn(tmp_path_factory):
    """Postgres jetable avec les migrations Supabase appliquées (ignoré si PostgreSQL n'est pas installé)."""
    if not (shutil.which("initdb") and shutil.which("pg_ctl")):
        pytest.skip("PostgreSQL local absent")
    root = tmp_path_factory.mktemp("pg")
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    subprocess.run(["initdb", "-D", str(root / "data"), "-U", "postgres", "-A", "trust"], check=True, capture_output=True)
    subprocess.run(["pg_ctl", "-D", str(root / "data"), "-o", f"-p {port} -c unix_socket_directories= -c listen_addresses=127.0.0.1",
                    "-l", str(root / "log"), "-w", "start"], check=True, capture_output=True)
    dsn = f"postgresql://postgres@127.0.0.1:{port}/postgres"
    cmd = ["psql", dsn, "-v", "ON_ERROR_STOP=1", "-q", "-c", AUTH_STUB]
    for m in MIGRATIONS:
        cmd += ["-f", str(m)]
    cmd += ["-c", f"insert into auth.users values ('{USER}', 'a@exemple.fr'), ('{USER2}', 'b@exemple.fr')"]
    subprocess.run(cmd, check=True, capture_output=True)
    yield dsn
    subprocess.run(["pg_ctl", "-D", str(root / "data"), "-m", "immediate", "stop"], capture_output=True)
