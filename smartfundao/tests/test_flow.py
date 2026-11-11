"""Testes automáticos do Townly Up (testes de aceitação TA01–TA08 e segurança).

Executar:  python -m pytest -v
"""
import io
import os
import re
import sys

import pytest
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as database  # noqa: E402
import services as sv  # noqa: E402
from app import create_app  # noqa: E402
from db import get_db, init_db  # noqa: E402
from security import hash_password  # noqa: E402

PASSWORD = "Teste1234"


# ------------------------------------------------------------------ Fixtures
@pytest.fixture
def app(tmp_path, database_config):
    app = create_app({
        "TESTING": True,
        "CSRF_ENABLED": False,
        "UPLOAD_FOLDER": str(tmp_path / "uploads"),
        **database_config,
    })
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    with app.app_context():
        init_db()
        db = get_db()
        sv.create_user(db, "Ana", "ana@teste.pt", hash_password(PASSWORD))
        sv.create_user(db, "Carlos", "carlos@teste.pt", hash_password(PASSWORD))
        sv.create_user(db, "Rui", "rui@teste.pt", hash_password(PASSWORD), role="admin",
                       organization_id=sv.organization_by(db, "camara")["id"])
    yield app
    with app.app_context():
        get_db().close()


@pytest.fixture
def client(app):
    return app.test_client()


def login(client, email):
    return client.post("/login", data={"email": email, "password": PASSWORD})


def image_bytes(fmt="JPEG"):
    buf = io.BytesIO()
    Image.new("RGB", (60, 40), (120, 120, 120)).save(buf, format=fmt)
    buf.seek(0)
    return buf


def incident_data(**overrides):
    data = {
        "category_id": "1",
        "title": "Buraco na estrada",
        "description": "Buraco grande junto ao cruzamento.",
        "latitude": "40.1405",
        "longitude": "-7.5012",
        "privacy": "1",
        "photos": [(image_bytes(), "foto.jpg")],
    }
    data.update(overrides)
    return data


def create_incident(client, **overrides):
    """Cria uma ocorrência. Se o sistema a achar repetida, confirma «é outro problema» (como o munícipe faria)."""
    resp = client.post("/incidents/new", data=incident_data(**overrides), content_type="multipart/form-data")
    if resp.status_code == 400:
        ids = re.findall(r'data-duplicate-id="(\d+)"', resp.get_data(as_text=True))
        assert ids, resp.get_data(as_text=True)[:500]
        resp = client.post("/incidents/new", data=incident_data(not_duplicate=ids, **overrides),
                           content_type="multipart/form-data")
    assert resp.status_code == 302
    return int(re.search(r"/incidents/(\d+)/enviada", resp.headers["Location"]).group(1))


def status_of(app, incident_id):
    with app.app_context():
        return get_db().execute("SELECT status FROM incidents WHERE id = ?", (incident_id,)).fetchone()[0]


def update(client, incident_id, status, note="Observação de teste."):
    return client.post(f"/admin/incidents/{incident_id}/status",
                       data={"new_status": status, "note": note, "priority": "Média"})


# ------------------------------------------------------------------ Autenticação
def test_login_certo_e_errado(client):
    resp = client.post("/login", data={"email": "ana@teste.pt", "password": "errada"})
    assert "Email ou palavra-passe incorretos." in resp.get_data(as_text=True)
    assert login(client, "ana@teste.pt").headers["Location"] == "/citizen"
    client.post("/logout")
    assert login(client, "rui@teste.pt").headers["Location"] == "/admin"


def test_registo_cria_sempre_municipe(client, app):
    resp = client.post("/register", data={
        "name": "Hacker", "email": "novo@teste.pt", "password": "Segura123",
        "confirm": "Segura123", "terms": "1", "role": "admin"})
    assert resp.status_code == 302
    with app.app_context():
        user = get_db().execute("SELECT role, password_hash FROM users WHERE email = 'novo@teste.pt'").fetchone()
    assert user["role"] == "citizen"              # o campo 'role' é ignorado
    assert user["password_hash"] != "Segura123"   # password nunca em texto simples


# ------------------------------------------------------------------ TA01
def test_ta01_criar_ocorrencia_completa(client, app):
    login(client, "ana@teste.pt")
    incident_id = create_incident(client)
    with app.app_context():
        row = get_db().execute("SELECT * FROM incidents WHERE id = ?", (incident_id,)).fetchone()
        history = sv.get_history(get_db(), incident_id)
    assert re.fullmatch(r"TU-\d{4}-\d{4}", row["ticket"])
    assert row["status"] == "Registada"
    assert len(history) == 1 and history[0]["new_status"] == "Registada"
    page = client.get(f"/incidents/{incident_id}/enviada").get_data(as_text=True)
    assert row["ticket"] in page


