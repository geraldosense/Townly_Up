"""O autor apaga a sua ocorrência; alertas de cada novo problema para a comunidade."""
import services as sv
from db import get_db
from test_flow import app, client, create_incident, login, update  # noqa: F401


def count(app, sql, *params):
    with app.app_context():
        return get_db().execute(sql, params).fetchone()[0]


# ------------------------------------------------------------------ Apagar
def test_autor_apaga_a_sua_ocorrencia(client, app):
    login(client, "ana@teste.pt")
    incident_id = create_incident(client)
    assert "Apagar esta ocorrência" in client.get(f"/incidents/{incident_id}").get_data(as_text=True)
    resp = client.post(f"/incidents/{incident_id}/apagar")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/citizen")
    assert client.get(f"/incidents/{incident_id}").status_code == 404
    assert client.get("/api/v1/map").get_json()["incidents"] == []
    assert count(app, "SELECT COUNT(*) FROM incident_images WHERE incident_id = ?", incident_id) == 0
    assert count(app, "SELECT COALESCE(SUM(points), 0) FROM points_log WHERE incident_id = ?", incident_id) == 0
    assert count(app, "SELECT COUNT(*) FROM notifications WHERE incident_id = ?", incident_id) == 0
    # o registo e o histórico continuam na base de dados (RF10/RF11)
    assert count(app, "SELECT COUNT(*) FROM incidents WHERE id = ? AND deleted_at IS NOT NULL", incident_id) == 1
    assert count(app, "SELECT COUNT(*) FROM status_history WHERE incident_id = ? AND note LIKE '%apagada pelo autor%'", incident_id) == 1


def test_entidade_deixa_de_ver_a_apagada(client, app):
    login(client, "ana@teste.pt")
    incident_id = create_incident(client)
    client.post(f"/incidents/{incident_id}/apagar")
    client.post("/logout")
    login(client, "rui@teste.pt")
    assert client.get(f"/admin/incidents/{incident_id}").status_code == 404
    with app.app_context():
        assert sv.status_counts(get_db())["Registada"] == 0


def test_outro_municipe_nao_pode_apagar(client):
    login(client, "ana@teste.pt")
    incident_id = create_incident(client)
    client.post("/logout")
    login(client, "carlos@teste.pt")
    assert client.post(f"/incidents/{incident_id}/apagar").status_code == 403


def test_nao_apaga_depois_de_comecar_o_tratamento(client, app):
    login(client, "ana@teste.pt")
    incident_id = create_incident(client)
    client.post("/logout")
    login(client, "rui@teste.pt")
    update(client, incident_id, "Em análise", "A analisar.")
    update(client, incident_id, "Em tratamento", "Equipa a caminho.")
    client.post("/logout")
    login(client, "ana@teste.pt")
    page = client.get(f"/incidents/{incident_id}").get_data(as_text=True)
    assert "não pode ser apagada" in page and "Sim, apagar" not in page
    client.post(f"/incidents/{incident_id}/apagar")
    assert client.get(f"/incidents/{incident_id}").status_code == 200


# ------------------------------------------------------------------ Alertas da comunidade
def test_todos_os_municipes_recebem_alerta(client, app):
    login(client, "ana@teste.pt")
    create_incident(client)
    assert count(app, """SELECT COUNT(*) FROM notifications n JOIN users u ON u.id = n.user_id
                          WHERE u.email = 'carlos@teste.pt' AND n.kind = 'zone'""") == 1
    assert count(app, """SELECT COUNT(*) FROM notifications n JOIN users u ON u.id = n.user_id
                          WHERE u.email = 'ana@teste.pt' AND n.kind = 'zone'""") == 0   # o autor não


def test_mesma_freguesia_recebe_alerta_destacado(client, app):
    with app.app_context():
        db = get_db()
        with db:
            db.execute("UPDATE users SET parish_code = '050434' WHERE email = 'carlos@teste.pt'")
    login(client, "ana@teste.pt")
    create_incident(client)  # fica na sede (050434)
    with app.app_context():
        msg = get_db().execute("""SELECT n.message FROM notifications n JOIN users u ON u.id = n.user_id
                                   WHERE u.email = 'carlos@teste.pt'""").fetchone()[0]
    assert msg.startswith("Novo problema na sua freguesia")


def test_so_freguesias_que_sigo(client, app):
    login(client, "carlos@teste.pt")
    client.post("/alertas/alcance", data={"scope": "followed"})
    client.post("/logout")
    login(client, "ana@teste.pt")
    create_incident(client)
    assert count(app, """SELECT COUNT(*) FROM notifications n JOIN users u ON u.id = n.user_id
                          WHERE u.email = 'carlos@teste.pt'""") == 0
