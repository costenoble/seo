import pytest

from seo_audit.cloud import fixer

NEXT = ["package.json", "app/layout.tsx", "app/page.tsx", "app/(site)/tarifs/page.tsx", "app/blog/[slug]/page.tsx"]

REACT_APP = """import { Routes, Route } from 'react-router-dom'
import Home from './pages/Home.jsx'
import Services from './pages/Services'
const Article = lazy(() => import('./pages/Article.jsx'))

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Home />} />
      <Route element={<Services />} path="/services" />
      <Route path="/blog/:slug" element={<Article />} />
      <Route path="*" element={<NotFound />} />
    </Routes>
  )
}
"""
REACT = ["package.json", "index.html", "src/App.jsx", "src/pages/Home.jsx", "src/pages/Services.jsx", "src/pages/Article.jsx",
         "src/components/Header.jsx", "src/components/PageMeta.jsx", "src/lib/seo.js"]


def test_detect_framework():
    assert fixer.detect_framework(NEXT, '{"dependencies": {"next": "15"}}') == "next"
    assert fixer.detect_framework(REACT, '{"dependencies": {"react": "18", "react-router-dom": "6"}}') == "react"
    assert fixer.detect_framework(["index.html"], None) == "html"
    assert fixer.detect_framework(["main.py"], None) == "unknown"


def test_locate_next_app_router():
    assert fixer.locate("next", NEXT, "https://x.fr/tarifs") == ["app/(site)/tarifs/page.tsx", "app/layout.tsx"]
    assert fixer.locate("next", NEXT, "https://x.fr/blog/mon-article")[0] == "app/blog/[slug]/page.tsx"
    assert fixer.locate("next", NEXT, "https://x.fr/")[0] == "app/page.tsx"


def test_locate_react_router():
    routes = fixer.react_routes(REACT, lambda p: REACT_APP if p == "src/App.jsx" else "")
    assert routes == {"/": "src/pages/Home.jsx", "/services": "src/pages/Services.jsx", "/blog/:slug": "src/pages/Article.jsx"}
    found = fixer.locate("react", REACT, "https://x.fr/services/", routes)
    # la page, puis les fichiers du <head> (seo.js d'abord : il ajoute le suffixe de marque) ; pas le Header
    assert found == ["src/pages/Services.jsx", "src/lib/seo.js", "src/components/PageMeta.jsx"]
    assert fixer.locate("react", REACT, "https://x.fr/blog/un-article", routes)[0] == "src/pages/Article.jsx"
    assert fixer.locate("react", REACT, "https://x.fr/inconnue", routes) == []
    assert fixer.root_files("react", REACT)[0] == "index.html"


def test_apply_plan_is_strict():
    files = {"a.jsx": "title='A'\ntitle='B'\n"}
    plan = {"files": [{"path": "a.jsx", "mode": "edit", "content": "", "edits": [{"search": "title='A'", "replace": "title='AA'"}]}]}
    assert fixer.apply_plan(plan, files, {"a.jsx"}) == {"a.jsx": "title='AA'\ntitle='B'\n"}
    with pytest.raises(fixer.FixError, match="ambigu"):
        fixer.apply_plan({"files": [{"path": "a.jsx", "mode": "edit", "content": "", "edits": [{"search": "title=", "replace": "x"}]}]}, files, {"a.jsx"})
    with pytest.raises(fixer.FixError, match="refusé"):
        fixer.apply_plan({"files": [{"path": "../etc/x", "mode": "create", "content": "x", "edits": []}]}, files, set())
    with pytest.raises(fixer.FixError, match="non fourni"):
        fixer.apply_plan({"files": [{"path": "b.jsx", "mode": "edit", "content": "", "edits": [{"search": "x", "replace": "y"}]}]}, files, {"b.jsx"})
