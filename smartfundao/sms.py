"""SMS do Townly Up: avisa o responsável de cada freguesia quando é comunicada uma ocorrência na sua zona.

Envio (variáveis de ambiente):
  BREVO_API_KEY  → API de SMS transacionais da Brevo (a mesma chave do email; precisa de créditos SMS).
  SMS_SENDER     → remetente mostrado no telemóvel (até 11 letras/números; por omissão «TownlyUp»).
  sem chave      → modo de desenvolvimento: a SMS é gravada em instance/sms_outbox/ para se ver como fica.

A mensagem é escrita sem acentos para caber numa única SMS (160 caracteres no alfabeto GSM):
com acentos como «ã» ou «ó» cada SMS só leva 70 caracteres e o custo duplica ou triplica.
"""
import json
import logging
import os
import re
import threading
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime

log = logging.getLogger("townly.sms")
BREVO_SMS_URL = "https://api.brevo.com/v3/transactionalSMS/send"
MAX_LEN = 160


# ------------------------------------------------------------------ números
def normalize_phone(value):
    """Aceita «912 345 678», «+351 912345678» ou «00351…» e devolve «+351912345678» (ou None se inválido).
    Só telemóveis portugueses (começam por 9): são os únicos que recebem SMS."""
    digits = re.sub(r"[^\d+]", "", value or "")
    if digits.startswith("00"):
        digits = "+" + digits[2:]
    if digits.startswith("+351"):
        digits = digits[4:]
    elif digits.startswith("351") and len(digits) == 12:
        digits = digits[3:]
    if re.fullmatch(r"9[1236]\d{7}", digits):
        return "+351" + digits
    return None


def pretty_phone(phone):
    """+351912345678 → «+351 912 345 678»."""
    if not phone or not phone.startswith("+351") or len(phone) != 13:
        return phone or ""
    n = phone[4:]
    return f"+351 {n[:3]} {n[3:6]} {n[6:]}"


# ------------------------------------------------------------------ texto
def gsm(text):
    """Tira acentos e caracteres fora do alfabeto GSM (ex.: «Fundão» → «Fundao»)."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.replace("·", "-").replace("–", "-").replace("—", "-").replace("«", '"').replace("»", '"')
    return re.sub(r"[^\x20-\x7E\n]", "", text)


def new_incident_text(incident, parish_name, url):
    """«Townly Up: nova ocorrencia TU-2026-0042 em Fundao. Iluminacao publica - Candeeiro apagado. Ver: …»
    O título é encurtado para a mensagem nunca passar de 160 caracteres."""
    head = gsm(f"Townly Up: nova ocorrencia {incident['ticket']} em {parish_name}. {incident['category']} - ")
    tail = gsm(f". Ver: {url}")
    room = MAX_LEN - len(head) - len(tail)
    title = gsm(incident["title"]).strip()
    if room < 8:                       # endereço muito comprido: sacrifica-se o título
        return (head.rstrip(" -") + tail)[:MAX_LEN]
    if len(title) > room:
        title = title[: room - 3].rstrip() + "..."
    return head + title + tail


# ------------------------------------------------------------------ envio
def backend():
    return "brevo" if os.environ.get("BREVO_API_KEY") else "outbox"


def _send_brevo(phone, text):
    body = {"sender": os.environ.get("SMS_SENDER", "TownlyUp")[:11], "recipient": phone.lstrip("+"),
            "content": text, "type": "transactional", "tag": "nova-ocorrencia"}
    req = urllib.request.Request(BREVO_SMS_URL, data=json.dumps(body).encode(), method="POST", headers={
        "api-key": os.environ["BREVO_API_KEY"], "content-type": "application/json", "accept": "application/json"})
    from mailer import _ssl
    with urllib.request.urlopen(req, timeout=20, context=_ssl()) as resp:
        return resp.status


def _send_outbox(phone, text, folder):
    os.makedirs(folder, exist_ok=True)
    name = datetime.now().strftime("%Y%m%d-%H%M%S-%f") + "-" + phone.lstrip("+") + ".txt"
    with open(os.path.join(folder, name), "w", encoding="utf-8") as fh:
        fh.write(f"Para: {pretty_phone(phone)}\nRemetente: {os.environ.get('SMS_SENDER', 'TownlyUp')[:11]}\n"
                 f"Caracteres: {len(text)}/{MAX_LEN}\n\n{text}\n")
    log.info("SMS (modo de desenvolvimento) gravada em %s", os.path.join(folder, name))


def deliver(recipients, text, outbox_folder):
    """Envia a mesma SMS a vários números. Nunca lança: um erro fica só no registo."""
    sent = 0
    for phone in recipients:
        try:
            if backend() == "brevo":
                _send_brevo(phone, text)
            else:
                _send_outbox(phone, text, outbox_folder)
            sent += 1
        except (urllib.error.URLError, OSError, KeyError, ValueError) as exc:
            log.warning("Falha ao enviar SMS para %s: %s", phone, exc)
    return sent


# Os testes substituem esta função para enviar de forma síncrona.
def send_async(*args):
    threading.Thread(target=deliver, args=args, daemon=True).start()


def parish_recipients(db, parish_code):
    """Telemóveis dos responsáveis ativos da Junta da freguesia que querem receber SMS."""
    return [r["phone"] for r in db.execute(
        """SELECT DISTINCT u.phone FROM users u JOIN organizations o ON o.id = u.organization_id
            WHERE o.kind = 'junta' AND o.parish_code = ? AND u.role = 'staff' AND u.active = TRUE
              AND u.sms_alerts = TRUE AND u.phone IS NOT NULL""", (parish_code,)).fetchall()]


def new_incident_alert(app, db, incident, parish_name, url):
    """Chamado logo a seguir a gravar uma ocorrência."""
    recipients = parish_recipients(db, incident["parish_code"])
    if recipients:
        send_async(recipients, new_incident_text(incident, parish_name, url),
                   os.path.join(app.instance_path, "sms_outbox"))
    return len(recipients)
