"""Correction automatique : du problème SEO à la pull request.

1. détecter la technologie du dépôt et retrouver les fichiers qui génèrent les pages concernées ;
2. demander à Claude une modification minimale (format JSON strict) ;
3. vérifier chaque modification (texte à remplacer présent une seule fois, fichiers autorisés seulement) ;
4. créer une branche, y écrire les fichiers et ouvrir une pull request. Rien n'est fusionné automatiquement."""
import json
import posixpath
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

import anthropic

MODEL = "claude-opus-5-5"
MAX_PAGES = 4               # pages par pull request : garde la requête courte et la relecture facile
MAX_FILE_CHARS = 60_000     # au-delà, on ne confie pas le fichier à l'IA
PAGE_EXT = (".tsx", ".jsx", ".ts", ".js", ".mdx", ".md")

# Tâches que Vigie SEO sait corriger, et la consigne correspondante pour l'IA
FIXABLE = {
    "audit:missing_title": "Ajoute une balise title unique (30 à 60 caractères), le sujet principal au début.",
    "audit:title_too_long": "Raccourcis le title à 60 caractères au plus, en gardant le mot-clé principal au début.",
    "audit:title_too_short": "Allonge le title (30 à 60 caractères) en précisant le sujet et le bénéfice.",
    "audit:duplicate_title": "Donne à chaque page un title différent qui décrit son contenu propre.",
    "audit:missing_description": "Ajoute une meta description de 120 à 160 caractères qui donne envie de cliquer.",
    "audit:description_too_long": "Raccourcis la meta description à 160 caractères au plus.",
    "audit:description_too_short": "Allonge la meta description à 120-160 caractères (bénéfice, preuve, appel à l'action).",
    "audit:duplicate_description": "Écris une meta description propre à chaque page.",
    "audit:missing_lang": "Ajoute l'attribut lang sur la balise <html> (langue principale du site).",
    "audit:missing_viewport": "Ajoute la balise meta viewport (width=device-width, initial-scale=1).",
    "audit:missing_canonical": "Ajoute une balise canonical qui pointe vers l'URL de la page elle-même.",
    "gsc:noindex-utility": "Ajoute une directive noindex (robots) sur ces pages de connexion, d'inscription ou de compte.",
}
FIXABLE_PREFIXES = {"gsc:quick:": "Réécris le title et la meta description de cette page pour mieux correspondre aux "
                                  "requêtes Google listées et donner envie de cliquer (title ≤ 60 caractères, description 120 à 160)."}


class FixError(Exception):
    pass


def fixable(fingerprint: str) -> str | None:
    if fingerprint in FIXABLE:
        return FIXABLE[fingerprint]
    return next((v for k, v in FIXABLE_PREFIXES.items() if fingerprint.startswith(k)), None)


# -- technologie et fichiers ----------------------------------------------------------------------
def detect_framework(paths: list[str], package_json: str | None) -> str:
    deps = {}
    if package_json:
        try:
            pkg = json.loads(package_json)
            deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
        except ValueError:
            pass
    if "next" in deps:
        return "next"
    if "astro" in deps:
        return "astro"
    if "@sveltejs/kit" in deps:
        return "sveltekit"
    if "nuxt" in deps:
        return "nuxt"
    if any(p.endswith(".html") for p in paths):
        return "html"
    return "unknown"


def _segments(route_file: str, root: str, strip: tuple[str, ...]) -> list[str] | None:
    rel = route_file[len(root):]
    for s in strip:
        if rel.endswith(s):
            rel = rel[: -len(s)]
            break
    else:
        return None
    return [s for s in rel.strip("/").split("/") if s and not (s.startswith("(") and s.endswith(")"))]


def _match(pattern: list[str], parts: list[str]) -> int:
    """Score de correspondance d'une route (0 = non) : les segments statiques comptent plus que les dynamiques."""
    if pattern and pattern[-1].startswith("[...") or pattern and pattern[-1].startswith("[[..."):
        head = pattern[:-1]
        if len(parts) < len(head):
            return 0
        pattern, parts = head, parts[:len(head)]
    if len(pattern) != len(parts):
        return 0
    score = 1
    for p, s in zip(pattern, parts):
        if p.startswith("["):
            score += 1
        elif p.lower() == s.lower():
            score += 3
        else:
            return 0
    return score


