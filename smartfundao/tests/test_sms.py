"""Responsáveis por freguesia, SMS de novas ocorrências e dados da conta principal."""
import sms
import services as sv
from db import get_db
from test_flow import PASSWORD, app, client, create_incident, login  # noqa: F401

SEDE = "050434"          # o ponto dos testes (40.1405, -7.5012) fica na UF de Fundão
ALPEDRINHA = "050406"


def new_manager(client, parish=SEDE, email="fundao@townly.pt", phone="912 345 678"):
    return client.post("/admin/freguesias", data={"parish_code": parish, "name": "Junta do Fundão",
                                                  "email": email, "phone": phone, "password": "Temp12345"})


# ------------------------------------------------------------------ números e texto
def test_numeros_de_telemovel():
    assert sms.normalize_phone("912 345 678") == "+351912345678"
    assert sms.normalize_phone("+351 965-000-111") == "+351965000111"
    assert sms.normalize_phone("00351932222333") == "+351932222333"
    assert sms.normalize_phone("275 123 456") is None          # fixo: não recebe SMS
    assert sms.normalize_phone("91234") is None
    assert sms.pretty_phone("+351912345678") == "+351 912 345 678"


def test_sms_cabe_numa_mensagem_e_sem_acentos():
    inc = {"ticket": "TU-2026-0042", "category": "Iluminação pública",
           "title": "Candeeiro apagado " + "muito comprido " * 20}
    text = sms.new_incident_text(inc, "UF Fundão", "https://townlyup.pt/admin/incidents/42")
    assert len(text) <= 160 and text.isascii()
    assert text.startswith("Townly Up: nova ocorrencia TU-2026-0042 em UF Fundao.")
    assert text.endswith("Ver: https://townlyup.pt/admin/incidents/42") and "..." in text


# ------------------------------------------------------------------ página da conta principal
def test_so_a_conta_principal_gere_os_responsaveis(client):
    login(client, "ana@teste.pt")
    assert client.get("/admin/freguesias").status_code == 403


def test_criar_responsavel_e_receber_sms(client, app, sent_sms):
    login(client, "rui@teste.pt")
    page = client.get("/admin/freguesias").get_data(as_text=True)
    assert "0<span> / 23" in page and "Sem responsável" in page
    assert new_manager(client).status_code == 302
    page = client.get("/admin/freguesias").get_data(as_text=True)
    assert "1<span> / 23" in page and "+351 912 345 678" in page and "SMS ativas" in page
    with app.app_context():
        u = get_db().execute("SELECT role, phone FROM users WHERE email = 'fundao@townly.pt'").fetchone()
    assert u["role"] == "staff" and u["phone"] == "+351912345678"
    client.post("/logout")

    login(client, "ana@teste.pt")
    create_incident(client)
    assert len(sent_sms) == 1
    numbers, text, _folder = sent_sms[0]
    assert numbers == ["+351912345678"] and "em UF Fundao" in text and "TU-" in text and len(text) <= 160
    client.post("/logout")

    client.post("/login", data={"email": "fundao@townly.pt", "password": "Temp12345"})
    assert "TU-" in client.get("/admin").get_data(as_text=True)


def test_outras_freguesias_e_sms_desligadas_nao_recebem(client, app, sent_sms):
    login(client, "rui@teste.pt")
    new_manager(client, parish=ALPEDRINHA, email="alpedrinha@townly.pt")
    new_manager(client, email="fundao2@townly.pt", phone="961111222")
    with app.app_context():
        uid = get_db().execute("SELECT id FROM users WHERE email = 'fundao2@townly.pt'").fetchone()["id"]
    client.post(f"/admin/freguesias/{uid}/telemovel", data={"phone": "961111222"})   # sem «sms_alerts» → desliga
    client.post("/logout")
    login(client, "ana@teste.pt")
    create_incident(client)
    assert sent_sms == []                                     # Alpedrinha não; o do Fundão desligou


def test_erros_ao_criar_responsavel(client):
    login(client, "rui@teste.pt")
    resp = new_manager(client, phone="123", parish="000000")
    page = resp.get_data(as_text=True)
    assert resp.status_code == 400 and "telemóvel português válido" in page and "Escolha a freguesia" in page


# ------------------------------------------------------------------ dados da conta
def test_conta_principal_muda_nome_e_email(client, app):
    login(client, "rui@teste.pt")
    resp = client.post("/conta/dados", data={"name": "Townly Up", "email": "admin@townly.pt", "current_for_email": "errada"})
    assert resp.status_code == 400 and "palavra-passe atual" in resp.get_data(as_text=True)
    resp = client.post("/conta/dados", data={"name": "Townly Up", "email": "admin@townly.pt", "current_for_email": PASSWORD})
    assert resp.status_code == 302
    client.post("/logout")
    assert client.post("/login", data={"email": "admin@townly.pt", "password": PASSWORD}).status_code == 302
    assert "Townly Up" in client.get("/conta").get_data(as_text=True)


def test_email_repetido_e_responsavel_muda_telemovel(client, app):
    login(client, "rui@teste.pt")
    resp = client.post("/conta/dados", data={"name": "Rui", "email": "ana@teste.pt", "current_for_email": PASSWORD})
    assert "Já existe uma conta" in resp.get_data(as_text=True)
    new_manager(client)
    client.post("/logout")
    client.post("/login", data={"email": "fundao@townly.pt", "password": "Temp12345"})
    assert client.post("/conta/dados", data={"name": "Junta do Fundão", "email": "fundao@townly.pt",
                                             "phone": "+351 935 555 444", "sms_alerts": "1"}).status_code == 302
    with app.app_context():
        assert get_db().execute("SELECT phone FROM users WHERE email = 'fundao@townly.pt'").fetchone()["phone"] == "+351935555444"


def test_repor_conta_principal_pela_linha_de_comandos(app):
    runner = app.test_cli_runner()
    out = runner.invoke(args=["repor-admin"], input="admin@townly.pt\nTownly Up\nNova2026ab\nNova2026ab\n").output
    assert "rui@teste.pt → admin@townly.pt" in out
    client = app.test_client()
    assert client.post("/login", data={"email": "admin@townly.pt", "password": "Nova2026ab"}).status_code == 302
    bad = runner.invoke(args=["repor-admin"], input="ana@teste.pt\nX\nNova2026ab\nNova2026ab\n")
    assert bad.exit_code != 0 and "Já existe outra conta" in bad.output
