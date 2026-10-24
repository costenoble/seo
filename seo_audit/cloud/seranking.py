"""Données de domaines via l'API Data de SE Ranking : trafic estimé, mots-clés positionnés, concurrents
organiques, écart de mots-clés (keyword gap) et backlinks. Authentification : en-tête « Token <clé> »."""
import time

import httpx

API = "https://api.seranking.com/v1/"
# codes de lieu Google Ads (utilisés dans l'app) → bases SE Ranking
SOURCES = {2250: "fr", 2056: "be", 2756: "ch", 2124: "ca"}
INTENTS = {"I": "info", "C": "commercial", "T": "transactionnel", "N": "navigation", "L": "local"}


class SERankingError(Exception):
    pass


class SERanking:
    def __init__(self, key: str, timeout: float = 60):
        self.headers, self.timeout = {"Authorization": f"Token {key}"}, timeout

    def _get(self, path: str, params: dict, retries: int = 3):
        for attempt in range(retries):
            try:
                r = httpx.get(API + path, headers=self.headers, params=params, timeout=self.timeout)
            except httpx.HTTPError as exc:
                raise SERankingError(f"Service de données injoignable : {type(exc).__name__}") from exc
            if r.status_code in (429, 500) and "too many" in r.text.lower() and attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1))  # limite d'appels par seconde
                continue
            if r.status_code in (401, 403):
                raise SERankingError("Clé SE Ranking refusée ou expirée")
            if r.status_code == 402 or "units" in r.text.lower() and r.status_code >= 400:
                raise SERankingError("Crédits SE Ranking épuisés")
            if r.status_code >= 400:
                raise SERankingError(f"Erreur SE Ranking ({r.status_code})")
            return r.json()
        raise SERankingError("SE Ranking est saturé, réessayez dans un instant")

    def overview(self, domain: str, source: str) -> dict:
        d = self._get("domain/overview/db", {"source": source, "domain": domain}).get("organic") or {}
        return {"keywords": d.get("keywords_count", 0), "traffic": d.get("traffic_sum", 0), "value": d.get("price_sum", 0),
                "new": d.get("keywords_new_count", 0), "lost": d.get("keywords_lost_count", 0),
                "up": d.get("keywords_up_count", 0), "down": d.get("keywords_down_count", 0),
                "distribution": [d.get("top1_5", 0), d.get("top6_10", 0), d.get("top11_20", 0),
                                 d.get("top21_50", 0), d.get("top51_100", 0)]}

    def keywords(self, domain: str, source: str, limit: int = 100) -> list[dict]:
        rows = self._get("domain/keywords", {"source": source, "domain": domain, "limit": limit,
                                             "order_field": "traffic", "order_type": "desc"})
        return [keyword_row(r) for r in rows or []]

    def competitors(self, domain: str, source: str, limit: int = 15) -> list[dict]:
        rows = self._get("domain/competitors", {"source": source, "domain": domain, "type": "organic"}) or []
        return [{"domain": r["domain"], "common": r.get("common_keywords", 0), "missing": r.get("missing_keywords", 0),
                 "keywords": r.get("total_keywords", 0), "traffic": r.get("traffic_sum", 0)} for r in rows[:limit]]

    def gap(self, competitor: str, mine: str, source: str, limit: int = 100) -> list[dict]:
        """Mots-clés où `competitor` est positionné et `mine` ne l'est pas, par volume décroissant."""
        rows = self._get("domain/keywords/comparison", {"source": source, "domain": competitor, "compare": mine,
                                                        "diff": 1, "limit": limit, "order_field": "volume",
                                                        "order_type": "desc"})
        return [keyword_row(r) for r in rows or []]

    def backlinks(self, domain: str) -> dict:
        s = (self._get("backlinks/summary", {"target": domain}).get("summary") or [{}])[0]
        return {"backlinks": s.get("backlinks", 0), "refdomains": s.get("refdomains", 0),
                "dofollow_refdomains": s.get("dofollow_refdomains", 0), "authority": s.get("domain_inlink_rank", 0),
                "dofollow_pct": round(100 * s.get("dofollow_backlinks", 0) / s["backlinks"]) if s.get("backlinks") else 0,
                "anchors": [[a["anchor"], a.get("refdomains", a.get("backlinks", 0))]
                            for a in (s.get("top_anchors_by_refdomains") or s.get("top_anchors_by_backlinks") or [])[:10]],
                "pages": [[p.get("url"), p.get("refdomains", p.get("backlinks", 0))]
                          for p in (s.get("top_pages_by_refdomains") or s.get("top_pages_by_backlinks") or [])[:10]]}


def keyword_row(r: dict) -> dict:
    return {"keyword": r.get("keyword"), "position": r.get("position"), "prev": r.get("prev_pos"),
            "volume": r.get("volume"), "cpc": r.get("cpc"), "difficulty": r.get("difficulty"),
            "traffic": r.get("traffic"), "url": r.get("url"),
            "intent": INTENTS.get((r.get("intents") or [None])[0]),
            "features": r.get("serp_features") or [], "their_position": r.get("compare_position")}
