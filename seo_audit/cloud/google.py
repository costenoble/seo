"""Search Console avec le jeton de l'utilisateur (obtenu via la connexion Google de Supabase)."""
from datetime import date, timedelta
from urllib.parse import quote

import httpx

TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://searchconsole.googleapis.com/webmasters/v3"
SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
PAGE_SIZE = 25000
GSC_DELAY_DAYS = 2  # les deux derniers jours ne sont pas encore consolidés par Google


class GoogleError(Exception):
    def __init__(self, message: str, reconnect: bool = False):
        super().__init__(message)
        self.reconnect = reconnect


def access_token(refresh_token: str, client_id: str, client_secret: str) -> str:
    r = httpx.post(TOKEN_URL, timeout=20, data={
        "grant_type": "refresh_token", "refresh_token": refresh_token,
        "client_id": client_id, "client_secret": client_secret})
    if r.status_code in (400, 401):
        raise GoogleError("Autorisation Google expirée ou révoquée : reconnectez la Search Console", reconnect=True)
    r.raise_for_status()
    return r.json()["access_token"]


def _call(method: str, url: str, token: str, **kw) -> dict:
    r = httpx.request(method, url, headers={"Authorization": f"Bearer {token}"}, timeout=60, **kw)
    if r.status_code in (401, 403):
        raise GoogleError("Accès refusé par Google à cette propriété", reconnect=r.status_code == 401)
    if r.status_code == 429:
        raise GoogleError("Quota Google dépassé, réessayez dans quelques minutes")
    r.raise_for_status()
    return r.json()


def list_sites(token: str) -> list[dict]:
    entries = _call("GET", f"{API}/sites", token).get("siteEntry", [])
    return [{"site": e["siteUrl"], "level": e["permissionLevel"]} for e in entries
            if e["permissionLevel"] != "siteUnverifiedUser"]


def date_range(days: int, today: date | None = None) -> tuple[str, str]:
    end = (today or date.today()) - timedelta(days=GSC_DELAY_DAYS)
    return (end - timedelta(days=days - 1)).isoformat(), end.isoformat()


def search_analytics(token: str, site: str, start: str, end: str, offset: int = 0) -> list[dict]:
    data = _call("POST", f"{API}/sites/{quote(site, safe='')}/searchAnalytics/query", token, json={
        "startDate": start, "endDate": end, "dimensions": ["date", "query"],
        "rowLimit": PAGE_SIZE, "startRow": offset})
    return [{"date": r["keys"][0], "query": r["keys"][1], "clicks": r["clicks"], "impressions": r["impressions"],
             "ctr": r["ctr"], "position": r["position"]} for r in data.get("rows", [])]


def search_analytics_by(token: str, site: str, start: str, end: str, dimensions: list[str],
                        offset: int = 0) -> list[dict]:
    """Agrégats sur la période (sans la date) : par ["query", "page"] ou par ["page"]."""
    data = _call("POST", f"{API}/sites/{quote(site, safe='')}/searchAnalytics/query", token, json={
        "startDate": start, "endDate": end, "dimensions": dimensions, "rowLimit": PAGE_SIZE, "startRow": offset})
    return [{**dict(zip(dimensions, r["keys"])), "clicks": r["clicks"], "impressions": r["impressions"],
             "position": r["position"]} for r in data.get("rows", [])]


def revoke(refresh_token: str) -> None:
    try:
        httpx.post("https://oauth2.googleapis.com/revoke", data={"token": refresh_token}, timeout=10)
    except httpx.HTTPError:
        pass