def locate(framework: str, paths: list[str], url: str) -> list[str]:
    """Fichiers à fournir à l'IA pour une URL : la page et les gabarits qui définissent ses métadonnées."""
    parts = [p for p in (urlsplit(url).path or "/").strip("/").split("/") if p]
    best, files = (0, None), []
    if framework == "next":
        for root in ("src/app/", "app/"):
            pages = [p for p in paths if p.startswith(root) and re.search(r"/page\.(tsx|jsx|ts|js|mdx)$", "/" + p)]
            for p in pages:
                seg = _segments(p, root, tuple(f"page{e}" for e in PAGE_EXT))
                score = _match(seg, parts) if seg is not None else 0
                if score > best[0]:
                    best = (score, p)
            if best[1]:
                page = best[1]
                files = [page]
                d = posixpath.dirname(page)
                while d.startswith(root.rstrip("/")):  # gabarits parents : métadonnées par défaut
                    for e in (".tsx", ".jsx", ".ts", ".js"):
                        if f"{d}/layout{e}" in paths:
                            files.append(f"{d}/layout{e}")
                    if d == root.rstrip("/"):
                        break
                    d = posixpath.dirname(d)
                return files
        for root in ("src/pages/", "pages/"):
            for p in paths:
                if not p.startswith(root) or "/api/" in "/" + p or posixpath.basename(p).startswith("_"):
                    continue
                for suffix in [f"/index{e}" for e in PAGE_EXT] + list(PAGE_EXT):
                    seg = _segments(p, root, (suffix,))
                    if seg is not None:
                        score = _match(seg, parts)
                        if score > best[0]:
                            best = (score, p)
                        break
            if best[1]:
                extra = [f"{root}_document{e}" for e in (".tsx", ".jsx", ".js") if f"{root}_document{e}" in paths]
                return [best[1]] + extra
    if framework == "astro":
        for p in paths:
            if p.startswith("src/pages/"):
                for suffix in ("/index.astro", ".astro", "/index.md", ".md"):
                    seg = _segments(p, "src/pages/", (suffix,))
                    if seg is not None:
                        score = _match(seg, parts)
                        if score > best[0]:
                            best = (score, p)
                        break
        if best[1]:
            layouts = [p for p in paths if p.startswith("src/layouts/") and p.endswith(".astro")][:2]
            return [best[1]] + layouts
    if framework == "sveltekit":
        for p in paths:
            if p.startswith("src/routes/") and p.endswith("/+page.svelte"):
                seg = _segments(p, "src/routes/", ("+page.svelte",))
                score = _match(seg, parts) if seg is not None else 0
                if score > best[0]:
                    best = (score, p)
        if best[1]:
            return [best[1]] + [p for p in ("src/app.html",) if p in paths]
    if framework == "html":
        base = "/".join(parts)
        for c in ([f"{base}/index.html", f"{base}.html"] if base else ["index.html"]):
            for prefix in ("", "public/", "dist/", "docs/"):
                if prefix + c in paths:
                    return [prefix + c]
    return []


def root_files(framework: str, paths: list[str]) -> list[str]:
    """Fichiers globaux (balise <html>, <head>) pour lang ou viewport."""
    candidates = {"next": ["src/app/layout.tsx", "app/layout.tsx", "src/app/layout.jsx", "app/layout.jsx", "app/layout.js",
                           "pages/_document.tsx", "src/pages/_document.tsx", "pages/_document.jsx", "pages/_document.js"],
                  "astro": [p for p in paths if p.startswith("src/layouts/")][:3], "sveltekit": ["src/app.html"],
                  "html": ["index.html", "public/index.html"]}.get(framework, [])
    return [c for c in candidates if c in paths]


# -- rédaction par Claude ----------------------------------------------------------------------------
SCHEMA = {
    "type": "object",
    "properties": {
        "files": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "mode": {"type": "string", "enum": ["edit", "create"]},
                "edits": {"type": "array", "items": {"type": "object", "properties": {"search": {"type": "string"}, "replace": {"type": "string"}},
                                                     "required": ["search", "replace"], "additionalProperties": False}},
                "content": {"type": "string"},
            },
            "required": ["path", "mode", "edits", "content"], "additionalProperties": False}},
        "pr_title": {"type": "string"},
        "summary": {"type": "string"},
        "changes": {"type": "array", "items": {"type": "object", "properties": {"page": {"type": "string"}, "before": {"type": "string"}, "after": {"type": "string"}},
                                               "required": ["page", "before", "after"], "additionalProperties": False}},
        "skipped_reason": {"type": "string"},
    },
    "required": ["files", "pr_title", "summary", "changes", "skipped_reason"],
    "additionalProperties": False,
}

