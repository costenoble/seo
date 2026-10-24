"""Suivi de positions : récupère les requêtes de la Search Console (API Search Analytics) et les stocke."""
import asyncio
import logging
from datetime import date, timedelta
from urllib.parse import quote

from . import store
from .gsc import _session, _with_retry

log = logging.getLogger(__name__)
ENDPOINT = "https://searchconsole.googleapis.com/webmasters/v3/sites/{site}/searchAnalytics/query"
PAGE_SIZE = 25000
GSC_DELAY_DAYS = 2  # les données les plus récentes ne sont pas encore disponibles


async def sync_positions(site_url: str, credentials_file: str | None = None, days: int = 90) -> int:
    """Importe les `days` derniers jours (requête x date). Rejouable : les lignes existantes sont remplacées."""
    session = await asyncio.to_thread(_session, credentials_file)
    end = date.today() - timedelta(days=GSC_DELAY_DAYS)
    start = end - timedelta(days=days - 1)
    url = ENDPOINT.format(site=quote(site_url, safe=""))
    total, offset = 0, 0
    while True:
        def call(offset=offset):
            r = session.post(url, timeout=120, json={
                "startDate": start.isoformat(), "endDate": end.isoformat(),
                "dimensions": ["date", "query"], "rowLimit": PAGE_SIZE, "startRow": offset})
            r.raise_for_status()
            return r.json()
        data = await _with_retry(call)
        rows = data.get("rows", [])
        total += store.save_gsc_rows(site_url, [
            {"date": r["keys"][0], "query": r["keys"][1], "clicks": r["clicks"],
             "impressions": r["impressions"], "ctr": r["ctr"], "position": r["position"]}
            for r in rows])
        log.info("Positions : %d lignes importées", total)
        if len(rows) < PAGE_SIZE:
            return total
        offset += PAGE_SIZE
