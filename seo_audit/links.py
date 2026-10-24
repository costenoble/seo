"""Vérification des liens externes (HEAD, puis GET si le serveur refuse HEAD)."""
import asyncio

import httpx

RETRY_WITH_GET = {403, 404, 405, 429, 501}
# Statuts souvent renvoyés aux robots alors que la page fonctionne : non considérés comme cassés.
UNVERIFIABLE = {401, 403, 429, 999}


async def check_external_links(urls, *, user_agent: str, concurrency: int = 10,
                               timeout: float = 10.0) -> dict[str, dict]:
    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout,
                                 headers={"User-Agent": user_agent}) as client:
        async def one(url: str):
            async with sem:
                try:
                    r = await client.head(url)
                    status = r.status_code
                    if status in RETRY_WITH_GET or status >= 500:
                        async with client.stream("GET", url) as r2:
                            status = r2.status_code
                    return url, {"status": status, "error": None}
                except httpx.HTTPError as exc:
                    return url, {"status": None, "error": type(exc).__name__}

        return dict(await asyncio.gather(*(one(u) for u in urls)))


def is_broken(info: dict) -> bool:
    if info.get("error"):
        return True
    status = info.get("status")
    return status is not None and status >= 400 and status not in UNVERIFIABLE
