"""Testes dos recetores: entidades (Câmara, Juntas, serviços), encaminhamento,
permissões por entidade e alertas por freguesia. Correm em SQLite e PostgreSQL."""
import pytest

import db as database
import services as sv
from db import get_db
from security import hash_password
from test_flow import PASSWORD, app, client, create_incident, image_bytes, incident_data, login, update  # noqa: F401

SEDE = "050434"        # ponto 40.1405, -7.5012 dos testes fica aqui
ALPEDRINHA = "050406"


def add_staff(app, email, kind, value=None):
    with app.app_context():
        db = get_db()
        org = sv.organization_by(db, kind, value)
        return sv.create_user(db, "Técnico", email, hash_password(PASSWORD), role="staff", organization_id=org["id"])


def incident_row(app, incident_id):
    with app.app_context():
        return sv.get_incident(get_db(), incident_id)


def notifications(app, email, kind=None):
    with app.app_context():
        sql = """SELECT n.* FROM notifications n JOIN users u ON u.id = n.user_id WHERE u.email = ?"""
        params = [email]
        if kind:
            sql += " AND n.kind = ?"
            params.append(kind)
        return get_db().execute(sql, params).fetchall()


# ------------------------------------------------------------------ Encaminhamento
def test_encaminha_para_o_servico_da_categoria(client, app):
    login(client, "ana@teste.pt")
    incident_id = create_incident(client)  # categoria 1 = Vias e pavimentos
    row = incident_row(app, incident_id)
    assert row["parish_code"] == SEDE
    assert row["organization"] == "Obras Municipais"


def test_sem_servico_vai_para_a_junta_da_freguesia(client, app):
    with app.app_context():
        cat = get_db().execute("SELECT id FROM categories WHERE name = 'Mobiliário urbano'").fetchone()[0]
    login(client, "ana@teste.pt")
    resp = client.post("/incidents/new", data=incident_data(category_id=str(cat)), content_type="multipart/form-data")
    assert resp.status_code == 302
    with app.app_context():
        row = get_db().execute("SELECT o.kind, o.parish_code FROM incidents i JOIN organizations o ON o.id = i.organization_id").fetchone()
    assert row["kind"] == "junta" and row["parish_code"] == SEDE


# ------------------------------------------------------------------ Permissões por entidade
def test_tecnico_so_trata_o_que_e_da_sua_entidade(client, app):
    add_staff(app, "obras@teste.pt", "servico", "Obras Municipais")
    add_staff(app, "luz@teste.pt", "servico", "Iluminação Pública")
    add_staff(app, "junta@teste.pt", "junta", SEDE)
    add_staff(app, "alpedrinha@teste.pt", "junta", ALPEDRINHA)
    login(client, "ana@teste.pt")
    incident_id = create_incident(client)
    client.post("/logout")

    login(client, "obras@teste.pt")                       # serviço responsável
    assert client.get(f"/admin/incidents/{incident_id}").status_code == 200
    assert update(client, incident_id, "Em análise").status_code == 302
    assert "TU-" in client.get("/admin").get_data(as_text=True)
    client.post("/logout")

    login(client, "junta@teste.pt")                       # Junta da freguesia do local
    assert client.get(f"/admin/incidents/{incident_id}").status_code == 200
    client.post("/logout")

    for other in ("luz@teste.pt", "alpedrinha@teste.pt"):  # outras entidades: não
        login(client, other)
        assert client.get(f"/admin/incidents/{incident_id}").status_code == 403
        assert client.get(f"/incidents/{incident_id}").status_code == 403
        assert update(client, incident_id, "Em tratamento").status_code == 403
        client.post("/logout")
    assert incident_row(app, incident_id)["status"] == "Em análise"


def test_tecnico_nao_comunica_ocorrencias(client, app):
    add_staff(app, "obras@teste.pt", "servico", "Obras Municipais")
    login(client, "obras@teste.pt")
    assert client.get("/incidents/new").status_code == 403


