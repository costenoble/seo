"""Conseils personnalisés : Claude lit les données Search Console (et l'audit) d'un site et dit quoi faire, dans l'ordre.

Le texte est destiné à quelqu'un qui n'est pas spécialiste : chaque conseil explique pourquoi et comment,
avec les requêtes et la page concernées. Le résultat est mis en cache 12 heures par utilisateur et propriété."""
import json
from collections import Counter

import anthropic

from .fixer import MODEL
from .insights import brand_of

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "advice": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "why": {"type": "string"},
                "steps": {"type": "array", "items": {"type": "string"}},
                "queries": {"type": "array", "items": {"type": "string"}},
                "page": {"type": "string"},
                "impact": {"type": "string", "enum": ["fort", "moyen", "faible"]},
                "effort": {"type": "string", "enum": ["rapide", "moyen", "long"]},
            },
            "required": ["title", "why", "steps", "queries", "page", "impact", "effort"], "additionalProperties": False}},
    },
    "required": ["summary", "advice"], "additionalProperties": False,
}

SYSTEM = """Tu es un consultant SEO bienveillant qui conseille le propriétaire d'un site, qui n'est pas spécialiste.
À partir de ses vraies données Google Search Console (et de l'audit technique s'il est fourni), donne 4 à 6 conseils
concrets, du plus rentable au moins rentable.

Règles :
- Écris en français simple, sans jargon (ou explique-le en quelques mots : « CTR, le pourcentage de personnes qui cliquent »).
- Chaque conseil s'appuie sur des chiffres réels fournis (requêtes, positions, impressions) : cite-les.
- "why" : pourquoi c'est important pour CE site, en 2 ou 3 phrases. "steps" : 2 à 4 actions concrètes, dans l'ordre.
- "page" : l'adresse de la page à travailler si elle est connue, sinon chaîne vide. "queries" : les requêtes concernées.
- Distingue la marque (recherches du nom du site) des recherches de clients qui ne connaissent pas encore le site.
- Positions : 1 à 3 = excellent ; 4 à 10 = page 1 ; 11 à 20 = page 2, tout proche ; au-delà de 30 = Google connaît
  la page mais la juge moins pertinente que les concurrents.
- N'invente aucun chiffre ni aucune information sur l'entreprise. Pas de promesses de résultat.
- "summary" : 2 phrases qui résument la situation du site et la priorité n° 1."""


def build_prompt(site: str, queries: list[dict], query_pages: list[dict], audit: dict | None, insights: list[dict]) -> str:
    brand = brand_of(site)
    top = sorted(queries, key=lambda q: -q["impressions"])[:40]
    best_page = {}
    for qp in sorted(query_pages, key=lambda r: -r["impressions"]):
        best_page.setdefault(qp["query"], qp["page"])
    lines = [f"Site : {site} (marque : « {brand} »)", "",
             "Requêtes des 28 derniers jours (requête | clics | impressions | CTR | position | évolution sur 4 semaines | page).",
             "Une position plus petite est meilleure : passer de 4,4 à 3,4 est une PROGRESSION d'une place.", ""]

    def trend(q):
        before, now = q.get("previous_position"), q["position"]
        if before is None or now is None:
            return "nouvelle requête"
        gain = round(before - now, 1)
        return "stable" if abs(gain) < 0.5 else (f"progression de {gain} places (avant {before})" if gain > 0
                                                 else f"recul de {-gain} places (avant {before})")
    for q in top:
        lines.append(f"- {q['query']} | {q['clicks']} | {q['impressions']} | {q['ctr'] * 100:.1f} % | {q['position']}"
                     f" | {trend(q)} | {best_page.get(q['query'], '')}")
    total_clicks, total_imp = sum(q["clicks"] for q in queries), sum(q["impressions"] for q in queries)
    lines += ["", f"Total : {total_clicks} clics, {total_imp} impressions, {len(queries)} requêtes."]
    if insights:
        lines += ["", "Constats déjà calculés par Vigie SEO :"] + [f"- {i['title']}" for i in insights if i.get("priority") != "good"][:8]
    if audit and audit.get("issues"):
        counts = Counter(i["message"].split(" (")[0] for i in audit["issues"])
        lines += ["", "Audit technique (problèmes les plus fréquents) :"] + [f"- {m} : {n} page(s)" for m, n in counts.most_common(8)]
    return "\n".join(lines)


def advise(api_key: str, prompt: str, model: str = MODEL) -> dict:
    client = anthropic.Anthropic(api_key=api_key, timeout=55, max_retries=1)
    request = dict(model=model, max_tokens=8000, system=SYSTEM, messages=[{"role": "user", "content": prompt}],
                   output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}})
    if model.startswith("claude-haiku"):  # Haiku n'a pas de repli côté serveur
        response = client.messages.create(**request)
    else:
        response = client.beta.messages.create(**request, betas=["server-side-fallback-2026-07-01"], fallbacks="default")
    if response.stop_reason == "refusal":
        raise ValueError("L'IA n'a pas pu analyser ces données")
    if response.stop_reason == "max_tokens":
        raise ValueError("Analyse trop longue, réessayez")
    return json.loads(next((b.text for b in response.content if b.type == "text"), "{}"))
