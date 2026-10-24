"""Idées de mots-clés gratuites : autocomplétion Google (point d'accès public non documenté)
et requêtes réelles de la Search Console déjà stockées. Pas de volumes de recherche."""
import asyncio
import string

import httpx

SUGGEST = "https://suggestqueries.google.com/complete/search"
QUESTION_WORDS = ("comment", "pourquoi", "quel", "quelle", "quels", "où", "quand", "combien", "est-ce que", "qu'est-ce que")
MODIFIERS = ("pour", "sans", "avec", "vs", "ou", "prix", "avis", "gratuit", "meilleur")


def expansions(seed: str) -> list[tuple[str, str]]:
    """(requête envoyée à l'autocomplétion, type d'idée)."""
    out = [(seed, "suggestion")]
    out += [(f"{seed} {c}", "suggestion") for c in string.ascii_lowercase]
    out += [(f"{w} {seed}", "question") for w in QUESTION_WORDS]
    out += [(f"{seed} {m}", "variante") for m in MODIFIERS]
    return out


async def autocomplete(seed: str, *, lang: str = "fr", concurrency: int = 4) -> list[tuple[str, str]]:
    sem = asyncio.Semaphore(concurrency)
    found: dict[str, str] = {}
    async with httpx.AsyncClient(timeout=10, headers={"User-Agent": "Mozilla/5.0"}) as client:
        async def one(q, kind):
            async with sem:
                try:
                    r = await client.get(SUGGEST, params={"client": "firefox", "hl": lang, "q": q})
                    r.raise_for_status()
                    suggestions = r.json()[1]
                except (httpx.HTTPError, ValueError, IndexError):
                    return
                await asyncio.sleep(0.15)  # rester poli avec le service
            for s in suggestions:
                if isinstance(s, str) and s.lower() != seed.lower():
                    found.setdefault(s, "question" if kind == "question" else kind)
        await asyncio.gather(*(one(q, k) for q, k in expansions(seed)))
    return sorted(found.items())


def from_search_console(seed: str, queries: list[dict]) -> list[tuple[str, str]]:
    words = [w for w in seed.lower().split() if len(w) > 2]
    return [(q["query"], "search_console") for q in queries
            if words and all(w in q["query"].lower() for w in words) and q["query"].lower() != seed.lower()]
