"""Comprovativo por email com o número de referência, enviado logo após comunicar."""
import io
import json

import mailer
from test_flow import app, client, create_incident, login  # noqa: F401


def test_email_com_numero_de_referencia(client, app, sent_emails):
    login(client, "ana@teste.pt")
    incident_id = create_incident(client)
    assert len(sent_emails) == 1
    to_email, to_name, subject, html, text, _folder = sent_emails[0]
    assert to_email == "ana@teste.pt" and to_name == "Ana"
    assert subject.startswith("Ocorrência registada · TU-")
    ticket = subject.split("· ")[1]
    assert ticket in html and ticket in text
    assert f"/incidents/{incident_id}" in html and "Obras Municipais" in html
    page = client.get(f"/incidents/{incident_id}/enviada").get_data(as_text=True)
    assert "Enviámos este número para" in page and "an•@teste.pt" in page


def test_sem_email_quando_o_envio_falha_validacao(client, sent_emails):
    login(client, "ana@teste.pt")
    client.post("/incidents/new", data={"category_id": "1"}, content_type="multipart/form-data")
    assert sent_emails == []


def test_pedido_a_api_da_brevo(monkeypatch):
    monkeypatch.setenv("BREVO_API_KEY", "chave")
    monkeypatch.setenv("MAIL_FROM", "townlyup.fundao@gmail.com")
    sent = {}

    class Resp(io.BytesIO):
        status = 201
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake(req, timeout=None, context=None):
        sent["url"], sent["headers"], sent["body"] = req.full_url, dict(req.header_items()), json.loads(req.data)
        return Resp(b"{}")
    monkeypatch.setattr(mailer.urllib.request, "urlopen", fake)
    assert mailer.deliver("a@b.pt", "Ana", "Assunto", "<p>x</p>", "x", "/tmp") is True
    assert sent["url"] == "https://api.brevo.com/v3/smtp/email" and sent["headers"]["Api-key"] == "chave"
    assert sent["body"]["sender"]["email"] == "townlyup.fundao@gmail.com" and sent["body"]["to"][0]["email"] == "a@b.pt"


def test_falha_de_envio_nao_rebenta(monkeypatch):
    monkeypatch.setenv("BREVO_API_KEY", "chave")
    def boom(*a, **k): raise mailer.urllib.error.URLError("sem rede")
    monkeypatch.setattr(mailer.urllib.request, "urlopen", boom)
    assert mailer.deliver("a@b.pt", "Ana", "Assunto", "<p>x</p>", "x", "/tmp") is False


def test_modo_desenvolvimento_grava_o_email(tmp_path, monkeypatch):
    monkeypatch.delenv("BREVO_API_KEY", raising=False)
    monkeypatch.delenv("MAIL_SERVER", raising=False)
    assert mailer.deliver("a@b.pt", "Ana", "Ocorrência TU-2026-0001", "<p>olá</p>", "olá", str(tmp_path))
    files = list(tmp_path.iterdir())
    assert len(files) == 1 and "<p>olá</p>" in files[0].read_text()
