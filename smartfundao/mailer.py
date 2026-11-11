"""Envio de emails do Townly Up (ex.: o número de referência logo após comunicar uma ocorrência).

Três formas de envio, escolhidas pelas variáveis de ambiente:
  1. BREVO_API_KEY            → API HTTPS da Brevo (gratuita até 300 emails/dia). Recomendado online:
                                 o plano gratuito do Render bloqueia as portas SMTP.
  2. MAIL_SERVER (+ MAIL_PORT, MAIL_USERNAME, MAIL_PASSWORD) → SMTP clássico (ex.: Gmail com
                                 «palavra-passe de aplicação»), útil no computador.
  3. nenhuma                  → modo de desenvolvimento: o email é gravado em instance/outbox/
                                 (abre-se no navegador para ver como fica).
Remetente: MAIL_FROM (email verificado no fornecedor) e MAIL_FROM_NAME (por omissão «Townly Up»).

O envio é feito numa thread à parte: a pessoa não fica à espera e, se o email falhar,
a ocorrência continua registada (o erro fica no registo do servidor).
"""
import json
import logging
import os
import smtplib
import ssl
import threading
import urllib.error
import urllib.request
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr

from flask import render_template

log = logging.getLogger("townly.mail")
BREVO_URL = "https://api.brevo.com/v3/smtp/email"


def backend():
    if os.environ.get("BREVO_API_KEY"):
        return "brevo"
    if os.environ.get("MAIL_SERVER"):
        return "smtp"
    return "outbox"


def _sender():
    return (os.environ.get("MAIL_FROM_NAME", "Townly Up"),
            os.environ.get("MAIL_FROM", "nao-responder@townlyup.local"))


def _ssl():
    try:
        from app import _ssl_context
        return _ssl_context()
    except Exception:  # noqa: BLE001
        return ssl.create_default_context()


# ------------------------------------------------------------------ envio (cada forma)
def _send_brevo(to_email, to_name, subject, html, text):
    name, email = _sender()
    body = {"sender": {"name": name, "email": email}, "to": [{"email": to_email, "name": to_name}],
            "subject": subject, "htmlContent": html, "textContent": text}
    req = urllib.request.Request(BREVO_URL, data=json.dumps(body).encode(), method="POST", headers={
        "api-key": os.environ["BREVO_API_KEY"], "content-type": "application/json", "accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20, context=_ssl()) as resp:
        return resp.status


def _send_smtp(to_email, to_name, subject, html, text):
    msg = EmailMessage()
    msg["From"] = formataddr(_sender())
    msg["To"] = formataddr((to_name, to_email))
    msg["Subject"] = subject
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    port = int(os.environ.get("MAIL_PORT", "587"))
    server = os.environ["MAIL_SERVER"]
    if port == 465:
        smtp = smtplib.SMTP_SSL(server, port, timeout=20, context=_ssl())
    else:
        smtp = smtplib.SMTP(server, port, timeout=20)
        smtp.starttls(context=_ssl())
    with smtp:
        if os.environ.get("MAIL_USERNAME"):
            smtp.login(os.environ["MAIL_USERNAME"], os.environ.get("MAIL_PASSWORD", ""))
        smtp.send_message(msg)


def _send_outbox(to_email, to_name, subject, html, text, folder):
    os.makedirs(folder, exist_ok=True)
    name = datetime.now().strftime("%Y%m%d-%H%M%S-") + "".join(c if c.isalnum() else "_" for c in subject)[:60] + ".html"
    with open(os.path.join(folder, name), "w", encoding="utf-8") as fh:
        fh.write(f"<!-- Para: {to_name} <{to_email}> · Assunto: {subject} -->\n{html}")
    log.info("Email (modo de desenvolvimento) gravado em %s", os.path.join(folder, name))


def deliver(to_email, to_name, subject, html, text, outbox_folder):
    """Envia já, no processo atual. Devolve True/False (nunca lança)."""
    try:
        kind = backend()
        if kind == "brevo":
            _send_brevo(to_email, to_name, subject, html, text)
        elif kind == "smtp":
            _send_smtp(to_email, to_name, subject, html, text)
        else:
            _send_outbox(to_email, to_name, subject, html, text, outbox_folder)
        return True
    except (urllib.error.URLError, smtplib.SMTPException, OSError, KeyError, ValueError) as exc:
        log.warning("Falha ao enviar email para %s: %s", to_email, exc)
        return False


# Os testes substituem esta função para enviar de forma síncrona.
def send_async(*args):
    threading.Thread(target=deliver, args=args, daemon=True).start()


# ------------------------------------------------------------------ emails da aplicação
def incident_receipt(app, author, incident, incident_url):
    """Comprovativo imediato: número de referência, resumo e próximos passos."""
    maps = f"https://www.google.com/maps/search/?api=1&query={incident['latitude']},{incident['longitude']}"
    ctx = {"author": author, "incident": incident, "incident_url": incident_url, "maps_url": maps,
           "first_name": author["name"].split()[0]}
    subject = f"Ocorrência registada · {incident['ticket']}"
    html = render_template("emails/incident_receipt.html", **ctx)
    text = render_template("emails/incident_receipt.txt", **ctx)
    send_async(author["email"], author["name"], subject, html, text, os.path.join(app.instance_path, "outbox"))
