"""E-mails transactionnels via Resend : mise en page aux couleurs de Vigie SEO (HTML en tableaux,
styles en ligne pour les messageries), résumé hebdomadaire, alertes, désinscription signée."""
import base64
import hashlib
import hmac
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr
from html import escape
from pathlib import Path

import httpx

# Logo joint à l'e-mail (image intégrée « cid: ») : affiché même quand la messagerie bloque les images externes.
LOGO_CID = "vigie-logo"
LOGO_PNG = Path(__file__).parent / "assets" / "logo-email.png"
INK, CREAM, GREEN, BLUE, PINK, MINT, MUTED = "#0b0b0b", "#faf4f2", "#2bb673", "#d2edff", "#ffc3c3", "#d9f4e6", "#6b6461"
HEAD = "Poppins,'Helvetica Neue',Arial,sans-serif"
BODY = "Lato,'Helvetica Neue',Arial,sans-serif"


class EmailError(Exception):
    pass


def enabled(settings) -> bool:
    return bool(settings.smtp_host or settings.resend_api_key)


def send(settings, to: str, subject: str, html: str, text: str, unsubscribe_url: str = "") -> str:
    """Envoie par SMTP (ex. Hostinger) si configuré, sinon par l'API Resend."""
    if settings.smtp_host:
        return send_smtp(settings, to, subject, html, text, unsubscribe_url)
    return send_resend(settings.resend_api_key, settings.email_from, to, subject, html, text, unsubscribe_url)


def send_smtp(settings, to: str, subject: str, html: str, text: str, unsubscribe_url: str = "") -> str:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = settings.email_from, to, subject
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain=parseaddr(settings.email_from)[1].split("@")[-1] or None)
    if unsubscribe_url:
        msg["List-Unsubscribe"] = f"<{unsubscribe_url}>"
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    if f"cid:{LOGO_CID}" in html:
        msg.get_body(("html",)).add_related(LOGO_PNG.read_bytes(), maintype="image", subtype="png",
                                             cid=f"<{LOGO_CID}>", filename="vigie-seo.png", disposition="inline")
    try:
        if settings.smtp_port == 465:
            server = smtplib.SMTP_SSL(settings.smtp_host, 465, context=ssl.create_default_context(), timeout=20)
        else:
            server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20)
            server.starttls(context=ssl.create_default_context())
        with server:
            server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)
    except (smtplib.SMTPException, OSError) as exc:
        raise EmailError(f"Envoi SMTP impossible : {type(exc).__name__}: {exc}") from exc
    return msg["Message-ID"]


