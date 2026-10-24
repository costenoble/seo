"""Connexion Google (OAuth 2.0, flux « application de bureau » avec PKCE) pour la Search Console.

Chaque utilisateur autorise l'accès en lecture seule avec son propre compte ; le jeton est
conservé dans data/google_token.json (permissions 600). L'identifiant OAuth de l'application
(client_id / client_secret) vient, dans l'ordre : des variables VIGIE_GOOGLE_CLIENT_ID et
VIGIE_GOOGLE_CLIENT_SECRET, de data/oauth_client.json, ou de seo_audit/oauth_client.json
(livré avec l'application). Pour une application « de bureau », Google considère ce secret
comme non confidentiel."""
import base64
import hashlib
import json
import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly", "openid", "email"]
SITES_URL = "https://searchconsole.googleapis.com/webmasters/v3/sites"
PENDING_TTL = 600

DATA_DIR = Path("data")
BUNDLED_CLIENT = Path(__file__).parent / "oauth_client.json"
_pending: dict[str, dict] = {}  # state -> {verifier, redirect_uri, expires}


def token_path() -> Path:
    return DATA_DIR / "google_token.json"


def client_path() -> Path:
    return DATA_DIR / "oauth_client.json"


def parse_client_json(raw: str | dict) -> dict:
    """Accepte le fichier téléchargé depuis Google Cloud (clé « installed » ou « web »)."""
    data = json.loads(raw) if isinstance(raw, str) else raw
    block = data.get("installed") or data.get("web") or data
    if not block.get("client_id") or not block.get("client_secret"):
        raise ValueError("Fichier invalide : client_id et client_secret sont attendus")
    return {"client_id": block["client_id"].strip(), "client_secret": block["client_secret"].strip()}


def load_client() -> dict | None:
    if os.getenv("VIGIE_GOOGLE_CLIENT_ID") and os.getenv("VIGIE_GOOGLE_CLIENT_SECRET"):
        return {"client_id": os.environ["VIGIE_GOOGLE_CLIENT_ID"], "client_secret": os.environ["VIGIE_GOOGLE_CLIENT_SECRET"]}
    for path in (client_path(), BUNDLED_CLIENT):
        if path.is_file():
            try:
                return parse_client_json(path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
    return None


def save_client(raw: str | dict) -> None:
    client = parse_client_json(raw)
    _write_private(client_path(), client)


def _write_private(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def begin_login(redirect_uri: str) -> str:
    client = load_client()
    if not client:
        raise ValueError("Aucun identifiant OAuth configuré")
    now = time.time()
    for s in [s for s, p in _pending.items() if p["expires"] < now]:
        del _pending[s]
    state, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    _pending[state] = {"verifier": verifier, "redirect_uri": redirect_uri, "expires": now + PENDING_TTL}
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return AUTH_URL + "?" + urlencode({
        "client_id": client["client_id"], "redirect_uri": redirect_uri, "response_type": "code",
        "scope": " ".join(SCOPES), "state": state, "code_challenge": challenge,
        "code_challenge_method": "S256", "access_type": "offline", "prompt": "consent"})


def finish_login(state: str, code: str) -> dict:
    """Échange le code contre un jeton ; refuse un state inconnu ou expiré (protection CSRF)."""
    pending = _pending.pop(state, None)
    if not pending or pending["expires"] < time.time():
        raise ValueError("Demande de connexion expirée ou inconnue, recommencez")
    client = load_client()
    r = httpx.post(TOKEN_URL, timeout=20, data={
        "code": code, "client_id": client["client_id"], "client_secret": client["client_secret"],
        "redirect_uri": pending["redirect_uri"], "grant_type": "authorization_code",
        "code_verifier": pending["verifier"]})
    body = r.json() if r.content else {}
    if r.status_code != 200 or "refresh_token" not in body:
        raise ValueError(body.get("error_description") or body.get("error") or "Google a refusé la connexion")
    email = None
    try:  # l'id_token vient directement de Google en HTTPS : on lit seulement l'adresse affichée
        payload = body["id_token"].split(".")[1]
        email = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))).get("email")
    except (KeyError, ValueError, IndexError):
        pass
    token = {"refresh_token": body["refresh_token"], "email": email, "scope": body.get("scope", "")}
    _write_private(token_path(), token)
    return {"email": email}


def status() -> dict:
    token = _read_token()
    return {"configured": load_client() is not None, "connected": token is not None,
            "email": token.get("email") if token else None}


def _read_token() -> dict | None:
    try:
        return json.loads(token_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def disconnect() -> None:
    token = _read_token()
    if token:  # révoque côté Google, puis efface le jeton local même si la révocation échoue
        try:
            httpx.post("https://oauth2.googleapis.com/revoke", timeout=10, data={"token": token["refresh_token"]})
        except httpx.HTTPError:
            pass
    token_path().unlink(missing_ok=True)


def credentials():
    """Identifiants google-auth rafraîchis automatiquement, ou None si non connecté."""
    token, client = _read_token(), load_client()
    if not token or not client:
        return None
    from google.oauth2.credentials import Credentials
    return Credentials(None, refresh_token=token["refresh_token"], token_uri=TOKEN_URL,
                       client_id=client["client_id"], client_secret=client["client_secret"], scopes=SCOPES)


def list_properties() -> list[dict]:
    from google.auth.transport.requests import AuthorizedSession
    creds = credentials()
    if not creds:
        raise PermissionError("Non connecté à Google")
    r = AuthorizedSession(creds).get(SITES_URL, timeout=30)
    if r.status_code in (401, 403):
        raise PermissionError("Accès refusé par Google : reconnectez-vous")
    r.raise_for_status()
    return [{"site": e["siteUrl"], "level": e["permissionLevel"]} for e in r.json().get("siteEntry", [])
            if e["permissionLevel"] != "siteUnverifiedUser"]
