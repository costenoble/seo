"""Analyse automatique des données Search Console : recommandations classées par priorité.

Entrées (90 derniers jours) : requêtes agrégées, couples requête × page, pages, et le dernier audit du site.
Tout est calculé par des règles explicites ; chaque recommandation cite les chiffres qui la justifient."""
import re
import unicodedata
from collections import defaultdict
from urllib.parse import urlsplit

# Taux de clic moyen observé selon la position (ordre de grandeur, toutes thématiques confondues)
EXPECTED_CTR = {1: .28, 2: .15, 3: .10, 4: .07, 5: .05, 6: .04, 7: .03, 8: .025, 9: .02, 10: .02}
UTILITY_RE = re.compile(r"(login|log-in|signin|sign-in|connexion|register|inscription|signup|sign-up|"
                        r"mon-compte|my-account|account|panier|cart|checkout|wp-admin|wp-login|"
                        r"password|mot-de-passe|reset|[?&](s|q|search|tab|ref|sort|page)=)", re.I)
TITLE_ISSUES = {"title_too_long": "title trop long", "title_too_short": "title trop court",
                "missing_title": "title absent", "duplicate_title": "title en double",
                "missing_description": "description absente", "description_too_long": "description trop longue",
                "description_too_short": "description trop courte", "duplicate_description": "description en double",
                "thin_content": "contenu faible"}
PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2, "good": 3}


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _plain(text))


