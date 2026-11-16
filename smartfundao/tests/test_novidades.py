"""Subcategorias, morada, «Antes e depois» e avaliação da resolução."""
import io

from PIL import Image

import services as sv
from db import get_db
from test_flow import PASSWORD, app, client, create_incident, login  # noqa: F401
from test_entities import add_staff, SEDE


def sub_id(app, category_id, name):
    with app.app_context():
        return get_db().execute("SELECT id FROM subcategories WHERE category_id = ? AND name = ?", (category_id, name)).fetchone()["id"]


def last_incident(app):
    with app.app_context():
        return get_db().execute("SELECT * FROM incidents ORDER BY id DESC LIMIT 1").fetchone()


def resolve(client, app, incident_id, photo=True):
    data = {"new_status": "Em análise", "note": "A analisar.", "priority": "Média"}
    client.post(f"/admin/incidents/{incident_id}/status", data=data)
    client.post(f"/admin/incidents/{incident_id}/status", data={**data, "new_status": "Em tratamento", "note": "Equipa no local."})
    buf = io.BytesIO(); Image.new("RGB", (80, 60), (20, 160, 60)).save(buf, "JPEG"); buf.seek(0)
    payload = {**data, "new_status": "Resolvida", "note": "Buraco tapado."}
    if photo:
        payload["resolution_photo"] = (buf, "depois.jpg")
    return client.post(f"/admin/incidents/{incident_id}/status", data=payload, content_type="multipart/form-data")


def test_subcategoria_e_morada(client, app):
    login(client, "ana@teste.pt")
    sid = sub_id(app, 1, "Buraco no pavimento")
    assert "Buraco no pavimento" in client.get("/incidents/new").get_data(as_text=True)
    create_incident(client, subcategory_id=str(sid), title="", address="  Rua da Liberdade 12,   6230-398 Fundão ")
    i = last_incident(app)
    assert i["subcategory_id"] == sid and i["title"] == "Buraco no pavimento"
    assert i["address"] == "Rua da Liberdade 12, 6230-398 Fundão"
    html = client.get(f"/incidents/{i['id']}").get_data(as_text=True)
    assert "Vias e pavimentos › Buraco no pavimento" in html and "Rua da Liberdade 12" in html


def test_subcategoria_de_outra_categoria_e_recusada(client, app):
    login(client, "ana@teste.pt")
    other = sub_id(app, 2, "Candeeiro apagado")
    resp = client.post("/incidents/new", data={"category_id": "1", "subcategory_id": str(other), "description": "Descrição suficiente.",
                                              "latitude": "40.1405", "longitude": "-7.5012", "privacy": "1"},
                       content_type="multipart/form-data")
    assert resp.status_code == 400 and "Escolha um tipo de problema desta categoria" in resp.get_data(as_text=True)


def test_antes_e_depois_e_avaliacao(client, app):
    add_staff(app, "obras@teste.pt", "servico", "Obras Municipais")
    login(client, "ana@teste.pt")
    create_incident(client)
    iid = last_incident(app)["id"]
    client.post("/logout")
    assert client.get(f"/media/publica/{iid}/depois.jpg").status_code == 404           # ainda não resolvida

    login(client, "obras@teste.pt")
    assert resolve(client, app, iid).status_code == 302
    client.post("/logout")
    page = client.get("/resolvidas").get_data(as_text=True)
    assert f"/media/publica/{iid}/depois.jpg" in page
    assert client.get(f"/media/publica/{iid}/depois.jpg").status_code == 200

    login(client, "ana@teste.pt")
    assert "Ficou bem resolvido?" in client.get(f"/incidents/{iid}").get_data(as_text=True)
    client.post(f"/incidents/{iid}/avaliar", data={"stars": "5", "comment": "Rápido!"})
    html = client.get(f"/incidents/{iid}").get_data(as_text=True)
    assert "A sua avaliação" in html and "Rápido!" in html
    assert "Já avaliou" in client.post(f"/incidents/{iid}/avaliar", data={"stars": "4"}, follow_redirects=True).get_data(as_text=True)
    with app.app_context():
        assert sv.satisfaction(get_db())["avg"] == 5.0


def test_problema_continua_reabre(client, app):
    add_staff(app, "obras@teste.pt", "servico", "Obras Municipais")
    login(client, "ana@teste.pt")
    create_incident(client)
    iid = last_incident(app)["id"]
    client.post("/logout")
    login(client, "obras@teste.pt"); resolve(client, app, iid, photo=False); client.post("/logout")
    login(client, "ana@teste.pt")
    resp = client.post(f"/incidents/{iid}/avaliar", data={"stars": "1", "reopen": "1"}, follow_redirects=True)
    assert "Diga-nos o que continua" in resp.get_data(as_text=True)
    client.post(f"/incidents/{iid}/avaliar", data={"stars": "1", "reopen": "1", "comment": "O buraco voltou a abrir."})
    i = last_incident(app)
    assert i["status"] == "Em tratamento" and i["resolved_at"] is None
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM notifications WHERE kind = 'assigned' AND message LIKE '%reaberta%'").fetchone()[0] == 1
    client.post("/logout")
    login(client, "carlos@teste.pt")
    assert client.post(f"/incidents/{iid}/avaliar", data={"stars": "5"}).status_code in (403, 302)