def test_so_a_central_reencaminha(client, app):
    add_staff(app, "obras@teste.pt", "servico", "Obras Municipais")
    add_staff(app, "verdes@teste.pt", "servico", "Espaços Verdes")
    login(client, "ana@teste.pt")
    incident_id = create_incident(client)
    client.post("/logout")
    with app.app_context():
        verdes = sv.organization_by(get_db(), "servico", "Espaços Verdes")["id"]

    login(client, "obras@teste.pt")
    resp = client.post(f"/admin/incidents/{incident_id}/status",
                       data={"new_status": "Registada", "note": "Não é nosso.", "organization_id": verdes})
    assert resp.status_code == 400
    client.post("/logout")

    login(client, "rui@teste.pt")
    resp = client.post(f"/admin/incidents/{incident_id}/status",
                       data={"new_status": "Em análise", "note": "Árvore, não pavimento.", "organization_id": verdes})
    assert resp.status_code == 302
    assert incident_row(app, incident_id)["organization"] == "Espaços Verdes"
    assert notifications(app, "verdes@teste.pt", "assigned")


# ------------------------------------------------------------------ Alertas (recetores = todos os utilizadores)
def test_quem_segue_a_freguesia_recebe_alerta(client, app):
    login(client, "carlos@teste.pt")
    client.post("/alertas/freguesias", data={"parish": [SEDE, ALPEDRINHA]})
    client.post("/logout")
    add_staff(app, "obras@teste.pt", "servico", "Obras Municipais")

    login(client, "ana@teste.pt")
    client.post("/alertas/freguesias", data={"parish": [SEDE]})
    create_incident(client)

    assert len(notifications(app, "carlos@teste.pt", "zone")) == 1      # segue a freguesia
    assert notifications(app, "ana@teste.pt", "zone") == []             # o autor não
    assert len(notifications(app, "obras@teste.pt", "assigned")) == 1   # a entidade responsável


def test_registo_segue_a_propria_freguesia(client, app):
    client.post("/register", data={"name": "Rita", "email": "rita@teste.pt", "password": "Segura123",
                                   "confirm": "Segura123", "terms": "1", "parish": ALPEDRINHA})
    with app.app_context():
        uid = get_db().execute("SELECT id FROM users WHERE email = 'rita@teste.pt'").fetchone()[0]
        assert sv.followed_parishes(get_db(), uid) == [ALPEDRINHA]


def test_quem_confirmou_acompanha_a_evolucao(client, app):
    login(client, "ana@teste.pt")
    incident_id = create_incident(client)
    client.post("/logout")
    login(client, "carlos@teste.pt")
    client.post(f"/api/v1/incidents/{incident_id}/confirm", json={"kind": "seen"})
    client.post("/logout")
    login(client, "rui@teste.pt")
    update(client, incident_id, "Em análise")
    assert any("passou a «Em análise»" in n["message"] for n in notifications(app, "carlos@teste.pt", "status"))


def test_pagina_de_alertas(client, app):
    login(client, "ana@teste.pt")
    html = client.get("/alertas").get_data(as_text=True)
    assert "Alpedrinha" in html and 'name="parish"' in html
    assert client.post("/alertas/lidas").status_code == 302


# ------------------------------------------------------------------ Freguesias
def test_pagina_da_freguesia(client, app):
    login(client, "ana@teste.pt")
    create_incident(client)
    client.post("/logout")
    html = client.get(f"/freguesias/{SEDE}").get_data(as_text=True)
    assert "Aldeia Nova do Cabo" in html and "TU-" in html
    assert client.get("/freguesias/999999").status_code == 404


def test_api_freguesias(client):
    data = client.get("/api/v1/parishes").get_json()
    assert len(data) == 23 and {"code", "short_name", "active", "image"} <= data[0].keys()


# ------------------------------------------------------------------ Regras da própria base de dados
def test_base_de_dados_recusa_dados_incoerentes(app):
    with app.app_context():
        db = get_db()
        with pytest.raises(database.IntegrityError):   # técnico sem entidade
            db.execute("INSERT INTO users (name, email, password_hash, role, created_at) "
                       "VALUES ('Zé', 'ze@teste.pt', 'x', 'staff', '2026-01-01 00:00:00')")
        with pytest.raises(database.IntegrityError):   # email com maiúsculas
            db.execute("INSERT INTO users (name, email, password_hash, created_at) "
                       "VALUES ('Zé', 'Ze@Teste.pt', 'x', '2026-01-01 00:00:00')")
        with pytest.raises(database.IntegrityError):   # estado que não existe
            db.execute("UPDATE incidents SET status = 'Esquecida'")
            db.execute("INSERT INTO categories (name) VALUES ('Vias e pavimentos')")
        with pytest.raises(database.IntegrityError):   # freguesia inexistente
            db.execute("INSERT INTO organizations (name, kind, parish_code) VALUES ('X', 'junta', '999999')")