SYSTEM = """Tu es un développeur web expert en référencement. Tu corriges un problème SEO précis dans le code d'un site,
avec la modification la plus petite et la plus sûre possible.

Règles :
- Ne modifie que les fichiers fournis (mode "edit"), ou crée un fichier nouveau uniquement si la technologie l'exige
  (par exemple un layout.tsx pour ajouter des métadonnées à une page client Next.js) (mode "create" avec son contenu complet).
- En mode "edit", chaque "search" doit être un extrait EXACT du fichier, présent une seule fois, assez long pour être unique ;
  "replace" est le texte qui le remplace. Laisse "content" vide. En mode "create", laisse "edits" vide.
- Respecte le style du code (guillemets, indentation, conventions du framework). Ne change rien d'autre : ni logique, ni mise en page.
- Rédige en français si le site est en français. Titles de 60 caractères au plus, meta descriptions de 120 à 160 caractères,
  sans bourrage de mots-clés, en t'appuyant sur les requêtes Google fournies quand il y en a.
- Si tu ne peux pas corriger proprement (contenu chargé depuis un CMS, fichier introuvable, risque de casser le site),
  renvoie "files": [] et explique pourquoi dans "skipped_reason".
- "pr_title" : titre court de la pull request, en français. "summary" : 2 ou 3 phrases pour le relecteur.
  "changes" : pour chaque page, la valeur avant et après (title, description…)."""


def ask_claude(api_key: str, task: dict, framework: str, pages: list[dict], files: dict[str, str]) -> dict:
    """pages : [{url, title, description, queries}] ; files : {chemin: contenu}."""
    parts = [f"Technologie : {framework}", f"Problème : {task['title']}", f"Consigne : {task['instruction']}",
             f"Détail : {task.get('detail') or ''}", "", "Pages concernées :"]
    for p in pages:
        parts.append(f"- {p['url']} | title actuel : {p.get('title') or '(inconnu)'} | description actuelle : {p.get('description') or '(inconnue)'}"
                     + (f" | requêtes Google : {', '.join(p['queries'][:8])}" if p.get("queries") else ""))
    parts.append("\nFichiers :")
    for path, content in files.items():
        parts.append(f'<file path="{path}">\n{content}\n</file>')
    client = anthropic.Anthropic(api_key=api_key, timeout=55, max_retries=1)
    try:
        # Repli côté serveur : si le modèle refuse, une autre version de Claude reprend la même demande.
        response = client.beta.messages.create(
            model=MODEL, max_tokens=16000, system=SYSTEM,
            messages=[{"role": "user", "content": "\n".join(parts)}],
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        )
    except anthropic.RateLimitError as exc:
        raise FixError("Service d'IA saturé, réessayez dans une minute") from exc
    except anthropic.APIStatusError as exc:
        raise FixError(f"Service d'IA indisponible ({exc.status_code})") from exc
    except anthropic.APIConnectionError as exc:
        raise FixError("Service d'IA injoignable") from exc
    if response.stop_reason == "refusal":
        raise FixError("L'IA a refusé de traiter cette demande")
    if response.stop_reason == "max_tokens":
        raise FixError("Correction trop longue : réessayez avec moins de pages")
    text = next((b.text for b in response.content if b.type == "text"), "")
    return json.loads(text)


# -- vérification et application ----------------------------------------------------------------------
def apply_plan(plan: dict, originals: dict[str, str], existing_paths: set[str]) -> dict[str, str]:
    """Renvoie {chemin: nouveau contenu} ; lève FixError si une modification n'est pas sûre."""
    out = {}
    for f in plan.get("files", []):
        path = f["path"].lstrip("/")
        if ".." in path.split("/") or path.startswith((".git/", ".github/")):
            raise FixError(f"Chemin refusé : {path}")
        if f["mode"] == "create":
            if path in existing_paths:
                raise FixError(f"Le fichier {path} existe déjà")
            if not f["content"].strip():
                raise FixError(f"Contenu vide pour {path}")
            out[path] = f["content"]
            continue
        if path not in originals:
            raise FixError(f"Modification d'un fichier non fourni : {path}")
        content = out.get(path, originals[path])
        for e in f["edits"]:
            if not e["search"]:
                raise FixError(f"Modification vide dans {path}")
            n = content.count(e["search"])
            if n != 1:
                raise FixError(f"Texte à remplacer {'introuvable' if n == 0 else 'ambigu'} dans {path}")
            content = content.replace(e["search"], e["replace"], 1)
        if content != originals[path]:
            out[path] = content
    return out


