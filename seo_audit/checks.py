"""Règles d'audit. Chaque règle produit des Issue ; les seuils sont regroupés en haut du fichier."""
from collections import defaultdict

from .links import is_broken
from .checks_extra import run_extra_checks
from .models import Issue, PageResult
from .urls import normalize_url

TITLE_MIN, TITLE_MAX = 30, 60
DESC_MIN, DESC_MAX = 70, 160
THIN_CONTENT_WORDS = 200
MAX_DEPTH = 3
SLOW_TTFB_MS = 800
MAX_REDIRECT_HOPS = 10


def follow_chain(url: str, results: dict[str, PageResult]) -> tuple[list[str], bool]:
    """Renvoie la chaîne de redirections depuis url et un booléen « boucle détectée »."""
    chain, seen = [url], {url}
    current = results[url]
    while current.redirect_to and len(chain) <= MAX_REDIRECT_HOPS:
        nxt = current.redirect_to
        if nxt in seen:
            chain.append(nxt)
            return chain, True
        chain.append(nxt)
        seen.add(nxt)
        if nxt not in results:
            break
        current = results[nxt]
    return chain, False


def run_checks(crawler, external_status: dict | None = None) -> list[Issue]:
    results: dict[str, PageResult] = crawler.results
    issues: list[Issue] = []

    def add(url, category, code, severity, message, detail=""):
        issues.append(Issue(url, category, code, severity, message, detail))

    for url, p in results.items():
        _check_page(url, p, results, add)

    _check_duplicates(results, add)
    run_extra_checks(results, add)
    _check_links(crawler, results, external_status or {}, add)

    for url in crawler.sitemap_urls:
        if url in results and url != crawler.start_url and not crawler.inlinks.get(url):
            add(url, "liens", "orphan_page", "medium",
                "Page orpheline : présente dans le sitemap mais aucun lien interne n'y mène")
    return issues


def _check_page(url, p: PageResult, results, add):
    if p.blocked_by_robots:
        if p.in_sitemap:
            add(url, "indexabilite", "sitemap_blocked", "high",
                "Bloquée par robots.txt mais présente dans le sitemap")
        else:
            add(url, "indexabilite", "blocked_robots_txt", "info", "Bloquée par robots.txt")
        return
    if p.error:
        add(url, "http", "fetch_error", "critical", "Page injoignable", p.error)
        return

    s = p.status or 0
    if s >= 500:
        add(url, "http", "http_5xx", "critical", f"Erreur serveur ({s})")
    elif s >= 400:
        add(url, "http", "http_4xx", "high", f"Erreur client ({s})")
    elif 300 <= s < 400:
        chain, loop = follow_chain(url, results)
        if loop:
            add(url, "http", "redirect_loop", "high", "Boucle de redirection", " → ".join(chain))
        elif len(chain) > 2:
            add(url, "http", "redirect_chain", "medium",
                f"Chaîne de redirections ({len(chain) - 1} sauts)", " → ".join(chain))
        if s in (302, 303, 307):
            add(url, "http", "temporary_redirect", "low",
                f"Redirection temporaire, une 301 transmet mieux les signaux ({s})", p.redirect_to or "")

    if p.in_sitemap and s != 200:
        add(url, "indexabilite", "sitemap_non_200", "high", f"URL du sitemap hors statut 200 ({s})")
    if p.ttfb_ms and p.ttfb_ms > SLOW_TTFB_MS:
        add(url, "performance", "slow_ttfb", "medium",
            f"Temps de réponse serveur lent ({p.ttfb_ms:.0f} ms)")
    if not p.is_html_ok:
        return

    _check_indexability(url, p, results, add)
    _check_content(url, p, add)
    _check_js_dependency(url, p, add)
    _check_performance(url, p, add)
    _check_gsc(url, p, add)


