"""Statut d'indexation réel via l'API URL Inspection de Google Search Console.

Pré-requis : un compte de service Google Cloud avec l'API Search Console activée,
ajouté comme utilisateur de la propriété. Quota : environ 2 000 inspections par jour
et par propriété, d'où la limite --gsc-max."""
import asyncio
import logging

log = logging.getLogger(__name__)
SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]
ENDPOINT = "https://searchconsole.googleapis.com/v1/urlInspection/index:inspect"
FIELDS = ("verdict", "coverageState", "indexingState", "robotsTxtState", "pageFetchState",
          "lastCrawlTime", "googleCanonical", "userCanonical")


def _session(credentials_file: str):
    try:
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2 import service_account
    except ImportError as exc:
        raise SystemExit("Dépendances Search Console manquantes : pip install google-auth requests") from exc
    creds = service_account.Credentials.from_service_account_file(credentials_file, scopes=SCOPES)
    return AuthorizedSession(creds)


async def _with_retry(call, attempts: int = 4):
    """Rejoue l'appel avec un délai croissant sur 429/5xx ; 401/403 arrête l'audit."""
    for n in range(attempts):
        try:
            return await asyncio.to_thread(call)
        except Exception as exc:
            code = getattr(getattr(exc, "response", None), "status_code", None)
            if code in (401, 403):
                raise PermissionError(f"HTTP {code}") from exc
            if code not in (429, 500, 502, 503, 504) or n == attempts - 1:
                raise
            await asyncio.sleep(2 ** n * 2)


async def inspect_urls(urls, *, site_url: str, credentials_file: str) -> dict[str, dict]:
    """site_url : 'https://www.example.com/' ou 'sc-domain:example.com'."""
    session = await asyncio.to_thread(_session, credentials_file)
    out = {}
    for url in urls:
        def call(u=url):
            r = session.post(ENDPOINT, timeout=60, json={
                "inspectionUrl": u, "siteUrl": site_url, "languageCode": "fr-FR"})
            r.raise_for_status()
            return r.json()
        try:
            data = await _with_retry(call)
            res = data.get("inspectionResult", {}).get("indexStatusResult", {})
            out[url] = {k: res.get(k) for k in FIELDS}
        except PermissionError as exc:
            raise SystemExit(f"Search Console refuse l'accès : {exc}. Le compte de service est-il "
                             "ajouté comme utilisateur de la propriété ?") from exc
        except Exception as exc:
            log.warning("Inspection GSC %s : %s", url, exc)
            out[url] = {"error": str(exc)[:200]}
    return out
