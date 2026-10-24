"""Petit site local volontairement défectueux, pour tester l'audit de bout en bout."""
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOREM = " ".join(["mot"] * 250)


def page(title="Page de test correcte pour l'audit SEO", desc=None, body="", head="", lang="fr"):
    desc = desc if desc is not None else ("Une description suffisamment longue pour passer les "
                                          "contrôles de longueur de l'outil d'audit SEO.")
    t = f"<title>{title}</title>" if title is not None else ""
    d = f'<meta name="description" content="{desc}">' if desc else ""
    return (f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width">{t}{d}{head}</head>'
            f"<body>{body}<p>{LOREM}</p></body></html>")


def build_routes(base: str) -> dict:
    canon = lambda path: f'<link rel="canonical" href="{base}{path}">'
    return {
        "/robots.txt": (200, {"Content-Type": "text/plain"},
                        f"User-agent: *\nDisallow: /prive/\nSitemap: {base}/sitemap.xml\n"),
        "/sitemap.xml": (200, {"Content-Type": "application/xml"},
                         '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                         + "".join(f"<url><loc>{base}{p}</loc></url>"
                                   for p in ("/", "/orpheline", "/noindex", "/disparue", "/prive/x"))
                         + "</urlset>"),
        "/": (200, {}, page("Accueil du site de test pour l'audit SEO", head=canon("/"), body="""
              <h1>Accueil</h1>
              <a href="/a">A</a> <a href="/sans-titre">B</a> <a href="/doublon-1">C</a>
              <a href="/doublon-2">D</a> <a href="/noindex">E</a> <a href="/disparue">F</a>
              <a href="/redir-1">G</a> <a href="/prive/x">H</a> <a href="/canon-cassee">I</a>
              <a href="/x-robots">J</a> <a href="/a?utm_source=news">A bis</a>
              <a href="http://localhost:{port}/externe-cassee">ext</a> <a href="mailto:a@b.c">mail</a>""")),
        "/a": (200, {}, page(desc="", head=canon("/a"), body="<h1>Un</h1><h1>Deux</h1><img src='x.png'>")),
        "/sans-titre": (200, {}, page(title=None, head=canon("/sans-titre"), body="<h1>Sans titre</h1>")),
        "/doublon-1": (200, {}, page("Titre en double sur deux pages du site", head=canon("/doublon-1"), body="<h1>D1</h1>")),
        "/doublon-2": (200, {}, page("Titre en double sur deux pages du site", head=canon("/doublon-2"), body="<h1>D2</h1>")),
        "/noindex": (200, {}, page(head='<meta name="robots" content="noindex, follow">', body="<h1>N</h1>")),
        "/x-robots": (200, {"X-Robots-Tag": "noindex"}, page(head=canon("/x-robots"), body="<h1>X</h1>")),
        "/canon-cassee": (200, {}, page(head=canon("/disparue"), body="<h1>C</h1>")),
        "/orpheline": (200, {}, page(head=canon("/orpheline"), body="<h1>O</h1>")),
        "/redir-1": (301, {"Location": "/redir-2"}, ""),
        "/redir-2": (302, {"Location": "/a"}, ""),
    }


class Handler(BaseHTTPRequestHandler):
    routes: dict = {}

    def do_GET(self):
        path = self.path.split("?")[0]
        status, headers, body = self.routes.get(path, (404, {}, page("Introuvable", body="<h1>404</h1>")))
        body = body.replace("{port}", str(self.server.server_address[1])).encode()
        self.send_response(status)
        self.send_header("Content-Type", headers.pop("Content-Type", "text/html; charset=utf-8")
                         if "Content-Type" in headers else "text/html; charset=utf-8")
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_HEAD = do_GET

    def log_message(self, *args):
        pass


def start_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    Handler.routes = build_routes(base)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, base


if __name__ == "__main__":
    srv, url = start_server()
    print("Site de test sur", url, "(Ctrl+C pour arrêter)")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        srv.shutdown()