def _lev(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def brand_of(site: str) -> str:
    """sc-domain:kompta.pro → kompta ; https://www.vigie-seo.fr/ → vigieseo."""
    host = site.split(":", 1)[1] if site.startswith("sc-domain:") else (urlsplit(site).hostname or site)
    host = host.lower().removeprefix("www.")
    return _squash(host.split(".")[0])


def _path(url: str) -> str:
    parts = urlsplit(url)
    return (parts.path or "/") + (f"?{parts.query}" if parts.query else "")


def _wavg(rows, key="position", weight="impressions"):
    w = sum(r[weight] for r in rows)
    return sum(r[key] * r[weight] for r in rows) / w if w else None


def _fmt_pos(p: float) -> str:
    return f"{p:.1f}".replace(".", ",")


def _insight(id_, priority, title, text, action, columns=None, rows=None, chips=None, targets=None):
    """targets : éléments concrets à traiter (deviennent des tâches du plan d'action) :
    {key, title, detail?, pages, queries} — queries sert à mesurer l'effet une fois la tâche faite."""
    return {"id": id_, "priority": priority, "title": title, "text": text, "action": action,
            "table": {"columns": columns, "rows": rows} if rows else None, "chips": chips or [],
            "targets": targets or []}


def _target(key, title, detail="", pages=(), queries=()):
    return {"key": key, "title": title, "detail": detail, "pages": list(pages), "queries": list(queries)}


def analyze(site: str, queries: list[dict], query_pages: list[dict], pages: list[dict],
            audit: dict | None = None, recent: list[dict] | None = None) -> dict:
    """queries : [{query, clicks, impressions, position}] sur 90 jours ; query_pages : [{query, page, …}] ;
    pages : [{page, …}] ; audit : {"issues": [...], "pages": [...]} du dernier audit ; recent : requêtes
    sur 28 jours avec previous_position (positions_summary)."""
    total_imp = sum(q["impressions"] for q in queries)
    total_clicks = sum(q["clicks"] for q in queries)
    overview = {"days": 90, "clicks": round(total_clicks), "impressions": round(total_imp),
                "position": round(_wavg(queries), 1) if queries else None, "queries": len(queries),
                "has_pages": bool(pages)}
    min_imp = max(5, round(total_imp * 0.003))
    audit_by_url = defaultdict(set)
    for i in (audit or {}).get("issues", []):
        if i["code"] in TITLE_ISSUES:
            audit_by_url[_path(i["url"])].add(TITLE_ISSUES[i["code"]])
    out = []
    if total_imp < 50:
        return {"overview": overview, "insights": [], "note": "Pas encore assez de données pour une analyse "
                "(moins de 50 impressions sur 90 jours). Revenez quand le site aura un peu plus de visibilité."}

    # 1. La marque -------------------------------------------------------------------------------
    brand = brand_of(site)
    near = []
    if len(brand) >= 3:
        exact = [q for q in queries if brand in _squash(q["query"])]
        tol = 1 if len(brand) <= 5 else 2
        # variantes et fautes de frappe du nom : recherches courtes (1 ou 2 mots) dont un mot est proche du nom
        near = [q for q in queries if q not in exact and len(re.findall(r"[a-z0-9]+", _plain(q["query"]))) <= 2 and any(
            len(t) >= 3 and t != brand and _lev(t, brand) <= tol for t in re.findall(r"[a-z0-9]+", _plain(q["query"])))]
        near.sort(key=lambda q: -q["impressions"])
        if exact:
            pos = _wavg(exact)
            imp, clk = sum(q["impressions"] for q in exact), sum(q["clicks"] for q in exact)
            home_title = next((r.get("title") or "" for r in (audit or {}).get("pages", [])
                               if _path(r["url"]) == "/"), None)
            title_note = ""
            if home_title is not None:
                title_note = (" Le nom est déjà en tête du titre de votre page d'accueil, c'est bien." if
                              _squash(home_title).startswith(brand) else
                              f" Placez « {brand} » au début du titre de la page d'accueil (actuellement : « {home_title} »).")
            if pos > 1.5:
                text = (f"Sur les recherches de votre nom, vous êtes en moyenne {_fmt_pos(pos)}e "
                        f"({round(imp)} impressions, {round(clk)} clics). Sur votre propre nom, vous devriez être 1er.")
                if near:
                    text += (f" Des recherches proches ({', '.join(q['query'] for q in near[:5])}…) font aussi apparaître "
                             "votre site : d'autres acteurs portent un nom voisin.")
                out.append(_insight("brand", "high", "Votre marque n'est pas en 1re position", text,
                                    "Créez une fiche Google Business Profile, obtenez des liens vers votre site depuis "
                                    "des annuaires professionnels et vos réseaux sociais (LinkedIn…)." + title_note,
                                    chips=[q["query"] for q in near[:12]],
                                    targets=[_target("brand", f"Passer 1er sur « {exact[0]['query']} »", text,
                                                     queries=[q["query"] for q in exact])]))
            else:
                out.append(_insight("brand", "good", "Votre marque est bien en 1re position",
                                    f"Position moyenne {_fmt_pos(pos)} sur votre nom ({round(clk)} clics).", ""))

    # 2. Technique : versions du site et pages inutiles indexées ------------------------------------
    if pages:
        schemes = {urlsplit(p["page"]).scheme for p in pages}
        hosts = {(urlsplit(p["page"]).hostname or "").startswith("www.") for p in pages}
        problems, examples = [], []
        if len(schemes) > 1:
            problems.append("en http et en https")
            examples += [p["page"] for p in pages if p["page"].startswith("http://")][:3]
        if len(hosts) > 1:
            problems.append("avec et sans www")
        utility = sorted((p for p in pages if UTILITY_RE.search(_path(p["page"]))), key=lambda p: -p["impressions"])
        if problems or utility:
            text, action = [], []
            if problems:
                text.append(f"Google affiche votre site {' et '.join(problems)} : il voit deux versions de chaque page "
                            f"(par exemple {', '.join(examples) or pages[0]['page']}).")
                action.append("Mettez en place une redirection 301 vers une seule version (https, avec ou sans www) : "
                              "c'est souvent une simple option chez votre hébergeur.")
            if utility:
                text.append(f"{len(utility)} page(s) sans intérêt pour la recherche sont indexées "
                            f"({', '.join(_path(p['page']) for p in utility[:4])}).")
                action.append("Ajoutez-leur une balise noindex (connexion, inscription, compte, panier, recherche interne).")
            targets = []
            if problems:
                targets.append(_target("versions", "Rediriger toutes les versions du site vers une seule (301)",
                                       text[0], pages=examples))
            if utility:
                targets.append(_target("noindex-utility", "Désindexer les pages de connexion et de compte",
                                       text[-1], pages=[p["page"] for p in utility[:20]]))
            out.append(_insight("technical", "high", "Un problème technique facile à régler", " ".join(text),
                                " ".join(action), ["Page", "Impressions", "Position"],
                                [[p["page"], round(p["impressions"]), round(p["position"], 1)]
                                 for p in (utility[:8] if utility else [])], targets=targets))

    # 3. Presque en page 1 (positions 4 à 15) ----------------------------------------------------
    # sans les recherches de la marque ni les pages à désindexer, déjà traitées plus haut
    brand_queries = {q["query"] for q in queries if len(brand) >= 3 and (brand in _squash(q["query"]) or q in near)} \
        if len(brand) >= 3 else set()
    source = query_pages or [{**q, "page": ""} for q in queries]
    by_page = defaultdict(list)
    for r in source:
        if r["query"] in brand_queries or (r["page"] and UTILITY_RE.search(_path(r["page"]))):
            continue
        if 3.5 < r["position"] <= 15:
            by_page[r["page"]].append(r)
    wins = []
    for page, rows in by_page.items():
        imp = sum(r["impressions"] for r in rows)
        if imp < min_imp * 2:
            continue
        rows.sort(key=lambda r: -r["impressions"])
        pos, clk = _wavg(rows), sum(r["clicks"] for r in rows)
        expected = EXPECTED_CTR.get(round(pos), .01)
        todo = ("Réécrire le titre et la description affichés dans Google" if clk / imp < expected * .5 and pos <= 10
                else "Enrichir la page : définition en haut, FAQ, exemples")
        if page and audit_by_url.get(_path(page)):
            todo += f" (l'audit signale : {', '.join(sorted(audit_by_url[_path(page)]))})"
        lo, hi = min(r["position"] for r in rows), max(r["position"] for r in rows)
        label = _path(page) if page else rows[0]["query"]
        wins.append((imp, [label, f"{round(lo)} à {round(hi)}" if round(lo) != round(hi) else str(round(lo)),
                           round(imp), round(clk), ", ".join(r["query"] for r in rows[:5]), todo],
                     _target("quick:" + label, f"Optimiser {label} (presque en page 1)",
                             f"Positions {round(lo)} à {round(hi)}, {round(imp)} impressions, {round(clk)} clics. {todo}.",
                             pages=[page] if page else [], queries=[r["query"] for r in rows])))
    if wins:
        wins.sort(key=lambda w: -w[0])
        out.append(_insight("quick_wins", "high", "Presque en page 1 : des gains rapides",
                            f"{len(wins)} page(s) sont entre la 4e et la 15e position. Quelques améliorations peuvent "
                            "suffire à les faire entrer dans le haut de la page 1, où se font la plupart des clics.",
                            "Commencez par la page qui a le plus d'impressions.",
                            ["Page", "Positions", "Impressions", "Clics", "Requêtes", "À faire"],
                            [w[1] for w in wins[:6]], targets=[w[2] for w in wins[:6]]))

    # 4. Proches de la page 2 (positions 15 à 30) ------------------------------------------------
    page_of = {}
    for r in sorted(query_pages, key=lambda r: -r["impressions"]):
        page_of.setdefault(r["query"], r["page"])
    near2 = sorted((q for q in queries if 15 < q["position"] <= 30 and q["impressions"] >= min_imp),
                   key=lambda q: -q["impressions"])
    if near2:
        out.append(_insight("page_two", "medium", "Proches de la page 2",
                            f"{len(near2)} requête(s) entre la 16e et la 30e position. Google associe déjà votre site à ces sujets.",
                            "Une page ou un article dédié à chacune peut suffire à les faire monter.",
                            ["Requête", "Position", "Impressions", "Page actuelle"],
                            [[q["query"], round(q["position"], 1), round(q["impressions"]),
                              _path(page_of[q["query"]]) if q["query"] in page_of else "–"] for q in near2[:8]],
                            targets=[_target("page2:" + q["query"], f"Créer un contenu dédié à « {q['query']} »",
                                             f"Position {_fmt_pos(q['position'])}, {round(q['impressions'])} impressions.",
                                             pages=[page_of[q["query"]]] if q["query"] in page_of else [],
                                             queries=[q["query"]]) for q in near2[:5]]))

    # 5. Gros potentiel : beaucoup d'impressions, très loin ----------------------------------------
    far_source = pages or []
    far = sorted((p for p in far_source if p["position"] > 30 and p["impressions"] >= max(min_imp * 3, 30)),
                 key=lambda p: -p["impressions"])
    if far:
        family = defaultdict(list)
        for p in far_source:
            seg = _path(p["page"]).strip("/").split("/")[-1]
            if "-" in seg:
                family[seg.rsplit("-", 1)[0]].append(p)
        fam = max(family.items(), key=lambda kv: (len(kv[1]), sum(p["impressions"] for p in kv[1])), default=None)
        fam_txt = (f" Vos pages « {fam[0]}-… » sont dans ce cas : Google comprend le sujet, mais elles ne sont pas "
                   "assez fortes face aux concurrents." if fam and len(fam[1]) >= 3 else "")
        top = _path(far[0]["page"])
        out.append(_insight("potential", "medium", "Le gros potentiel : des pages vues mais très loin",
                            f"{len(far)} page(s) reçoivent beaucoup d'impressions au-delà de la 30e position." + fam_txt,
                            f"Commencez par {top} (le plus d'impressions) : rendez-la vraiment spécifique (exemples concrets, "
                            "FAQ, captures d'écran, témoignages), faites des liens vers elle depuis l'accueil et le blog, "
                            "puis reproduisez la méthode sur les suivantes.",
                            ["Page", "Impressions", "Position"],
                            [[_path(p["page"]), round(p["impressions"]), round(p["position"])] for p in far[:8]],
                            targets=[_target("potential:" + _path(p["page"]), f"Renforcer {_path(p['page'])}",
                                             f"{round(p['impressions'])} impressions en position {round(p['position'])}.",
                                             pages=[p["page"]],
                                             queries=[r["query"] for r in query_pages if r["page"] == p["page"]])
                                     for p in far[:3]]))
    elif not pages:
        far_q = sorted((q for q in queries if q["position"] > 30 and q["impressions"] >= max(min_imp * 3, 30)),
                       key=lambda q: -q["impressions"])
        if far_q:
            out.append(_insight("potential", "medium", "Le gros potentiel : des recherches vues mais très loin",
                                f"{len(far_q)} requête(s) avec beaucoup d'impressions au-delà de la 30e position.",
                                "Renforcez les pages qui visent ces recherches (contenu spécifique, FAQ, liens internes).",
                                ["Requête", "Impressions", "Position"],
                                [[q["query"], round(q["impressions"]), round(q["position"])] for q in far_q[:8]]))

    # 6. Pages en concurrence sur la même requête ---------------------------------------------------
    by_query = defaultdict(list)
    for r in query_pages:
        by_query[r["query"]].append(r)
    canni = []
    for q, rows in by_query.items():
        imp = sum(r["impressions"] for r in rows)
        paths = {urlsplit(r["page"]).path for r in rows if r["impressions"] >= imp * .2}
        if imp >= min_imp * 2 and len(paths) >= 2:
            canni.append([q, round(imp), " / ".join(sorted(paths)[:3]), sorted({r["page"] for r in rows})])
    if canni:
        canni.sort(key=lambda r: -r[1])
        out.append(_insight("cannibalization", "low", "Plusieurs pages en concurrence",
                            f"Sur {len(canni)} requête(s), plusieurs de vos pages se partagent les impressions : "
                            "Google hésite et aucune ne monte vraiment.",
                            "Choisissez la page principale pour chaque requête, renforcez-la et faites pointer les autres vers elle.",
                            ["Requête", "Impressions", "Pages"], [c[:3] for c in canni[:6]],
                            targets=[_target("canni:" + c[0], f"Choisir une page principale pour « {c[0]} »",
                                             f"{c[1]} impressions partagées entre {c[2]}.", pages=c[3], queries=[c[0]])
                                     for c in canni[:3]]))

    # 7. Évolution sur 28 jours ---------------------------------------------------------------------
    if recent:
        falls = sorted((q for q in recent if q["previous_position"] and q["position"]
                        and q["position"] - q["previous_position"] >= 3 and q["impressions"] >= min_imp),
                       key=lambda q: -q["impressions"])
        if falls:
            out.append(_insight("drops", "medium", "En recul ces 4 dernières semaines",
                                f"{len(falls)} requête(s) ont perdu au moins 3 places par rapport aux 4 semaines précédentes.",
                                "Vérifiez que ces pages n'ont pas changé (titre, contenu, redirection) et regardez qui vous a dépassé.",
                                ["Requête", "Avant", "Maintenant", "Impressions"],
                                [[q["query"], q["previous_position"], q["position"], round(q["impressions"])] for q in falls[:6]],
                                targets=[_target("drop:" + q["query"], f"Comprendre le recul sur « {q['query']} »",
                                                 f"Position {_fmt_pos(q['previous_position'])} → {_fmt_pos(q['position'])} "
                                                 "en 4 semaines.", queries=[q["query"]]) for q in falls[:3]]))

    out.sort(key=lambda i: PRIORITY_ORDER[i["priority"]])
    return {"overview": overview, "insights": out, "note": None if pages else
            "Analyse par page indisponible : relancez l'import de cette propriété pour l'activer."}
