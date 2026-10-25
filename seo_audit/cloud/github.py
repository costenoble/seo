"""Application GitHub « Vigie SEO » : authentification de l'application (JWT), jetons d'installation,
lecture des dépôts et ouverture de pull requests. Aucune écriture directe sur la branche principale."""
import base64
import time

import httpx
import jwt

API = "https://api.github.com"
HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "vigie-seo"}


class GitHubError(Exception):
    pass


def load_private_key(value: str) -> str:
    """Accepte la clé PEM telle quelle, avec des \\n échappés, ou encodée en base64 (pratique pour Vercel)."""
    value = (value or "").strip().strip('"')
    if "BEGIN" in value:
        return value.replace("\\n", "\n")
    try:
        return base64.b64decode(value).decode()
    except ValueError:
        return value


class GitHubApp:
    def __init__(self, app_id: str, private_key: str, client_id: str = "", client_secret: str = ""):
        self.app_id, self.private_key = str(app_id), load_private_key(private_key)
        self.client_id, self.client_secret = client_id, client_secret

    def _app_jwt(self) -> str:
        now = int(time.time())
        return jwt.encode({"iat": now - 60, "exp": now + 540, "iss": self.app_id}, self.private_key, algorithm="RS256")

    def _request(self, method: str, path: str, token: str, **kw) -> httpx.Response:
        r = httpx.request(method, path if path.startswith("http") else API + path, timeout=30,
                          headers={**HEADERS, "Authorization": f"Bearer {token}"}, **kw)
        if r.status_code >= 400:
            msg = (r.json().get("message") if r.content and r.headers.get("content-type", "").startswith("application/json") else r.text) or ""
            raise GitHubError(f"GitHub {r.status_code} : {msg[:200]}")
        return r

    # -- liaison d'une installation à un utilisateur de Vigie SEO -------------------------------
    def user_installations(self, code: str) -> list[dict]:
        """Échange le code OAuth reçu à l'installation, puis liste les installations auxquelles l'utilisateur
        GitHub a accès : on ne lie une installation que si elle figure dans cette liste."""
        r = httpx.post("https://github.com/login/oauth/access_token", timeout=20, headers={"Accept": "application/json"},
                       data={"client_id": self.client_id, "client_secret": self.client_secret, "code": code})
        token = (r.json() if r.content else {}).get("access_token")
        if not token:
            raise GitHubError("GitHub a refusé l'autorisation, recommencez l'installation")
        data = self._request("GET", "/user/installations", token, params={"per_page": 100}).json()
        return [{"id": i["id"], "account": i["account"]["login"]} for i in data.get("installations", [])]

    # -- jetons d'installation et dépôts ------------------------------------------------------------
    def installation_token(self, installation_id: int) -> str:
        return self._request("POST", f"/app/installations/{installation_id}/access_tokens", self._app_jwt()).json()["token"]

    def repositories(self, installation_id: int) -> list[dict]:
        token, out, page = self.installation_token(installation_id), [], 1
        while True:
            data = self._request("GET", "/installation/repositories", token, params={"per_page": 100, "page": page}).json()
            out += [{"full_name": r["full_name"], "default_branch": r["default_branch"], "private": r["private"]} for r in data["repositories"]]
            if len(out) >= data.get("total_count", 0) or not data["repositories"]:
                return out
            page += 1


class Repo:
    """Opérations sur un dépôt avec un jeton d'installation."""

    def __init__(self, token: str, full_name: str):
        self.token, self.full_name = token, full_name

    def _req(self, method, path, **kw):
        r = httpx.request(method, f"{API}/repos/{self.full_name}{path}", timeout=30,
                          headers={**HEADERS, "Authorization": f"Bearer {self.token}"}, **kw)
        if r.status_code >= 400:
            msg = r.json().get("message", "") if r.content else ""
            raise GitHubError(f"GitHub {r.status_code} sur {path} : {msg[:200]}")
        return r.json() if r.content else {}

    def default_branch(self) -> str:
        return self._req("GET", "")["default_branch"]

    def head_sha(self, branch: str) -> str:
        return self._req("GET", f"/git/ref/heads/{branch}")["object"]["sha"]

    def tree(self, sha: str) -> list[str]:
        data = self._req("GET", f"/git/trees/{sha}", params={"recursive": "1"})
        return [t["path"] for t in data.get("tree", []) if t.get("type") == "blob"]

    def read(self, path: str, ref: str) -> tuple[str, str] | None:
        """(contenu texte, sha) ou None si le fichier n'existe pas."""
        try:
            data = self._req("GET", f"/contents/{path}", params={"ref": ref})
        except GitHubError as exc:
            if " 404 " in str(exc):
                return None
            raise
        return base64.b64decode(data["content"]).decode("utf-8", errors="replace"), data["sha"]

    def create_branch(self, name: str, sha: str) -> None:
        self._req("POST", "/git/refs", json={"ref": f"refs/heads/{name}", "sha": sha})

    def write(self, path: str, content: str, message: str, branch: str, sha: str | None) -> None:
        body = {"message": message, "content": base64.b64encode(content.encode()).decode(), "branch": branch}
        if sha:
            body["sha"] = sha
        self._req("PUT", f"/contents/{path}", json=body)

    def open_pr(self, title: str, head: str, base: str, body: str) -> dict:
        data = self._req("POST", "/pulls", json={"title": title, "head": head, "base": base, "body": body})
        return {"number": data["number"], "url": data["html_url"]}
