"""Réglages lus dans les variables d'environnement (Vercel → Settings → Environment Variables)."""
import os
from dataclasses import dataclass

APP_NAME = "Vigie SEO"


def _local_pem() -> str:
    """Développement local : clé privée de l'application GitHub déposée dans secrets/."""
    from pathlib import Path
    pems = sorted(Path("secrets").glob("*.pem")) if Path("secrets").is_dir() else []
    return pems[0].read_text() if pems else ""


@dataclass(frozen=True)
class Settings:
    database_url: str
    supabase_url: str
    supabase_anon_key: str
    supabase_jwt_secret: str = ""        # projets Supabase anciens (HS256) ; sinon clés publiques JWKS
    supabase_service_role_key: str = ""  # suppression de compte (API admin)
    google_client_id: str = ""
    google_client_secret: str = ""
    token_key: str = ""                  # clé Fernet pour chiffrer les jetons Google
    cron_secret: str = ""
    dataforseo_login: str = ""           # données de marché (volumes, difficulté…) — facultatif
    dataforseo_password: str = ""
    seranking_api_key: str = ""          # analyse de domaines et backlinks — facultatif
    resend_api_key: str = ""             # e-mails (résumé hebdomadaire, alertes) — facultatif
    email_from: str = "Vigie SEO <onboarding@resend.dev>"
    smtp_host: str = ""                  # envoi par SMTP (prioritaire sur Resend), ex. smtp.hostinger.com
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_password: str = ""
    app_url: str = "https://vigie-seo.vercel.app"
    anthropic_api_key: str = ""          # rédaction des corrections automatiques — facultatif
    github_app_id: str = ""              # application GitHub « Vigie SEO » — facultatif
    github_app_slug: str = ""
    github_client_id: str = ""
    github_client_secret: str = ""
    github_private_key: str = ""         # PEM (ou base64) ; en local : fichier secrets/*.pem
    step_budget: float = 40.0            # secondes de travail par appel, sous la durée max de la fonction
    allow_private_urls: bool = False     # développement local uniquement
    serve_static: bool = False           # développement local : Flask sert aussi public/

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ.get
        return cls(
            database_url=env("DATABASE_URL", ""),
            supabase_url=env("SUPABASE_URL", "").rstrip("/"),
            supabase_anon_key=env("SUPABASE_ANON_KEY", ""),
            supabase_jwt_secret=env("SUPABASE_JWT_SECRET", ""),
            supabase_service_role_key=env("SUPABASE_SERVICE_ROLE_KEY", ""),
            google_client_id=env("GOOGLE_CLIENT_ID", ""),
            google_client_secret=env("GOOGLE_CLIENT_SECRET", ""),
            token_key=env("TOKEN_ENCRYPTION_KEY", ""),
            cron_secret=env("CRON_SECRET", ""),
            dataforseo_login=env("DATAFORSEO_LOGIN", ""),
            dataforseo_password=env("DATAFORSEO_PASSWORD", ""),
            seranking_api_key=env("SERANKING_API_KEY", "").strip().strip('"'),
            resend_api_key=env("RESEND_API_KEY", "").strip().strip('"'),
            email_from=env("EMAIL_FROM", "Vigie SEO <onboarding@resend.dev>"),
            smtp_host=env("SMTP_HOST", ""),
            smtp_port=int(env("SMTP_PORT", "465")),
            smtp_user=env("SMTP_USER", ""),
            smtp_password=env("SMTP_PASSWORD", ""),
            app_url=env("APP_URL", "https://vigie-seo.vercel.app").rstrip("/"),
            anthropic_api_key=env("ANTHROPIC_API_KEY", ""),
            github_app_id=env("GITHUB_APP_ID", ""),
            github_app_slug=env("GITHUB_APP_SLUG", ""),
            github_client_id=env("GITHUB_CLIENT_ID", ""),
            github_client_secret=env("GITHUB_CLIENT_SECRET", ""),
            github_private_key=env("GITHUB_PRIVATE_KEY", "") or _local_pem(),
            step_budget=float(env("STEP_BUDGET", "40")),
            allow_private_urls=env("ALLOW_PRIVATE_URLS", "") == "1",
            serve_static=env("SERVE_STATIC", "") == "1",
        )

    def missing(self) -> list[str]:
        required = {"DATABASE_URL": self.database_url, "SUPABASE_URL": self.supabase_url,
                    "SUPABASE_ANON_KEY": self.supabase_anon_key, "TOKEN_ENCRYPTION_KEY": self.token_key,
                    "CRON_SECRET": self.cron_secret}
        return [k for k, v in required.items() if not v]