def _check_indexability(url, p, results, add):
    if p.is_noindex:
        if p.in_sitemap:
            add(url, "indexabilite", "sitemap_noindex", "high",
                "Page en noindex mais présente dans le sitemap", ", ".join(sorted(p.robots_directives)))
        else:
            add(url, "indexabilite", "noindex", "medium", "Page non indexable (noindex)",
                ", ".join(sorted(p.robots_directives)))

    canonicals = {normalize_url(c) for c in p.parsed.canonicals}
    if not canonicals:
        add(url, "indexabilite", "missing_canonical", "low", "Balise canonical absente")
    elif len(canonicals) > 1:
        add(url, "indexabilite", "multiple_canonicals", "high",
            "Plusieurs balises canonical différentes", " | ".join(sorted(canonicals)))
    elif p.canonical != url:
        target = results.get(p.canonical)
        if target and (target.status != 200 or target.is_noindex or target.blocked_by_robots):
            add(url, "indexabilite", "canonical_to_non_indexable", "high",
                "La canonical pointe vers une page non indexable",
                f"{p.canonical} (statut {target.status})")
        else:
            add(url, "indexabilite", "canonicalised", "info",
                "Page canonicalisée vers une autre URL", p.canonical)


def _check_content(url, p, add):
    d = p.parsed
    if not d.title:
        add(url, "contenu", "missing_title", "high", "Balise title absente ou vide")
    else:
        if len(d.titles) > 1:
            add(url, "contenu", "multiple_titles", "medium", f"Balises title multiples ({len(d.titles)})")
        n = len(d.title)
        if n > TITLE_MAX:
            add(url, "contenu", "title_too_long", "low", f"Title trop long ({n} caractères)", d.title)
        elif n < TITLE_MIN:
            add(url, "contenu", "title_too_short", "low", f"Title trop court ({n} caractères)", d.title)

    if not d.description:
        add(url, "contenu", "missing_description", "medium", "Meta description absente ou vide")
    else:
        if len(d.meta_descriptions) > 1:
            add(url, "contenu", "multiple_descriptions", "medium",
                f"Meta descriptions multiples ({len(d.meta_descriptions)})")
        n = len(d.description)
        if n > DESC_MAX:
            add(url, "contenu", "description_too_long", "low", f"Meta description trop longue ({n} caractères)")
        elif n < DESC_MIN:
            add(url, "contenu", "description_too_short", "low", f"Meta description trop courte ({n} caractères)")

    h1 = [h for h in d.h1 if h]
    if not h1:
        add(url, "contenu", "missing_h1", "medium", "Aucun H1")
    elif len(h1) > 1:
        add(url, "contenu", "multiple_h1", "low", f"Plusieurs H1 ({len(h1)})", " | ".join(h1[:3]))

    if not d.lang:
        add(url, "technique", "missing_lang", "low", "Attribut lang absent sur <html>")
    if not d.has_viewport:
        add(url, "technique", "missing_viewport", "medium", "Meta viewport absente (affichage mobile)")
    if d.images_missing_alt:
        add(url, "contenu", "images_missing_alt", "low",
            f"Images sans attribut alt ({len(d.images_missing_alt)})", ", ".join(d.images_missing_alt[:5]))
    if p.indexable and d.word_count < THIN_CONTENT_WORDS:
        add(url, "contenu", "thin_content", "low", f"Contenu faible ({d.word_count} mots)")
    if p.depth is not None and p.depth > MAX_DEPTH:
        add(url, "liens", "deep_page", "low", f"Page trop profonde ({p.depth} clics depuis l'accueil)")


def _check_js_dependency(url, p, add):
    raw, rendered = p.parsed_raw, p.parsed
    if raw is None:
        return
    def squash(text):
        return " ".join(text.split())

    diffs = []
    if squash(raw.title) != squash(rendered.title):
        diffs.append("title")
    if squash(raw.description) != squash(rendered.description):
        diffs.append("meta description")
    if sorted(map(normalize_url, raw.canonicals)) != sorted(map(normalize_url, rendered.canonicals)):
        diffs.append("canonical")
    if sorted(raw.meta_robots) != sorted(rendered.meta_robots):
        diffs.append("meta robots")
    if [squash(h) for h in raw.h1 if h] != [squash(h) for h in rendered.h1 if h]:
        diffs.append("H1")
    if diffs:
        add(url, "technique", "js_dependent_seo", "medium",
            "Éléments SEO différents entre HTML brut et rendu JavaScript", ", ".join(diffs))


