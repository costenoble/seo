"""Analyse du trafic GA4 : indicateurs comparés, canaux, pages d'entrée depuis Google croisées avec la
Search Console et l'audit, et recommandations (pages qui attirent sans convertir, visiteurs qui repartent vite)."""
from collections import defaultdict
from urllib.parse import urlsplit

ORGANIC = "Organic Search"
CHANNEL_FR = {"Organic Search": "Recherche Google (naturel)", "Direct": "Accès direct", "Referral": "Sites référents",
              "Organic Social": "Réseaux sociaux", "Paid Search": "Annonces Google", "Email": "E-mail",
              "Paid Social": "Réseaux sociaux (payant)", "Unassigned": "Non attribué", "Organic Video": "Vidéo",
              "Display": "Display", "Cross-network": "Multi-réseaux", "Affiliates": "Affiliation"}


def _pct(cur, prev):
    return round((cur - prev) / prev * 100) if prev else None


def _path(url: str) -> str:
    parts = urlsplit(url if "://" in url else "https://x" + (url if url.startswith("/") else "/" + url))
    return (parts.path or "/").rstrip("/") or "/"


def build(raw: dict, gsc_pages: list[dict] | None = None, audit: dict | None = None) -> dict:
    cur, prev = raw.get("current", {}), raw.get("previous", {})
    kpis = {k: {"value": cur.get(k, 0), "change": _pct(cur.get(k, 0), prev.get(k, 0))}
            for k in ("totalUsers", "sessions", "keyEvents", "engagementRate")}
    kpis["engagementRate"]["change"] = (round((cur.get("engagementRate", 0) - prev.get("engagementRate", 0)) * 100, 1)
                                        if prev.get("engagementRate") is not None else None)  # en points

    ch = defaultdict(lambda: {"sessions": 0, "previous": 0, "keyEvents": 0})
    for r in raw.get("channels", []):
        key = r.get("sessionDefaultChannelGroup", "Unassigned")
        if r.get("dateRange", "date_range_0") == "date_range_0":
            ch[key]["sessions"] += r["sessions"]; ch[key]["keyEvents"] += r.get("keyEvents", 0)
        else:
            ch[key]["previous"] += r["sessions"]
    total = sum(c["sessions"] for c in ch.values()) or 1
    channels = sorted(({"channel": k, "label": CHANNEL_FR.get(k, k), "sessions": v["sessions"], "share": round(v["sessions"] / total * 100, 1),
                        "change": _pct(v["sessions"], v["previous"]), "keyEvents": v["keyEvents"]} for k, v in ch.items()),
                      key=lambda c: -c["sessions"])

    by_day = defaultdict(lambda: {"organic": 0, "other": 0})
    for r in raw.get("daily", []):
        d = r.get("date", "")
        by_day[d]["organic" if r.get("sessionDefaultChannelGroup") == ORGANIC else "other"] += r["sessions"]
    daily = [{"date": f"{d[:4]}-{d[4:6]}-{d[6:]}", **v} for d, v in sorted(by_day.items()) if len(d) == 8]

    gsc_by_path = defaultdict(lambda: {"clicks": 0, "impressions": 0, "pos_w": 0})
    for p in gsc_pages or []:
        g = gsc_by_path[_path(p["page"])]
        g["clicks"] += p["clicks"]; g["impressions"] += p["impressions"]; g["pos_w"] += p["position"] * p["impressions"]
    issues_by_path = defaultdict(int)
    for i in (audit or {}).get("issues", []):
        if i.get("severity") in ("critical", "high", "medium"):
            issues_by_path[_path(i["url"])] += 1
    organic_total = sum(p["sessions"] for p in raw.get("organic_pages", [])) or 0
    pages = []
    for p in raw.get("organic_pages", []):
        path = _path(p.get("landingPage") or "/")
        g = gsc_by_path.get(path)
        pages.append({"page": p.get("landingPage") or "/", "sessions": p["sessions"], "keyEvents": p.get("keyEvents", 0),
                      "engagementRate": round(p.get("engagementRate", 0) * 100, 1),
                      "gsc_clicks": g["clicks"] if g else None,
                      "gsc_position": round(g["pos_w"] / g["impressions"], 1) if g and g["impressions"] else None,
                      "issues": issues_by_path.get(path, 0)})

    insights, threshold = [], max(20, organic_total * .05)
    organic = next((c for c in channels if c["channel"] == ORGANIC), None)
    if organic:
        tone = "good" if (organic["change"] or 0) >= 0 else "medium"
        insights.append({"priority": tone, "title": f"Google vous apporte {organic['share']} % de vos visites",
                         "text": f"{round(organic['sessions'])} sessions depuis la recherche naturelle sur {raw.get('days', 28)} jours"
                                 + (f", {'+' if organic['change'] > 0 else ''}{organic['change']} % par rapport à la période précédente." if organic["change"] is not None else "."),
                         "action": "" if tone == "good" else "Ouvrez le plan d'action : les tâches de priorité haute sont les plus susceptibles de faire remonter ce trafic."})
    if not cur.get("keyEvents"):
        insights.append({"priority": "medium", "title": "Aucune conversion mesurée",
                         "text": "GA4 n'a enregistré aucun événement clé (inscription, demande de devis, achat…) sur la période.",
                         "action": "Dans GA4, marquez comme « événement clé » les actions qui comptent pour vous : Vigie SEO pourra alors repérer les pages qui convertissent."})
    else:
        dead = [p for p in pages if p["sessions"] >= threshold and not p["keyEvents"]]
        if dead:
            insights.append({"priority": "high", "title": f"{len(dead)} page(s) attirent du trafic Google sans convertir",
                             "text": "Ces pages reçoivent des visiteurs depuis Google, mais aucun ne réalise d'action clé.",
                             "action": "Ajoutez un appel à l'action clair (essai, devis, contact) en haut de ces pages et vérifiez qu'elles répondent bien à la recherche.",
                             "pages": [p["page"] for p in dead[:6]]})
    bounce = [p for p in pages if p["sessions"] >= threshold and p["engagementRate"] < 40]
    if bounce:
        insights.append({"priority": "medium", "title": f"{len(bounce)} page(s) où les visiteurs repartent vite",
                         "text": "Moins de 40 % des visites venues de Google sont « engagées » (plus de 10 secondes, 2 pages ou une conversion).",
                         "action": "Vérifiez que le haut de page répond immédiatement à la recherche, accélérez le chargement et ajoutez des liens vers vos pages clés.",
                         "pages": [p["page"] for p in bounce[:6]]})
    with_issues = [p for p in pages[:15] if p["issues"]]
    if with_issues:
        insights.append({"priority": "high", "title": "Des problèmes d'audit sur vos pages qui reçoivent du trafic",
                         "text": f"{len(with_issues)} de vos pages d'entrée les plus visitées depuis Google ont des problèmes relevés par le dernier audit.",
                         "action": "Corrigez-les en premier : ce sont les pages où chaque amélioration compte le plus.",
                         "pages": [p["page"] for p in with_issues[:6]]})
    order = {"high": 0, "medium": 1, "good": 2}
    insights.sort(key=lambda i: order.get(i["priority"], 3))
    return {"days": raw.get("days", 28), "kpis": kpis, "channels": channels, "daily": daily, "pages": pages, "insights": insights}