def test_tickets_unicos(client, app):
    login(client, "ana@teste.pt")
    ids = [create_incident(client) for _ in range(3)]
    with app.app_context():
        tickets = {get_db().execute("SELECT ticket FROM incidents WHERE id = ?", (i,)).fetchone()[0] for i in ids}
    assert len(tickets) == 3


# ------------------------------------------------------------------ TA02
def test_ta02_sem_categoria_nao_guarda(client, app):
    login(client, "ana@teste.pt")
    resp = client.post("/incidents/new", data=incident_data(category_id=""), content_type="multipart/form-data")
    assert resp.status_code == 400
    assert "Escolha uma categoria." in resp.get_data(as_text=True)
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == 0


def test_sem_fotografia_nao_guarda(client, app):
    login(client, "ana@teste.pt")
    resp = client.post("/incidents/new", data=incident_data(photos=[]), content_type="multipart/form-data")
    assert resp.status_code == 400
    assert "Anexe pelo menos uma fotografia" in resp.get_data(as_text=True)


def test_localizacao_fora_do_fundao(client):
    login(client, "ana@teste.pt")
    resp = client.post("/incidents/new", data=incident_data(latitude="38.72", longitude="-9.14"),
                       content_type="multipart/form-data")
    assert resp.status_code == 400
    assert "concelho do Fundão" in resp.get_data(as_text=True)


# ------------------------------------------------------------------ TA03
def test_ta03_municipe_nao_entra_no_admin(client):
    login(client, "ana@teste.pt")
    assert client.get("/admin").status_code == 403


def test_anonimo_e_redirecionado_para_login(client):
    resp = client.get("/admin")
    assert resp.status_code == 302 and "/login" in resp.headers["Location"]


# ------------------------------------------------------------------ TA04 / TA05
def test_ta04_ta05_fluxo_completo_e_historico(client, app):
    login(client, "ana@teste.pt")
    incident_id = create_incident(client)
    client.post("/logout")

    login(client, "rui@teste.pt")
    assert update(client, incident_id, "Em análise").status_code == 302
    assert status_of(app, incident_id) == "Em análise"
    with app.app_context():
        last = sv.get_history(get_db(), incident_id)[-1]
    assert last["previous_status"] == "Registada" and last["new_status"] == "Em análise"
    assert last["actor_name"] == "Rui" and last["changed_at"]

    update(client, incident_id, "Em tratamento", "Equipa deslocada ao local.")
    resp = client.post(f"/admin/incidents/{incident_id}/status",
                       data={"new_status": "Resolvida", "note": "Pavimento reparado.",
                             "resolution_photo": (image_bytes("PNG"), "fim.png")},
                       content_type="multipart/form-data")
    assert resp.status_code == 302
    assert status_of(app, incident_id) == "Resolvida"
    client.post("/logout")

    # TA05: o munícipe já não a vê nas ativas, mas vê-a no histórico.
    login(client, "ana@teste.pt")
    with app.app_context():
        ticket = sv.get_incident(get_db(), incident_id)["ticket"]
    item = f'<div class="tu-item-code">{ticket}</div>'
    assert item not in client.get("/citizen?view=ativas").get_data(as_text=True)
    assert item in client.get("/citizen?view=arquivo").get_data(as_text=True)
    detail = client.get(f"/incidents/{incident_id}").get_data(as_text=True)
    assert "Pavimento reparado." in detail and "Equipa deslocada ao local." in detail
    with app.app_context():
        assert len(sv.get_history(get_db(), incident_id)) == 4
        assert get_db().execute("SELECT COUNT(*) FROM notifications WHERE kind = 'status'").fetchone()[0] == 3


def test_transicao_nao_permitida(client, app):
    login(client, "ana@teste.pt")
    incident_id = create_incident(client)
    client.post("/logout")
    login(client, "rui@teste.pt")
    resp = update(client, incident_id, "Resolvida")  # Registada → Resolvida não é permitido
    assert resp.status_code == 400
    assert status_of(app, incident_id) == "Registada"


def test_nota_obrigatoria(client, app):
    login(client, "ana@teste.pt")
    incident_id = create_incident(client)
    client.post("/logout")
    login(client, "rui@teste.pt")
    assert update(client, incident_id, "Em análise", note="").status_code == 400
    assert status_of(app, incident_id) == "Registada"


def test_duplicada_exige_ticket_de_referencia(client, app):
    login(client, "ana@teste.pt")
    first, second = create_incident(client), create_incident(client)
    client.post("/logout")
    login(client, "rui@teste.pt")
    assert update(client, second, "Duplicada", "É repetida.").status_code == 400
    with app.app_context():
        ref = sv.get_incident(get_db(), first)["ticket"]
    assert update(client, second, "Duplicada", f"Igual a {ref}.").status_code == 302
    assert status_of(app, second) == "Duplicada"