def send_resend(api_key: str, sender: str, to: str, subject: str, html: str, text: str, unsubscribe_url: str = "") -> str:
    headers = {}
    if unsubscribe_url:
        headers = {"List-Unsubscribe": f"<{unsubscribe_url}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}
    try:
        r = httpx.post("https://api.resend.com/emails", timeout=20, headers={"Authorization": f"Bearer {api_key}"},
                       json={"from": sender, "to": [to], "subject": subject, "html": html, "text": text, "headers": headers,
                             "attachments": [{"filename": "vigie-seo.png", "content_id": LOGO_CID,
                                              "content": base64.b64encode(LOGO_PNG.read_bytes()).decode()}]
                             if f"cid:{LOGO_CID}" in html else []})
    except httpx.HTTPError as exc:
        raise EmailError(f"Envoi impossible : {type(exc).__name__}") from exc
    if r.status_code >= 300:
        raise EmailError((r.json() if r.content else {}).get("message") or f"Erreur Resend ({r.status_code})")
    return r.json().get("id", "")


# -- désinscription en un clic (lien signé, sans connexion) -------------------------------------
def sign(secret: str, user_id: str, kind: str) -> str:
    mac = hmac.new(secret.encode(), f"{user_id}:{kind}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac[:18]).decode().rstrip("=")


def verify(secret: str, user_id: str, kind: str, token: str) -> bool:
    return hmac.compare_digest(sign(secret, user_id, kind), token or "")


def unsubscribe_url(app_url: str, secret: str, user_id: str, kind: str) -> str:
    return f"{app_url}/api/unsubscribe?u={user_id}&k={kind}&t={sign(secret, user_id, kind)}"


# -- mise en page --------------------------------------------------------------------------------
def _p(text, size=15, color=INK, weight=400, margin="0 0 10px", font=BODY):
    return f'<p style="margin:{margin};font-family:{font};font-size:{size}px;line-height:1.5;color:{color};font-weight:{weight}">{text}</p>'


def button(href: str, label: str, bg=GREEN, color=INK) -> str:
    return (f'<a href="{escape(href)}" style="display:inline-block;background:{bg};color:{color};font-family:{HEAD};font-weight:700;'
            f'font-size:15px;text-decoration:none;padding:13px 26px;border-radius:99px">{escape(label)}</a>')


def card(title: str, inner: str, tint: str = "#ffffff") -> str:
    return (f'<tr><td style="padding:0 0 16px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            f'style="background:{tint};border-radius:20px"><tr><td style="padding:24px 26px">'
            f'<h2 style="margin:0 0 14px;font-family:{HEAD};font-size:19px;line-height:1.2;color:{INK}">{escape(title)}</h2>'
            f'{inner}</td></tr></table></td></tr>')


def rows(items: list[tuple[str, str]]) -> str:
    """Lignes « libellé — valeur » séparées par un filet."""
    out = "".join(
        f'<tr><td style="padding:9px 0;border-top:1px solid rgba(0,0,0,.08);font-family:{BODY};font-size:14px;color:{INK}">{label}</td>'
        f'<td align="right" style="padding:9px 0 9px 12px;border-top:1px solid rgba(0,0,0,.08);font-family:{HEAD};font-size:14px;'
        f'font-weight:700;color:{INK};white-space:nowrap">{value}</td></tr>' for label, value in items)
    return f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{out}</table>'


def badge(value, good_when_positive=True, suffix="") -> str:
    if value is None or value == 0:
        return f'<span style="color:{MUTED}">=</span>'
    good = (value > 0) == good_when_positive
    bg, fg = ("#c9f0dc", "#0b5a33") if good else ("#ffd9d9", "#8d1f1f")
    arrow = "▲" if value > 0 else "▼"
    sign_ = "+" if value > 0 else ""
    return (f'<span style="background:{bg};color:{fg};border-radius:99px;padding:2px 9px;font-size:12px">'
            f'{arrow} {sign_}{escape(str(value))}{suffix}</span>')


def layout(app_url: str, title: str, intro: str, blocks: str, cta: tuple[str, str] | None, footer: str, preheader: str) -> str:
    cta_html = f'<tr><td align="center" style="padding:8px 0 28px">{button(*cta)}</td></tr>' if cta else ""
    return f'''<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light"><title>{escape(title)}</title></head>
<body style="margin:0;padding:0;background:{CREAM}">
<div style="display:none;max-height:0;overflow:hidden;opacity:0">{escape(preheader)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{CREAM}"><tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="width:100%;max-width:600px">
<tr><td style="background:{INK};border-radius:26px;padding:30px 32px 34px">
<img src="cid:{LOGO_CID}" width="160" height="38" alt="Vigie SEO" style="display:block;border:0;margin:0 0 26px">
<h1 style="margin:0 0 10px;font-family:{HEAD};font-size:30px;line-height:1.15;color:{CREAM};letter-spacing:-.5px">{escape(title)}</h1>
{_p(intro, 16, "#c9c1bd", margin="0")}</td></tr>
<tr><td style="height:18px"></td></tr>
{blocks}
{cta_html}
<tr><td style="padding:8px 8px 0;text-align:center">{_p(footer, 12, MUTED)}</td></tr>
</table></td></tr></table></body></html>'''


# -- résumé hebdomadaire --------------------------------------------------------------------------
def digest(app_url: str, name: str, data: dict, unsubscribe: str) -> tuple[str, str, str]:
    """Renvoie (sujet, html, texte)."""
    blocks, text = [], [f"Bonjour {name},", "", "Votre semaine SEO avec Vigie SEO.", ""]
    if data.get("tasks"):
        items = [(f"{escape(t['title'])}<br><span style=\"color:{MUTED};font-size:12px\">{escape(t['site'])}</span>",
                  "Priorité haute" if t["priority"] == "high" else "À prévoir") for t in data["tasks"]]
        blocks.append(card("À faire cette semaine", rows(items) + f'<div style="padding-top:16px">{button(app_url + "/#/plan", "Ouvrir le plan d’action", INK, "#ffffff")}</div>', PINK))
        text += ["À faire cette semaine :"] + [f"- {t['title']} ({t['site']})" for t in data["tasks"]] + [""]
    if data.get("projects"):
        items = [(escape(p["site"]) + (f' <span style="color:{MUTED};font-size:12px">· {p["open"]} tâche(s)</span>' if p["open"] else ""),
                  f'{p["score"]}/100 &nbsp;{badge(p["delta"])}') for p in data["projects"]]
        blocks.append(card("Santé de vos sites", rows(items), "#ffffff"))
        text += ["Santé de vos sites :"] + [f"- {p['site']} : {p['score']}/100" for p in data["projects"]] + [""]
    for g in data.get("gsc", []):
        inner = rows([("Clics (7 jours)", f'{g["clicks"]:,}'.replace(",", " ") + f' &nbsp;{badge(g["clicks_pct"], suffix=" %")}'),
                      ("Impressions", f'{g["impressions"]:,}'.replace(",", " ") + f' &nbsp;{badge(g["impressions_pct"], suffix=" %")}')])
        blocks.append(card(f"Search Console · {g['site']}", inner, BLUE))
        text += [f"Search Console {g['site']} : {g['clicks']} clics ({g['clicks_pct']:+} %), {g['impressions']} impressions"
                 if g["clicks_pct"] is not None else f"Search Console {g['site']} : {g['clicks']} clics", ""]
    for t in data.get("tracking", []):
        movers = [(escape(m["keyword"]), f'{m["now"] or "&gt; 30"} &nbsp;{badge(m["change"])}') for m in t["movers"]]
        head = [("Visibilité", f'{t["visibility"]} % &nbsp;{badge(t["visibility_change"], suffix=" pt")}')] if t["visibility"] is not None else []
        blocks.append(card(f"Positions suivies · {t['site']}", rows(head + movers), MINT))
        text += [f"Positions {t['site']} : visibilité {t['visibility']} %"] + [f"- {m['keyword']} : {m['now']}" for m in t["movers"]] + [""]
    subject = "Votre semaine SEO" + (f" : {len(data['tasks'])} action(s) prioritaire(s)" if data.get("tasks") else "")
    footer = (f'Vous recevez ce résumé chaque lundi. <a href="{escape(unsubscribe)}" style="color:{MUTED}">Ne plus le recevoir</a>'
              f' · <a href="{app_url}/#/compte" style="color:{MUTED}">Gérer mes e-mails</a>')
    html = layout(app_url, f"Bonjour {name}, voici votre semaine.", "Ce qui a bougé sur vos sites ces 7 derniers jours, et ce qu’il faut faire maintenant.",
                  "".join(blocks), (app_url, "Ouvrir Vigie SEO"), footer, subject)
    text += [f"Ouvrir Vigie SEO : {app_url}", f"Ne plus recevoir ce résumé : {unsubscribe}"]
    return subject, html, "\n".join(text)


# -- alertes -------------------------------------------------------------------------------------------
def alerts(app_url: str, name: str, items: list[dict], unsubscribe: str) -> tuple[str, str, str]:
    bad = [a for a in items if a["tone"] == "bad"]
    subject = (f"⚠ {bad[0]['title']}" if len(items) == 1 and bad else
               f"{len(items)} alerte{'s' if len(items) > 1 else ''} sur vos sites" if bad else f"Bonne nouvelle : {items[0]['title']}")
    blocks = []
    for a in items:
        inner = _p(escape(a["detail"]), 14) if a["detail"] else ""
        if a.get("link"):
            inner += f'<div style="padding-top:6px">{button(app_url + a["link"], "Voir le détail", INK, "#ffffff")}</div>'
        blocks.append(card(("⚠ " if a["tone"] == "bad" else "✓ ") + a["title"] + f" · {a['site']}", inner, PINK if a["tone"] == "bad" else MINT))
    footer = (f'Alertes envoyées au plus une fois par heure. <a href="{escape(unsubscribe)}" style="color:{MUTED}">Ne plus recevoir d’alertes</a>'
              f' · <a href="{app_url}/#/compte" style="color:{MUTED}">Gérer mes e-mails</a>')
    html = layout(app_url, "Du nouveau sur vos sites" if bad else "Bonne nouvelle !",
                  f"Bonjour {escape(name)}, voici ce que Vigie SEO a repéré.", "".join(blocks), (app_url, "Ouvrir Vigie SEO"), footer, subject)
    text = "\n".join([f"Bonjour {name},", ""] + [f"- {a['title']} ({a['site']}) : {a['detail']}" for a in items]
                     + ["", f"Ouvrir Vigie SEO : {app_url}", f"Ne plus recevoir d'alertes : {unsubscribe}"])
    return subject, html, text
