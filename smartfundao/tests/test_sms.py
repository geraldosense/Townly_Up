"""SMS de novas ocorrências para as contas das Juntas e dados da conta principal."""
import sms
import services as sv
from db import get_db
from test_flow import PASSWORD, app, client, create_incident, login  # noqa: F401

SEDE = "050434"          # o ponto dos testes (40.1405, -7.5012) fica na UF de Fundão
ALPEDRINHA = "050406"


def new_manager(client, parish=SEDE, email="fundao@townly.pt", phone="912 345 678", app=None):
    """Conta de uma Junta do Fundão com telemóvel (já não há página de «responsáveis por freguesia»)."""
    from security import hash_password
    with (app or client.application).app_context():
        db = get_db()
        org = sv.organization_by(db, "junta", parish)
        uid = sv.create_user(db, "Junta do Fundão", email, hash_password("Temp12345"), role="staff", organization_id=org["id"])
        with db:
            db.execute("UPDATE users SET phone = ?, sms_alerts = TRUE WHERE id = ?", (sms.normalize_phone(phone), uid))
    return uid


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


# ------------------------------------------------------------------ SMS para a Junta
def test_junta_recebe_sms(client, app, sent_sms):
    new_manager(client)
    login(client, "ana@teste.pt")
    create_incident(client)
    assert len(sent_sms) == 1
    numbers, text, _folder = sent_sms[0]
    assert numbers == ["+351912345678"] and "em UF Fundao" in text and "TU-" in text and len(text) <= 160
    client.post("/logout")
    client.post("/login", data={"email": "fundao@townly.pt", "password": "Temp12345"})
    assert "TU-" in client.get("/admin").get_data(as_text=True)


def test_ja_nao_ha_pagina_de_responsaveis(client):
    login(client, "rui@teste.pt")
    assert client.get("/admin/freguesias").status_code == 404


def test_outras_freguesias_e_sms_desligadas_nao_recebem(client, app, sent_sms):
    new_manager(client, parish=ALPEDRINHA, email="alpedrinha@townly.pt")
    uid = new_manager(client, email="fundao2@townly.pt", phone="961111222")
    with app.app_context():
        db = get_db()
        with db:
            db.execute("UPDATE users SET sms_alerts = FALSE WHERE id = ?", (uid,))      # desligou as SMS
    login(client, "ana@teste.pt")
    create_incident(client)
    assert sent_sms == []                                     # Alpedrinha não; o do Fundão desligou


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
    assert client.post("/definicoes/notificacoes", data={"phone": "+351 935 555 444", "sms_alerts": "1"}).status_code == 302
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


def test_apagar_tudo_deixa_so_os_dados_de_referencia(client, app):
    login(client, "ana@teste.pt")
    create_incident(client)
    client.post("/logout")
    runner = app.test_cli_runner()
    out = runner.invoke(args=["apagar-tudo"], input="nao\n").output
    assert "Cancelado" in out
    res = runner.invoke(args=["apagar-tudo"], input="APAGAR\n"); out = res.output
    assert res.exception is None, repr(res.exception)
    assert "Apagado: 4 conta(s) e 1 ocorrência(s)" in out
    with app.app_context():
        db = get_db()
        assert db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM organizations").fetchone()[0] > 20
    runner.invoke(args=["repor-admin"], input="admin@townly.pt\nTownly Up\nNova2026ab\nNova2026ab\n")
    runner.invoke(args=["create-admin"], input="x\nx@x.pt\nNova2026ab\nNova2026ab\n")
    with app.app_context():
        hp = __import__("security").hash_password
        # sem a conta da Câmara o concelho fica inativo: a plataforma cria-a de novo
        sv.create_camara_account(get_db(), {"district": "Castelo Branco", "municipality": "Fundão", "name": "Câmara",
                                            "email": "cm@fundao.pt", "password": "Nova2026ab"}, hp)
        sv.create_user(get_db(), "Ana", "ana@teste.pt", hp(PASSWORD))
    login(client, "ana@teste.pt")
    create_incident(client)
    with app.app_context():
        assert get_db().execute("SELECT ticket FROM incidents").fetchone()["ticket"].endswith("-0001")
