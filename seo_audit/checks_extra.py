"""Contrôles avancés : hreflang, données structurées (JSON-LD), contenus dupliqués."""
import re
from collections import defaultdict

from .urls import normalize_url

HREFLANG_RE = re.compile(r"^(x-default|[a-z]{2,3}(-[A-Za-z]{4})?(-([A-Za-z]{2}|\d{3}))?)$")
MIN_WORDS_DUPLICATE = 50
NEAR_DUPLICATE_DISTANCE = 3

# Propriétés attendues par type schema.org (sous-ensemble utile pour les résultats enrichis)
REQUIRED = {
    "Article": ("headline",), "NewsArticle": ("headline",), "BlogPosting": ("headline",),
    "Product": ("name",), "Organization": ("name",), "LocalBusiness": ("name", "address"),
    "FAQPage": ("mainEntity",), "BreadcrumbList": ("itemListElement",),
    "Recipe": ("name",), "Event": ("name", "startDate"), "VideoObject": ("name", "uploadDate"),
    "WebSite": ("name",),
}


def run_extra_checks(results, add) -> None:
    for url, p in results.items():
        if p.is_html_ok:
            _check_hreflang(url, p, results, add)
            _check_structured_data(url, p, add)
    _check_duplicate_content(results, add)


def _check_hreflang(url, p, results, add):
    entries = [(code, normalize_url(href)) for code, href in p.parsed.hreflangs]
    if not entries:
        return
    seen: dict[str, set[str]] = defaultdict(set)
    for code, href in entries:
        seen[code.lower()].add(href)
        if not HREFLANG_RE.match(code):
            add(url, "international", "hreflang_invalid_code", "medium",
                f"Code hreflang invalide : « {code} »", href)
    for code, hrefs in seen.items():
        if len(hrefs) > 1:
            add(url, "international", "hreflang_conflict", "medium",
                f"Plusieurs URLs pour le même hreflang « {code} »", " | ".join(sorted(hrefs)))
    targets = {href for _, href in entries}
    if url not in targets and (p.canonical or url) not in targets:
        add(url, "international", "hreflang_missing_self", "medium",
            "Les hreflang ne contiennent pas la page elle-même")
    for target in sorted(targets - {url}):
        tp = results.get(target)
        if tp is None:
            continue
        if tp.status != 200 or tp.is_noindex or tp.blocked_by_robots:
            add(url, "international", "hreflang_to_non_indexable", "high",
                "Hreflang vers une page non indexable", f"{target} (statut {tp.status})")
        elif tp.is_html_ok:
            back = {normalize_url(h) for _, h in tp.parsed.hreflangs}
            if url not in back:
                add(url, "international", "hreflang_missing_return", "medium",
                    "Hreflang sans lien retour (la page cible ne pointe pas vers celle-ci)", target)


def _types(item) -> list[str]:
    t = item.get("@type") if isinstance(item, dict) else None
    return [t] if isinstance(t, str) else [x for x in t or [] if isinstance(x, str)]


def _check_structured_data(url, p, add):
    d = p.parsed
    if d.json_ld_errors:
        add(url, "donnees", "jsonld_invalid", "high",
            f"JSON-LD illisible ({d.json_ld_errors} bloc{'s' if d.json_ld_errors > 1 else ''})")
    items = []
    for obj in d.json_ld:
        graph = obj.get("@graph") if isinstance(obj, dict) else None
        items.extend(graph if isinstance(graph, list) else [obj])
    for item in items:
        if not isinstance(item, dict):
            continue
        types = _types(item)
        if not types:
            add(url, "donnees", "jsonld_missing_type", "medium", "Bloc JSON-LD sans @type")
            continue
        for t in types:
            missing = [f for f in REQUIRED.get(t, ()) if not item.get(f)]
            if missing:
                add(url, "donnees", "jsonld_missing_property", "medium",
                    f"Données structurées {t} : propriété manquante", ", ".join(missing))


def _check_duplicate_content(results, add):
    pages = [(u, p.parsed) for u, p in results.items()
             if p.indexable and p.parsed.word_count >= MIN_WORDS_DUPLICATE]
    exact = defaultdict(list)
    for u, d in pages:
        exact[d.content_hash].append(u)
    exact_urls = set()
    for urls in exact.values():
        if len(urls) < 2:
            continue
        for u in urls:
            exact_urls.add(u)
            others = [o for o in urls if o != u]
            add(u, "contenu", "duplicate_content", "medium",
                f"Contenu identique sur plusieurs pages ({len(urls)})",
                ", ".join(others[:5]) + (" …" if len(others) > 5 else ""))
    reps = [(u, d.simhash) for u, d in pages if u not in exact_urls]
    near = defaultdict(list)
    for i, (u1, h1) in enumerate(reps):
        for u2, h2 in reps[i + 1:]:
            if (h1 ^ h2).bit_count() <= NEAR_DUPLICATE_DISTANCE:
                near[u1].append(u2)
                near[u2].append(u1)
    for u, others in near.items():
        add(u, "contenu", "near_duplicate_content", "low",
            f"Contenu quasi identique à {len(others)} autre{'s' if len(others) > 1 else ''} page{'s' if len(others) > 1 else ''}",
            ", ".join(sorted(others)[:5]))