def _check_performance(url, p, add):
    perf = p.performance
    if not perf or perf.get("error"):
        return
    score = perf.get("score")
    if score is not None and score < 50:
        add(url, "performance", "perf_poor", "high", f"Score Lighthouse faible ({score}/100)")
    elif score is not None and score < 90:
        add(url, "performance", "perf_needs_improvement", "medium", f"Score Lighthouse à améliorer ({score}/100)")

    field = perf.get("field", {})
    scope = ", données de l'origine" if field.get("origin_fallback") else ""
    for key, label, good, poor, unit in (
        ("lcp_ms", "LCP", 2500, 4000, " ms"),
        ("inp_ms", "INP", 200, 500, " ms"),
        ("cls", "CLS", 0.1, 0.25, ""),
    ):
        value = field.get(key)
        if value is None or value <= good:
            continue
        sev = "high" if value > poor else "medium"
        add(url, "performance", f"cwv_{key.split('_')[0]}", sev,
            f"{label} terrain trop élevé ({value:g}{unit} au 75e centile{scope})", f"seuil « bon » : {good:g}{unit}")


def _check_gsc(url, p, add):
    g = p.gsc
    if not g or g.get("error"):
        return
    verdict, state = g.get("verdict"), g.get("coverageState") or ""
    if verdict == "FAIL":
        add(url, "indexabilite", "gsc_error", "high", "Erreur d'indexation dans Search Console", state)
    elif verdict == "NEUTRAL" and p.indexable:
        add(url, "indexabilite", "gsc_not_indexed", "medium",
            "Page indexable mais non indexée par Google", state)
    gc, uc = g.get("googleCanonical"), g.get("userCanonical")
    if gc and uc and gc != uc:
        add(url, "indexabilite", "gsc_canonical_mismatch", "medium",
            "Google a choisi une autre canonical que celle déclarée", f"Google : {gc}")


def _check_duplicates(results, add):
    titles, descs = defaultdict(list), defaultdict(list)
    for url, p in results.items():
        if not p.indexable:
            continue
        if p.parsed.title:
            titles[p.parsed.title.strip().lower()].append(url)
        if p.parsed.description:
            descs[p.parsed.description.strip().lower()].append(url)
    for groups, code, sev, label in ((titles, "duplicate_title", "medium", "Title"),
                                      (descs, "duplicate_description", "low", "Meta description")):
        for urls in groups.values():
            if len(urls) < 2:
                continue
            for u in urls:
                others = [o for o in urls if o != u]
                add(u, "contenu", code, sev, f"{label} identique sur plusieurs pages ({len(urls)})",
                    ", ".join(others[:5]) + (" …" if len(others) > 5 else ""))


def _check_links(crawler, results, external_status, add):
    for target, sources in crawler.inlinks.items():
        tp = results.get(target)
        if tp is None or tp.blocked_by_robots:
            continue
        if tp.error or (tp.status and tp.status >= 400):
            reason = tp.status or tp.error
            for src in sorted(sources):
                add(src, "liens", "broken_internal_link", "high", "Lien interne cassé", f"{target} ({reason})")
        elif tp.status and 300 <= tp.status < 400:
            add(target, "liens", "linked_redirect", "low",
                f"Redirection ciblée par des liens internes ({len(sources)})",
                "Remplacer par l'URL finale dans : " + ", ".join(sorted(sources)[:5]))

    for target, info in external_status.items():
        if not is_broken(info):
            continue
        reason = info.get("status") or info.get("error")
        for src in sorted(crawler.external_sources.get(target, ())):
            add(src, "liens", "broken_external_link", "medium", "Lien externe cassé", f"{target} ({reason})")
