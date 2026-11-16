"""Menu, Definições (notificações, privacidade da conta) e Ajuda."""
import json

from db import get_db
from test_flow import PASSWORD, app, client, create_incident, login  # noqa: F401


def test_definicoes_e_ajuda_abrem_para_todos(client):
    html = client.get("/definicoes").get_data(as_text=True)
    assert "Tema" in html and "Acessibilidade" not in html and "Entre para escolher os seus alertas" in html
    assert "Perguntas frequentes" in client.get("/ajuda").get_data(as_text=True)


def test_menu_lateral(client):
    login(client, "ana@teste.pt")
    html = client.get("/mapa").get_data(as_text=True)
    drawer = html[html.index('id="tu-drawer"'):html.index('</aside>', html.index('id="tu-drawer"'))]
    for label in ("Início", "Mapa interativo", "Minhas ocorrências", "Notificações", "Ranking",
                  "Configurações", "Como funciona", "Regras", "Ajuda e suporte", "Idioma", "Modo claro"):
        assert label in drawer
    for lang in ("Português", "Español", "English", "Français", "Deutsch", "中文"):
        assert lang in drawer
    assert 'is-active' in drawer.split("Mapa interativo")[0].rsplit("<a", 1)[1]           # página atual destacada
    assert "tu-sb-desktop" in drawer.split("Mapa interativo")[0].rsplit("<a", 1)[1]       # no telemóvel já está na barra de baixo
    assert "Estatísticas" not in drawer and "Comunidade" not in drawer                                                    # só para a conta principal


def test_desligar_comprovativo_por_email(client, app, sent_emails):
    login(client, "ana@teste.pt")
    assert client.post("/definicoes/notificacoes", data={}).status_code == 302          # interruptor desligado
    create_incident(client)
    assert sent_emails == []
    client.post("/definicoes/notificacoes", data={"email_receipts": "1"})
    create_incident(client, title="Outro problema aqui", latitude="40.15", longitude="-7.47")
    assert len(sent_emails) == 1


def test_descarregar_os_meus_dados(client):
    login(client, "ana@teste.pt")
    create_incident(client)
    resp = client.get("/definicoes/dados")
    assert resp.status_code == 200 and "attachment" in resp.headers["Content-Disposition"]
    data = json.loads(resp.get_data(as_text=True))
    assert data["perfil"]["email"] == "ana@teste.pt" and len(data["ocorrencias"]) == 1 and data["pontos"]


def test_apagar_a_minha_conta(client, app):
    login(client, "ana@teste.pt")
    create_incident(client)
    resp = client.post("/definicoes/apagar-conta", data={"password": "errada", "confirm": "APAGAR"})
    assert resp.status_code == 400 and "palavra-passe está errada" in resp.get_data(as_text=True)
    assert client.post("/definicoes/apagar-conta", data={"password": PASSWORD, "confirm": "apagar"}).status_code == 302
    with app.app_context():
        u = get_db().execute("SELECT name, email, active FROM users WHERE email LIKE 'eliminada-%'").fetchone()
        assert u["name"] == "Conta eliminada" and not u["active"]
        assert get_db().execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == 1    # fica no histórico, anónima
    assert client.post("/login", data={"email": "ana@teste.pt", "password": PASSWORD}).status_code == 200  # já não entra


def test_entidades_nao_apagam_a_propria_conta(client):
    login(client, "rui@teste.pt")
    resp = client.post("/definicoes/apagar-conta", data={"password": PASSWORD, "confirm": "APAGAR"})
    assert resp.status_code == 400


def test_fotografia_publica_so_da_ocorrencia_publica(client, app):
    login(client, "ana@teste.pt")
    incident_id = create_incident(client) or 1
    client.post("/logout")
    with app.app_context():
        incident_id = get_db().execute("SELECT id FROM incidents ORDER BY id DESC LIMIT 1").fetchone()["id"]
    resp = client.get(f"/media/publica/{incident_id}.jpg")
    assert resp.status_code == 200 and resp.headers["Content-Type"] == "image/jpeg"
    with app.app_context():
        db = get_db()
        with db:
            db.execute("UPDATE incidents SET deleted_at = '2026-01-01 00:00:00' WHERE id = ?", (incident_id,))
    assert client.get(f"/media/publica/{incident_id}.jpg").status_code == 404     # apagada: deixa de ser pública


def test_painel_configuracoes_do_menu(client, app):
    login(client, "ana@teste.pt")
    html = client.get("/mapa").get_data(as_text=True)
    drawer = html[html.index('id="tu-drawer"'):]
    assert 'href="/conta"' in drawer and "Ver o perfil" in drawer                  # o menu leva ao Perfil
    perfil = client.get("/conta").get_data(as_text=True)
    assert "Nome e email" in perfil and "Atividade da conta" in perfil and "Terminar sessão" in perfil
    resp = client.post("/conta/painel", data={"name": "Ana Silva", "email": "ana@teste.pt", "parish_code": "050406",
                                             "next": "/mapa"})
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/mapa")
    with app.app_context():
        u = get_db().execute("SELECT name, parish_code, email_receipts FROM users WHERE email = 'ana@teste.pt'").fetchone()
    assert u["name"] == "Ana Silva" and u["parish_code"] == "050406" and not u["email_receipts"]
    client.post("/conta/painel", data={"name": "Ana Silva", "email": "nova@teste.pt", "next": "/mapa"})   # sem palavra-passe
    with app.app_context():
        assert get_db().execute("SELECT 1 FROM users WHERE email = 'nova@teste.pt'").fetchone() is None
    create_incident(client)
    page = client.get("/conta/atividade").get_data(as_text=True)
    assert "Comunicou TU-" in page and "Pontos ganhos" in page
