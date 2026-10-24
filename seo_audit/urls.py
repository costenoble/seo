"""Normalisation d'URL : une seule fonction, utilisée partout (crawl, rapports, jointures futures)."""
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "gclid", "fbclid", "msclkid", "mc_cid", "mc_eid",
}


def normalize_url(url: str) -> str:
    """Schéma et hôte en minuscules, port par défaut retiré, fragment retiré,
    paramètres de tracking retirés, chemin vide remplacé par '/'."""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    try:
        port = parts.port
    except ValueError:
        port = None
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        host = f"{host}:{port}"
    path = parts.path or "/"
    query = urlencode(
        [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
         if k.lower() not in TRACKING_PARAMS]
    )
    return urlunsplit((scheme, host, path, query, ""))


def is_http(url: str) -> bool:
    return urlsplit(url).scheme in ("http", "https")


def site_key(url: str) -> str:
    """Hôte sans 'www.' : example.com et www.example.com sont le même site."""
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host
