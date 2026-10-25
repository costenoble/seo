"""Configure l'authentification Supabase de Vigie SEO : envoi des e-mails par SMTP (Hostinger), modèles en
français aux couleurs du site, notifications de sécurité (2FA ajoutée / retirée).

Usage : set -a; . ./.env; set +a; python tools/supabase_auth_setup.py <référence-projet>
Le jeton d'accès est lu dans SUPABASE_ACCESS_TOKEN, sinon dans le trousseau du CLI Supabase (macOS)."""
import base64
import json
import os
import subprocess
import sys
import urllib.request

REF = sys.argv[1] if len(sys.argv) > 1 else "kpqzrmksmqaylzkcpetj"
INK, CREAM, GREEN, MUTED = "#0b0b0b", "#faf4f2", "#2bb673", "#6b6461"


def token() -> str:
    if os.environ.get("SUPABASE_ACCESS_TOKEN"):
        return os.environ["SUPABASE_ACCESS_TOKEN"]
    t = subprocess.run(["security", "find-generic-password", "-s", "Supabase CLI", "-w"], capture_output=True, text=True).stdout.strip()
    return base64.b64decode(t.split(":", 1)[1]).decode() if t.startswith("go-keyring-base64:") else t


def page(title: str, text: str, button: str | None = None, url: str = "{{ .ConfirmationURL }}", after: str = "") -> str:
    """Mise en page commune (logo en texte : toujours affiché, même si les images sont bloquées)."""
    btn = (f'<tr><td style="padding:8px 0 22px"><a href="{url}" style="display:inline-block;background:{GREEN};color:{INK};'
           f'font-family:Arial,sans-serif;font-weight:700;font-size:15px;text-decoration:none;padding:14px 28px;border-radius:99px">{button}</a></td></tr>') if button else ""
    return f'''<!doctype html><html lang="fr"><body style="margin:0;padding:0;background:{CREAM}">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{CREAM}"><tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="560" cellpadding="0" cellspacing="0" style="width:100%;max-width:560px">
<tr><td style="background:{INK};border-radius:24px;padding:30px 32px">
<div style="font-family:Arial,sans-serif;font-size:24px;font-weight:700;color:{CREAM};margin:0 0 22px">vigie <span style="color:{GREEN}">seo</span></div>
<h1 style="margin:0 0 12px;font-family:Arial,sans-serif;font-size:26px;line-height:1.2;color:{CREAM}">{title}</h1>
<p style="margin:0;font-family:Arial,sans-serif;font-size:16px;line-height:1.55;color:#c9c1bd">{text}</p></td></tr>
<tr><td style="height:18px"></td></tr>
<tr><td style="background:#ffffff;border-radius:20px;padding:26px 28px"><table role="presentation" width="100%">{btn}
<tr><td style="font-family:Arial,sans-serif;font-size:13px;line-height:1.6;color:{MUTED}">{after}</td></tr></table></td></tr>
<tr><td style="padding:16px 8px;text-align:center;font-family:Arial,sans-serif;font-size:12px;color:{MUTED}">Vigie SEO · e-mail automatique, merci de ne pas y répondre.</td></tr>
</table></td></tr></table></body></html>'''


ignore = "Si vous n'êtes pas à l'origine de cette demande, ignorez simplement cet e-mail."
config = {
    "smtp_host": os.environ["SMTP_HOST"], "smtp_port": os.environ.get("SMTP_PORT", "465"),
    "smtp_user": os.environ["SMTP_USER"], "smtp_pass": os.environ["SMTP_PASSWORD"],
    "smtp_admin_email": os.environ["EMAIL_FROM"].split("<")[-1].strip(" >"), "smtp_sender_name": "Vigie SEO",
    "rate_limit_email_sent": 30, "password_min_length": 8,
    "mailer_subjects_confirmation": "Confirmez votre adresse e-mail",
    "mailer_templates_confirmation_content": page("Bienvenue sur Vigie SEO 👋", "Il ne reste qu'à confirmer votre adresse e-mail pour activer votre compte.",
                                                   "Confirmer mon adresse", after=f"Le lien est valable 24 heures. {ignore}"),
    "mailer_subjects_recovery": "Réinitialisez votre mot de passe",
    "mailer_templates_recovery_content": page("Nouveau mot de passe", "Vous avez demandé à réinitialiser le mot de passe de votre compte Vigie SEO.",
                                               "Choisir un nouveau mot de passe", after=f"Le lien est valable 1 heure. {ignore}"),
    "mailer_subjects_magic_link": "Votre lien de connexion",
    "mailer_templates_magic_link_content": page("Votre lien de connexion", "Cliquez pour vous connecter à Vigie SEO.", "Me connecter", after=ignore),
    "mailer_subjects_email_change": "Confirmez votre nouvelle adresse e-mail",
    "mailer_templates_email_change_content": page("Changement d'adresse e-mail", "Confirmez la nouvelle adresse {{ .NewEmail }} pour votre compte Vigie SEO.",
                                                   "Confirmer la nouvelle adresse", after=ignore),
    "mailer_subjects_reauthentication": "Votre code de vérification : {{ .Token }}",
    "mailer_templates_reauthentication_content": page("Code de vérification", "Saisissez ce code dans Vigie SEO :",
                                                       after=f'<div style="font-size:32px;letter-spacing:8px;font-weight:700;color:{INK};padding:6px 0 14px">{{{{ .Token }}}}</div>{ignore}'),
    "mailer_notifications_mfa_factor_enrolled_enabled": True,
    "mailer_subjects_mfa_factor_enrolled_notification": "Double authentification activée",
    "mailer_templates_mfa_factor_enrolled_notification_content": page("Double authentification activée ✓", "Votre compte Vigie SEO est désormais protégé par un code à 6 chiffres à chaque connexion.",
                                                                       after="Si vous n'avez pas fait ce changement, changez votre mot de passe et contactez-nous immédiatement."),
    "mailer_notifications_mfa_factor_unenrolled_enabled": True,
    "mailer_subjects_mfa_factor_unenrolled_notification": "Double authentification désactivée",
    "mailer_templates_mfa_factor_unenrolled_notification_content": page("Double authentification désactivée", "La double authentification a été retirée de votre compte Vigie SEO.",
                                                                         after="Si vous n'avez pas fait ce changement, changez votre mot de passe immédiatement."),
}
req = urllib.request.Request(f"https://api.supabase.com/v1/projects/{REF}/config/auth", data=json.dumps(config).encode(), method="PATCH",
                             headers={"Authorization": f"Bearer {token()}", "Content-Type": "application/json"})
res = json.load(urllib.request.urlopen(req, timeout=30))
print("SMTP :", res.get("smtp_host"), res.get("smtp_admin_email"), "| e-mails/heure :", res.get("rate_limit_email_sent"),
      "| 2FA TOTP :", res.get("mfa_totp_enroll_enabled"), "| sujet confirmation :", res.get("mailer_subjects_confirmation"))
