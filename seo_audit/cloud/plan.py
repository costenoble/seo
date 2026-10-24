"""Plan d'action : tâches issues du dernier audit et de l'analyse Search Console, et mesure de leur effet."""
from collections import defaultdict

PRIORITY = {"critical": "high", "high": "high", "medium": "medium", "low": "low"}
# Conseil concret par type de problème d'audit (les autres reçoivent un conseil générique)
ACTIONS = {
    "fetch_error": "Vérifiez que ces pages répondent (serveur, pare-feu, certificat) ; sinon retirez les liens qui y mènent.",
    "http_5xx": "Erreurs serveur : regardez les journaux de votre hébergeur ; Google réduit l'exploration d'un site instable.",
    "http_4xx": "Rétablissez ces pages ou redirigez-les (301) vers la page la plus proche, puis corrigez les liens qui y mènent.",
    "redirect_loop": "Cassez la boucle : chaque URL doit rediriger une seule fois vers une page finale en 200.",
    "redirect_chain": "Faites pointer la première URL directement vers la destination finale (une seule redirection).",
    "temporary_redirect": "Remplacez ces redirections 302/307 par des 301 si le changement est définitif.",
    "sitemap_non_200": "Retirez du sitemap les URLs redirigées ou supprimées : il ne doit contenir que des pages en 200.",
    "sitemap_noindex": "Choisissez : retirez ces pages du sitemap, ou retirez leur noindex si elles doivent être trouvées.",
    "sitemap_blocked": "Débloquez ces pages dans robots.txt ou retirez-les du sitemap.",
    "slow_ttfb": "Activez un cache de pages et vérifiez l'hébergement : le serveur met trop de temps à répondre.",
    "noindex": "Vérifiez que ces pages doivent vraiment rester hors de Google.",
    "missing_canonical": "Ajoutez une balise canonical pointant vers l'URL de la page elle-même.",
    "multiple_canonicals": "Ne gardez qu'une seule balise canonical par page.",
    "canonical_to_non_indexable": "Faites pointer la canonical vers une page qui répond en 200 et qui est indexable.",
    "missing_title": "Rédigez un title unique de 30 à 60 caractères, avec le sujet principal au début.",
    "multiple_titles": "Supprimez les balises title en double (souvent un thème et une extension qui en ajoutent chacun une).",
    "title_too_long": "Raccourcissez à 60 caractères environ : Google coupe le reste dans ses résultats.",
    "title_too_short": "Allongez le title (30 à 60 caractères) en précisant le sujet et le bénéfice.",
    "duplicate_title": "Donnez à chaque page un title différent qui décrit son contenu propre.",
    "missing_description": "Rédigez une meta description de 120 à 160 caractères qui donne envie de cliquer.",
    "multiple_descriptions": "Ne gardez qu'une seule meta description par page.",
    "description_too_long": "Raccourcissez à 160 caractères environ.",
    "description_too_short": "Allongez à 120-160 caractères : bénéfice, preuve, appel à l'action.",
    "duplicate_description": "Écrivez une description propre à chaque page.",
    "missing_h1": "Ajoutez un titre H1 visible qui reprend le sujet de la page.",
    "multiple_h1": "Gardez un seul H1 par page ; passez les autres en H2.",
    "missing_lang": "Ajoutez l'attribut lang sur la balise <html> (par exemple lang=\"fr\").",
    "missing_viewport": "Ajoutez <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"> pour le mobile.",
    "images_missing_alt": "Ajoutez un texte alternatif (alt) qui décrit chaque image.",
    "thin_content": "Étoffez ces pages (au moins 300 mots utiles) ou regroupez-les avec des pages proches.",
    "duplicate_content": "Fusionnez ces pages ou ajoutez une canonical vers la version principale.",
    "near_duplicate_content": "Différenciez ces pages (contenu propre à chacune) ou fusionnez-les.",
    "broken_internal_link": "Corrigez ou supprimez ces liens internes : ils mènent à des pages en erreur.",
    "linked_redirect": "Remplacez ces liens par l'URL finale pour éviter une redirection à chaque clic.",
    "broken_external_link": "Mettez à jour ou supprimez ces liens vers d'autres sites.",
    "orphan_page": "Ajoutez des liens vers ces pages depuis le menu, l'accueil ou des articles proches.",
    "deep_page": "Rapprochez ces pages de l'accueil (3 clics au plus) avec des liens depuis les rubriques.",
    "hreflang_missing_return": "Chaque page liée en hreflang doit renvoyer vers celle-ci (liens réciproques).",
    "hreflang_missing_self": "Ajoutez un hreflang qui pointe vers la page elle-même.",
    "hreflang_invalid_code": "Utilisez des codes valides : langue ISO 639-1, éventuellement suivie du pays (fr, fr-BE, en-GB).",
    "jsonld_invalid": "Corrigez la syntaxe JSON-LD (testez avec l'outil Résultats enrichis de Google).",
    "jsonld_missing_property": "Complétez les propriétés obligatoires des données structurées.",
    "js_dependent_seo": "Rendez ces éléments dans le HTML envoyé par le serveur, sans attendre le JavaScript.",
    "perf_poor": "Allégez les images (WebP, dimensions), différez les scripts et activez le cache.",
    "gsc_not_indexed": "Demandez l'indexation dans la Search Console et ajoutez des liens internes vers ces pages.",
}


def tasks_from_audit(report: dict) -> list[dict]:
    groups = defaultdict(list)
    for i in report.get("issues", []):
        if i["severity"] in PRIORITY:
            groups[i["code"]].append(i)
    tasks = []
    for code, items in groups.items():
        base = items[0]["message"].split(" (")[0].split(" : ")[0]
        urls = sorted({i["url"] for i in items})
        tasks.append({
            "fingerprint": f"audit:{code}", "source": "audit", "priority": PRIORITY[items[0]["severity"]],
            "title": f"{base} ({len(urls)} page{'s' if len(urls) > 1 else ''})",
            "detail": items[0]["message"], "action": ACTIONS.get(code, "Corrigez les pages listées."),
            "pages": urls[:50], "queries": [], "baseline": {"count": len(urls)}})
    return tasks


def tasks_from_insights(analysis: dict) -> list[dict]:
    tasks = []
    for ins in analysis.get("insights", []):
        if ins["priority"] == "good":
            continue
        for t in ins.get("targets", []):
            tasks.append({
                "fingerprint": f"gsc:{t['key']}", "source": "gsc", "priority": ins["priority"],
                "title": t["title"], "detail": t.get("detail") or ins["text"], "action": ins["action"],
                "pages": t["pages"], "queries": t["queries"][:30], "baseline": {}})
    return tasks


def impact(before: dict, after: dict, days_after: int) -> dict | None:
    """Comparaison par jour des clics, impressions et position moyenne sur les requêtes de la tâche."""
    if days_after < 7 or not before.get("days") or not after.get("days"):
        return None

    def per_day(d, k):
        return d[k] / d["days"] if d["days"] else 0

    def change(a, b):
        return round((b - a) / a * 100) if a else None
    return {"days_after": days_after, "final": days_after >= 28,
            "impressions_pct": change(per_day(before, "impressions"), per_day(after, "impressions")),
            "clicks_pct": change(per_day(before, "clicks"), per_day(after, "clicks")),
            "position_before": before.get("position"), "position_after": after.get("position")}