def test_municipe_nao_altera_estado(client, app):
    login(client, "ana@teste.pt")
    incident_id = create_incident(client)
    assert update(client, incident_id, "Em análise").status_code == 403
    assert status_of(app, incident_id) == "Registada"


# ------------------------------------------------------------------ TA06
def test_ta06_ticket_de_outro_cidadao(client, app):
    login(client, "ana@teste.pt")
    incident_id = create_incident(client)
    with app.app_context():
        image_id = sv.get_images(get_db(), incident_id)[0]["id"]
    client.post("/logout")
    login(client, "carlos@teste.pt")
    assert client.get(f"/incidents/{incident_id}").status_code == 403
    assert client.get(f"/media/{image_id}").status_code == 403
    assert "Buraco na estrada" not in client.get("/citizen").get_data(as_text=True)


# ------------------------------------------------------------------ TA07
def test_ta07_ficheiro_invalido(client, app):
    login(client, "ana@teste.pt")
    fake = (io.BytesIO(b"#!/bin/sh\nrm -rf /\n"), "foto.jpg")  # executável disfarçado
    resp = client.post("/incidents/new", data=incident_data(photos=[fake]), content_type="multipart/form-data")
    assert resp.status_code == 400
    assert "não é uma imagem válida" in resp.get_data(as_text=True)
    assert os.listdir(app.config["UPLOAD_FOLDER"]) == []


def test_formato_nao_permitido(client):
    login(client, "ana@teste.pt")
    resp = client.post("/incidents/new", data=incident_data(photos=[(image_bytes("GIF"), "a.gif")]),
                       content_type="multipart/form-data")
    assert resp.status_code == 400


def test_maximo_tres_fotografias(client):
    login(client, "ana@teste.pt")
    photos = [(image_bytes(), f"{i}.jpg") for i in range(4)]
    resp = client.post("/incidents/new", data=incident_data(photos=photos), content_type="multipart/form-data")
    assert resp.status_code == 400


def test_exif_removido(client, app):
    login(client, "ana@teste.pt")
    exif = Image.Exif()
    exif[0x010F] = "Telemovel X"  # Make
    buf = io.BytesIO()
    Image.new("RGB", (50, 50)).save(buf, format="JPEG", exif=exif)
    buf.seek(0)
    create_incident_resp = client.post("/incidents/new", data=incident_data(photos=[(buf, "x.jpg")]),
                                       content_type="multipart/form-data")
    assert create_incident_resp.status_code == 302
    # A fotografia fica guardada na base de dados (não no disco) e sem EXIF.
    assert os.listdir(app.config["UPLOAD_FOLDER"]) == []
    image_id = client.get("/citizen").get_data(as_text=True).split("/media/")[1].split('"')[0]
    resp = client.get(f"/media/{image_id}")
    assert resp.status_code == 200 and resp.headers["Content-Type"] == "image/jpeg"
    with Image.open(io.BytesIO(resp.data)) as img:
        assert not img.getexif()


# ------------------------------------------------------------------ TA08
def test_ta08_interface_responsiva(client):
    html = client.get("/").get_data(as_text=True)
    assert 'name="viewport"' in html and "width=device-width" in html


# ------------------------------------------------------------------ Segurança extra
def test_historico_imutavel(client, app):
    login(client, "ana@teste.pt")
    incident_id = create_incident(client)
    with app.app_context():
        db = get_db()
        with pytest.raises(database.IntegrityError):
            db.execute("UPDATE status_history SET note = 'alterado'")
        with pytest.raises(database.IntegrityError):
            db.execute("DELETE FROM incidents WHERE id = ?", (incident_id,))


def test_csrf_bloqueia_pedidos_sem_token(tmp_path):
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "c.db"),
                      "UPLOAD_FOLDER": str(tmp_path / "u")})
    with app.app_context():
        init_db()
    resp = app.test_client().post("/login", data={"email": "x@x.pt", "password": "y"})
    assert resp.status_code == 400


def test_sql_injection_na_pesquisa(client):
    login(client, "rui@teste.pt")
    resp = client.get("/admin", query_string={"q": "' OR 1=1; DROP TABLE users; --"})
    assert resp.status_code == 200
    assert login(client, "rui@teste.pt").status_code == 302  # tabela users continua lá


def test_paginas_de_erro_amigaveis(client):
    resp = client.get("/nao-existe")
    assert resp.status_code == 404 and "Página não encontrada" in resp.get_data(as_text=True)
