"""Données de marché via DataForSEO (paiement à l'usage) : volumes, difficulté, CPC, intention.
https://docs.dataforseo.com/v3/dataforseo_labs/overview/ — authentification Basic (login + mot de passe d'API)."""
import re

import httpx

API = "https://api.dataforseo.com/v3/"
# Marchés francophones proposés : code de lieu Google Ads → libellé
LOCATIONS = {2250: "France", 2056: "Belgique", 2756: "Suisse", 2124: "Canada"}
STOPWORDS = {"pour", "avec", "sans", "dans", "des", "les", "une", "sur", "par", "aux", "est", "pas"}
INTENTS = {"informational": "info", "navigational": "navigation", "commercial": "commercial",
           "transactional": "transactionnel"}


class DataForSEOError(Exception):
    pass


class DataForSEO:
    def __init__(self, login: str, password: str, timeout: float = 60):
        self.auth, self.timeout = (login, password), timeout

    def _post(self, path: str, body: dict) -> dict:
        try:
            r = httpx.post(API + path, json=[body], auth=self.auth, timeout=self.timeout)
        except httpx.HTTPError as exc:
            raise DataForSEOError(f"Service de données injoignable : {type(exc).__name__}") from exc
        data = r.json() if r.content else {}
        if r.status_code == 401 or data.get("status_code") == 40100:
            raise DataForSEOError("Identifiants DataForSEO refusés")
        if data.get("status_code") == 40200 or r.status_code == 402:
            raise DataForSEOError("Crédit DataForSEO épuisé : rechargez le compte")
        task = (data.get("tasks") or [{}])[0]
        if task.get("status_code") != 20000:
            raise DataForSEOError(task.get("status_message") or data.get("status_message") or "Erreur DataForSEO")
        return (task.get("result") or [{}])[0] or {}

    def keyword_suggestions(self, seed: str, location: int, language: str = "fr", limit: int = 100) -> dict:
        res = self._post("dataforseo_labs/google/keyword_suggestions/live", {
            "keyword": seed, "location_code": location, "language_code": language, "limit": limit,
            "include_seed_keyword": True, "order_by": ["keyword_info.search_volume,desc"]})
        items = [normalize(i, "match") for i in res.get("items") or []]
        seed_data = res.get("seed_keyword_data")
        if seed_data and (seed_data.get("keyword_info") or {}).get("search_volume") is not None \
                and all(i["keyword"] != seed_data.get("keyword") for i in items):
            items.insert(0, normalize(seed_data, "match"))
        return {"total": res.get("total_count") or 0, "items": items}

    def related_keywords(self, seed: str, location: int, language: str = "fr", limit: int = 100) -> dict:
        res = self._post("dataforseo_labs/google/related_keywords/live", {
            "keyword": seed, "location_code": location, "language_code": language, "depth": 2, "limit": limit,
            "order_by": ["keyword_data.keyword_info.search_volume,desc"]})
        # les mots-clés associés dérivent vite du sujet : on garde ceux qui partagent un vrai mot avec la recherche
        words = {w for w in _words(seed) if len(w) >= 4 and w not in STOPWORDS}
        items = [normalize(i.get("keyword_data") or {}, "related") for i in res.get("items") or []]
        return {"total": res.get("total_count") or 0,
                "items": [i for i in items if i["keyword"] and (not words or words & set(_words(i["keyword"])))]}


def _words(text: str) -> list[str]:
    return re.findall(r"[a-zàâäçéèêëîïôöùûüÿœ0-9]+", text.lower())


def normalize(item: dict, group: str = "match") -> dict:
    ki = item.get("keyword_info") or {}
    months = sorted(ki.get("monthly_searches") or [], key=lambda m: (m.get("year", 0), m.get("month", 0)))
    return {
        "keyword": item.get("keyword"),
        "volume": ki.get("search_volume"),
        "cpc": ki.get("cpc"),
        "competition": ki.get("competition_level"),
        "difficulty": (item.get("keyword_properties") or {}).get("keyword_difficulty"),
        "intent": INTENTS.get((item.get("search_intent_info") or {}).get("main_intent")),
        "trend": [m.get("search_volume") or 0 for m in months][-12:],
        "group": group,  # match : contient la recherche ; related : mot-clé associé
    }
