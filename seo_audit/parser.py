"""Extraction des éléments SEO d'une page HTML."""
import hashlib
import json
from dataclasses import dataclass, field, fields
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser as HTMLParser

SKIP_PREFIXES = ("mailto:", "tel:", "javascript:", "data:", "sms:", "#")


@dataclass
class ParsedPage:
    titles: list[str] = field(default_factory=list)
    meta_descriptions: list[str] = field(default_factory=list)
    meta_robots: list[str] = field(default_factory=list)
    canonicals: list[str] = field(default_factory=list)
    h1: list[str] = field(default_factory=list)
    lang: str | None = None
    has_viewport: bool = False
    links: list[tuple[str, bool]] = field(default_factory=list)  # (url absolue, nofollow)
    images_missing_alt: list[str] = field(default_factory=list)
    word_count: int = 0
    hreflangs: list[tuple[str, str]] = field(default_factory=list)  # (code, url absolue)
    json_ld: list = field(default_factory=list)  # objets JSON-LD décodés
    json_ld_errors: int = 0
    content_hash: str = ""
    simhash: int = 0

    @classmethod
    def from_dict(cls, data: dict) -> "ParsedPage":
        known = {f.name for f in fields(cls)}
        data = {k: v for k, v in data.items() if k in known}
        for key in ("links", "hreflangs"):  # le JSON transforme les tuples en listes
            data[key] = [tuple(x) for x in data.get(key, [])]
        return cls(**data)

    @property
    def title(self) -> str:
        return next((t for t in self.titles if t), "")

    @property
    def description(self) -> str:
        return next((d for d in self.meta_descriptions if d), "")


def compute_simhash(words: list[str], size: int = 3) -> int:
    """Empreinte 64 bits : deux textes proches ont des empreintes à faible distance de Hamming."""
    if len(words) < size:
        shingles = [" ".join(words)]
    else:
        shingles = [" ".join(words[i:i + size]) for i in range(len(words) - size + 1)]
    counts = [0] * 64
    for sh in set(shingles):
        h = int.from_bytes(hashlib.blake2b(sh.encode(), digest_size=8).digest(), "big")
        for bit in range(64):
            counts[bit] += 1 if h >> bit & 1 else -1
    return sum(1 << bit for bit in range(64) if counts[bit] > 0)


def parse_html(html: str, url: str) -> ParsedPage:
    tree = HTMLParser(html)
    p = ParsedPage()

    base = url
    base_tag = tree.css_first("base[href]")
    if base_tag and base_tag.attributes.get("href"):
        base = urljoin(url, base_tag.attributes["href"])

    p.titles = [n.text(strip=True) for n in tree.css("head title")]

    for n in tree.css("meta"):
        name = (n.attributes.get("name") or "").strip().lower()
        content = (n.attributes.get("content") or "").strip()
        if name == "description":
            p.meta_descriptions.append(content)
        elif name in ("robots", "googlebot"):
            p.meta_robots.append(content.lower())
        elif name == "viewport":
            p.has_viewport = True

    for n in tree.css("link[rel]"):
        rels = (n.attributes.get("rel") or "").lower().split()
        href = n.attributes.get("href")
        if "canonical" in rels and href:
            p.canonicals.append(urljoin(base, href.strip()))
        if "alternate" in rels and href and n.attributes.get("hreflang"):
            p.hreflangs.append((n.attributes["hreflang"].strip(), urljoin(base, href.strip())))

    for n in tree.css('script[type="application/ld+json"]'):
        try:
            data = json.loads(n.text())
        except ValueError:
            p.json_ld_errors += 1
            continue
        p.json_ld.extend(data if isinstance(data, list) else [data])

    p.h1 = [n.text(strip=True) for n in tree.css("h1")]

    html_tag = tree.css_first("html")
    if html_tag:
        p.lang = (html_tag.attributes.get("lang") or "").strip() or None

    for n in tree.css("a[href]"):
        href = (n.attributes.get("href") or "").strip()
        if not href or href.lower().startswith(SKIP_PREFIXES):
            continue
        rel = (n.attributes.get("rel") or "").lower().split()
        p.links.append((urljoin(base, href), "nofollow" in rel))

    for n in tree.css("img"):
        if "alt" not in n.attributes:
            p.images_missing_alt.append(n.attributes.get("src") or "(sans src)")

    for n in tree.css("script, style, noscript, template"):
        n.decompose()
    body = tree.body
    words = body.text(separator=" ").lower().split() if body else []
    p.word_count = len(words)
    if words:
        p.content_hash = hashlib.sha1(" ".join(words).encode()).hexdigest()
        p.simhash = compute_simhash(words)
    return p
