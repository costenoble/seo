from dataclasses import asdict, dataclass, field, fields

from .parser import ParsedPage

SEVERITIES = ("critical", "high", "medium", "low", "info")
SEVERITY_LABELS = {
    "critical": "Critique", "high": "Élevée", "medium": "Moyenne", "low": "Faible", "info": "Info",
}
CATEGORY_LABELS = {
    "http": "HTTP", "indexabilite": "Indexabilité", "contenu": "Titres et métadonnées",
    "liens": "Liens", "performance": "Performance", "technique": "Technique",
    "international": "International", "donnees": "Données structurées",
}


@dataclass
class Issue:
    url: str
    category: str
    code: str
    severity: str
    message: str
    detail: str = ""


@dataclass
class PageResult:
    url: str
    depth: int | None
    status: int | None = None
    redirect_to: str | None = None
    content_type: str = ""
    ttfb_ms: float | None = None
    x_robots_tag: str = ""
    error: str | None = None
    blocked_by_robots: bool = False
    in_sitemap: bool = False
    parsed: ParsedPage | None = None
    parsed_raw: ParsedPage | None = None  # HTML brut, rempli seulement avec --render-js
    internal_links: list[str] = field(default_factory=list)
    external_links: list[str] = field(default_factory=list)
    performance: dict | None = None
    gsc: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "PageResult":
        known = {f.name for f in fields(cls)}
        data = {k: v for k, v in data.items() if k in known}
        for key in ("parsed", "parsed_raw"):
            if data.get(key):
                data[key] = ParsedPage.from_dict(data[key])
        return cls(**data)

    @property
    def is_html_ok(self) -> bool:
        return self.status == 200 and self.parsed is not None

    @property
    def robots_directives(self) -> set[str]:
        raw = list(self.parsed.meta_robots) if self.parsed else []
        raw.append(self.x_robots_tag.lower())
        tokens = set()
        for value in raw:
            for tok in value.split(","):
                tok = tok.split(":")[-1].strip()  # "googlebot: noindex" -> "noindex"
                if tok:
                    tokens.add(tok)
        return tokens

    @property
    def is_noindex(self) -> bool:
        return bool(self.robots_directives & {"noindex", "none"})

    @property
    def canonical(self) -> str | None:
        from .urls import normalize_url
        if not self.parsed or not self.parsed.canonicals:
            return None
        return normalize_url(self.parsed.canonicals[0])

    @property
    def indexable(self) -> bool:
        if not self.is_html_ok or self.is_noindex or self.blocked_by_robots:
            return False
        return self.canonical in (None, self.url)
