"""Search Console avec le jeton de l'utilisateur (obtenu via la connexion Google de Supabase)."""
from datetime import date, timedelta
from urllib.parse import quote

import httpx

TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://searchconsole.googleapis.com/webmasters/v3"
SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
GA4_SCOPE = "https://www.googleapis.com/auth/analytics.readonly"
SCOPES = {"gsc": SCOPE, "ga4": GA4_SCOPE}
ADMIN_API = "https://analyticsadmin.googleapis.com/v1beta"
DATA_API = "https://analyticsdata.googleapis.com/v1beta"
PAGE_SIZE = 25000
GSC_DELAY_DAYS = 2  # les deux derniers jours ne sont pas encore consolidés par Google


class GoogleError(Exception):
    def __init__(self, message: str, reconnect: bool = False):
        super().__init__(message)
        self.reconnect = reconnect


AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"


def auth_url(client_id: str, redirect_uri: str, state: str, login_hint: str = "", scopes: list[str] | None = None) -> str:
    """Autorisation incrémentale : les accès déjà accordés sont conservés (include_granted_scopes)."""
    from urllib.parse import urlencode
    params = {"client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code", "state": state,
              "scope": " ".join((scopes or [SCOPE]) + ["openid", "email"]), "access_type": "offline", "prompt": "consent",
              "include_granted_scopes": "true"}
    if login_hint:
        params["login_hint"] = login_hint
    return AUTH_URL + "?" + urlencode(params)


def exchange_code(code: str, client_id: str, client_secret: str, redirect_uri: str) -> dict:
    """Échange le code d'autorisation : {refresh_token, email, scope}."""
    import base64
    import json
    r = httpx.post(TOKEN_URL, timeout=20, data={"code": code, "client_id": client_id, "client_secret": client_secret,
                                               "redirect_uri": redirect_uri, "grant_type": "authorization_code"})
    data = r.json() if r.content else {}
    if r.status_code != 200:
        raise GoogleError(data.get("error_description") or "Google a refusé la connexion")
    granted = data.get("scope", "")
    if SCOPE not in granted and GA4_SCOPE not in granted:
        raise GoogleError("Aucun accès n'a été autorisé (Search Console ou Analytics)")
    if not data.get("refresh_token"):
        raise GoogleError("Google n'a pas fourni d'accès durable : retirez Vigie SEO de votre compte Google puis reconnectez")
    email = None
    try:  # id_token reçu directement de Google en HTTPS : on lit seulement l'adresse affichée
        part = data["id_token"].split(".")[1]
        email = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))).get("email")
    except (KeyError, ValueError, IndexError):
        pass
    return {"refresh_token": data["refresh_token"], "email": email, "scope": granted}


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


# -- Google Analytics 4 ------------------------------------------------------------------------------
def ga4_properties(token: str) -> list[dict]:
    out, page = [], None
    while True:
        data = _call("GET", f"{ADMIN_API}/accountSummaries", token, params={"pageSize": 200, **({"pageToken": page} if page else {})})
        for acc in data.get("accountSummaries", []):
            for p in acc.get("propertySummaries", []):
                out.append({"property_id": p["property"], "display_name": p.get("displayName", ""), "account": acc.get("displayName", "")})
        page = data.get("nextPageToken")
        if not page:
            return out


def ga4_report(token: str, property_id: str, body: dict) -> list[dict]:
    """runReport → lignes {dimension: valeur, métrique: nombre}."""
    data = _call("POST", f"{DATA_API}/{property_id}:runReport", token, json=body)
    dims = [d["name"] for d in data.get("dimensionHeaders", [])]
    mets = [m["name"] for m in data.get("metricHeaders", [])]
    rows = []
    for r in data.get("rows", []):
        row = {d: v.get("value") for d, v in zip(dims, r.get("dimensionValues", []))}
        for m, v in zip(mets, r.get("metricValues", [])):
            try:
                row[m] = float(v.get("value") or 0)
            except ValueError:
                row[m] = 0.0
        rows.append(row)
    return rows


def ga4_traffic(token: str, property_id: str, days: int = 28) -> dict:
    """Trafic sur `days` jours comparé à la période précédente, par canal et pages d'entrée depuis Google."""
    cur = {"startDate": f"{days}daysAgo", "endDate": "yesterday"}
    prev = {"startDate": f"{2 * days}daysAgo", "endDate": f"{days + 1}daysAgo"}
    metrics = [{"name": n} for n in ("totalUsers", "sessions", "keyEvents", "engagementRate")]
    totals = ga4_report(token, property_id, {"dateRanges": [cur, prev], "metrics": metrics})
    by_range = {r.get("dateRange", "date_range_0"): r for r in totals}
    daily = ga4_report(token, property_id, {"dateRanges": [cur], "dimensions": [{"name": "date"}, {"name": "sessionDefaultChannelGroup"}],
                                            "metrics": [{"name": "sessions"}], "limit": 5000})
    channels = ga4_report(token, property_id, {"dateRanges": [cur, prev], "dimensions": [{"name": "sessionDefaultChannelGroup"}],
                                               "metrics": [{"name": "sessions"}, {"name": "keyEvents"}]})
    organic_filter = {"filter": {"fieldName": "sessionDefaultChannelGroup", "stringFilter": {"value": "Organic Search"}}}
    pages = ga4_report(token, property_id, {"dateRanges": [cur], "dimensions": [{"name": "landingPage"}],
                                            "metrics": [{"name": "sessions"}, {"name": "keyEvents"}, {"name": "engagementRate"}],
                                            "dimensionFilter": organic_filter, "orderBys": [{"metric": {"metricName": "sessions"}, "desc": True}],
                                            "limit": 50})
    return {"days": days, "current": by_range.get("date_range_0", {}), "previous": by_range.get("date_range_1", {}),
            "daily": daily, "channels": channels, "organic_pages": pages}
