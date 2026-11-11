"""Contas: a Câmara cria as contas das entidades; todos mudam a sua palavra-passe;
a 1.ª conta da Câmara vem de variáveis de ambiente; o site começa vazio (só conteúdo dos utilizadores)."""
import services as sv
from db import get_db
from test_flow import PASSWORD, app, client, create_incident, login  # noqa: F401


def org_id(app, kind, value=None):
    with app.app_context():
        return sv.organization_by(get_db(), kind, value)["id"]


def test_so_a_camara_gere_contas(client):
    login(client, "ana@teste.pt")
    assert client.get("/admin/contas").status_code == 403


def test_camara_cria_tecnico_que_entra_no_painel_da_entidade(client, app):
    login(client, "rui@teste.pt")
    resp = client.post("/admin/contas", data={"name": "Técnica da Junta", "email": "Junta.X@Teste.pt",
                                              "organization_id": org_id(app, "junta", "050406"),
                                              "password": "Temp12345"})
    assert resp.status_code == 302
    with app.app_context():
        u = get_db().execute("SELECT role, organization_id FROM users WHERE email = 'junta.x@teste.pt'").fetchone()
    assert u["role"] == "staff"
    client.post("/logout")
    client.post("/login", data={"email": "junta.x@teste.pt", "password": "Temp12345"})
    assert client.get("/admin").status_code == 200
    assert client.get("/admin/contas").status_code == 403


def test_criacao_de_conta_valida_os_dados(client, app):
    login(client, "rui@teste.pt")
    html = client.post("/admin/contas", data={"name": "X", "email": "ana@teste.pt", "organization_id": "999",
                                              "password": "curta"}).get_data(as_text=True)
    assert "Já existe uma conta" in html and "Escolha a entidade" in html and "8+ caracteres" in html


def test_desativar_conta_impede_a_entrada(client, app):
    login(client, "rui@teste.pt")
    client.post("/admin/contas", data={"name": "Técnico", "email": "t@teste.pt", "password": "Temp12345",
                                       "organization_id": org_id(app, "servico", "Obras Municipais")})
    with app.app_context():
        uid = get_db().execute("SELECT id FROM users WHERE email = 't@teste.pt'").fetchone()[0]
        me = get_db().execute("SELECT id FROM users WHERE email = 'rui@teste.pt'").fetchone()[0]
    client.post(f"/admin/contas/{uid}/estado", data={"active": "0"})
    client.post(f"/admin/contas/{me}/estado", data={"active": "0"})  # não pode desativar-se a si próprio
    client.post("/logout")
    html = client.post("/login", data={"email": "t@teste.pt", "password": "Temp12345"}).get_data(as_text=True)
    assert "incorretos" in html
    assert login(client, "rui@teste.pt").status_code == 302


def test_mudar_palavra_passe(client):
    login(client, "ana@teste.pt")
    html = client.post("/conta", data={"current": "errada", "password": "Nova12345", "confirm": "Nova12345"}).get_data(as_text=True)
    assert "atual está errada" in html
    assert client.post("/conta", data={"current": PASSWORD, "password": "Nova12345", "confirm": "Nova12345"}).status_code == 302
    client.post("/logout")
    assert client.post("/login", data={"email": "ana@teste.pt", "password": "Nova12345"}).status_code == 302


def test_conta_da_camara_por_variaveis_de_ambiente(app, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", "Gestor@Cm-Exemplo.pt")
    monkeypatch.setenv("ADMIN_PASSWORD", "Segura2026")
    runner = app.test_cli_runner()
    assert "criada" in runner.invoke(args=["ensure-admin"]).output
    assert "já existe" in runner.invoke(args=["ensure-admin"]).output  # idempotente (cada arranque)
    with app.app_context():
        u = get_db().execute("SELECT role FROM users WHERE email = 'gestor@cm-exemplo.pt'").fetchone()
    assert u["role"] == "admin"
    monkeypatch.setenv("ADMIN_PASSWORD", "fraca")
    monkeypatch.setenv("ADMIN_EMAIL", "outro@cm-exemplo.pt")
    assert runner.invoke(args=["ensure-admin"]).exit_code != 0


def test_site_vazio_so_mostra_conteudo_dos_utilizadores(client):
    for url in ["/", "/mapa", "/ranking", "/login", "/register", "/freguesias/050406", "/api/v1/map"]:
        assert client.get(url).status_code == 200, url
    home = client.get("/").get_data(as_text=True)
    assert "TU-20" not in home  # nenhuma ocorrência inventada
    login(client, "ana@teste.pt")
    create_incident(client)
    client.post("/logout")
    assert "TU-20" in client.get("/").get_data(as_text=True)


def test_paginas_da_comunidade_e_rodape(client):
    for url, text in [("/freguesias", "As 23 freguesias"), ("/como-funciona", "Como funciona o Townly Up"),
                      ("/regras", "Regras da comunidade"), ("/privacidade", "Privacidade e dados")]:
        html = client.get(url).get_data(as_text=True)
        assert text in html, url
    assert "Alpedrinha" in client.get("/freguesias").get_data(as_text=True)