def pr_body(plan: dict, task: dict, pages: list[dict], app_url: str) -> str:
    lines = [plan["summary"], "", f"**Problème** : {task['title']}", ""]
    if plan.get("changes"):
        lines += ["| Page | Avant | Après |", "|---|---|---|"]
        for c in plan["changes"]:
            esc = lambda s: (s or "").replace("|", "\\|").replace("\n", " ")
            lines.append(f"| `{esc(c['page'])}` | {esc(c['before'])} | {esc(c['after'])} |")
        lines.append("")
    lines += ["**À vérifier avant de fusionner** : l'aperçu du déploiement (titres et descriptions affichés, aucune page cassée).", "",
              f"_Proposé par [Vigie SEO]({app_url}). Rien n'est fusionné automatiquement : vous gardez la main._"]
    return "\n".join(lines)


def branch_name(fingerprint: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", fingerprint.lower()).strip("-")[:40]
    return f"vigie-seo/{slug}-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}"


# -- orchestration ---------------------------------------------------------------------------------------
def run_fix(db, settings, user_id: str, task: dict, repo_link: dict, gh) -> dict:
    """Prépare et ouvre la pull request d'une tâche. Renvoie {status, pr_url, pr_number, branch, summary, error}."""
    from .github import Repo
    instruction = fixable(task["fingerprint"])
    if not instruction:
        raise FixError("Cette tâche ne peut pas encore être corrigée automatiquement")
    repo = Repo(gh.installation_token(repo_link["installation_id"]), repo_link["repo"])
    base = repo.default_branch()
    head = repo.head_sha(base)
    paths = repo.tree(head)
    pkg = repo.read("package.json", head) if "package.json" in paths else None
    framework = detect_framework(paths, pkg[0] if pkg else None)
    if framework in ("unknown", "nuxt"):
        raise FixError("Technologie du site non prise en charge pour l'instant (Next.js, Astro, SvelteKit et HTML le sont)")

    report = db.latest_report_for_site(user_id, task["site"]) or {"pages": []}
    rows = {r["url"]: r for r in report.get("pages", [])}
    urls = list(dict.fromkeys(task.get("pages") or []))[:MAX_PAGES]
    if task["fingerprint"] in ("audit:missing_lang", "audit:missing_viewport"):
        wanted, pages = root_files(framework, paths), []
    else:
        wanted, pages = [], []
        for url in urls:
            found = locate(framework, paths, url)
            if found:
                wanted += found
                pages.append({"url": url, "title": (rows.get(url) or {}).get("title"),
                              "queries": task.get("queries") or db.page_queries(user_id, task["site"], url)})
    wanted = list(dict.fromkeys(wanted))
    if not wanted:
        raise FixError(f"Impossible de retrouver dans le dépôt les fichiers des pages concernées ({framework})")
    files, shas = {}, {}
    for path in wanted:
        got = repo.read(path, head)
        if got and len(got[0]) <= MAX_FILE_CHARS:
            files[path], shas[path] = got
    if not files:
        raise FixError("Fichiers trop volumineux pour une correction automatique")

    plan = ask_claude(settings.anthropic_api_key, {**task, "instruction": instruction}, framework, pages, files)
    changed = apply_plan(plan, files, set(paths))
    if not changed:
        return {"status": "skipped", "error": plan.get("skipped_reason") or "Aucune modification nécessaire", "summary": plan.get("summary")}

    branch = branch_name(task["fingerprint"])
    repo.create_branch(branch, head)
    for path, content in changed.items():
        repo.write(path, content, f"SEO : {plan['pr_title']}", branch, shas.get(path))
    pr = repo.open_pr(plan["pr_title"] or task["title"], branch, base, pr_body(plan, task, pages, settings.app_url))
    return {"status": "done", "pr_url": pr["url"], "pr_number": pr["number"], "branch": branch, "summary": plan.get("summary")}
