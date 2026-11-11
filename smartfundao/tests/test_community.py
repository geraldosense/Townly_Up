"""Testes das funcionalidades estilo Waze: mapa vivo, «Eu também vi», duplicados,
pontos/níveis/ranking, API JSON e PWA.

Executar:  python -m pytest -v
"""
import re

import community as cm
import services as sv
from db import get_db
from test_flow import app, client, create_incident, image_bytes, incident_data, login, update  # noqa: F401


def points_of(app, email):
    with app.app_context():
        db = get_db()
        uid = db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()[0]
        return cm.user_points(db, uid)


def confirm(client, incident_id, kind="seen"):
    return client.post(f"/api/v1/incidents/{incident_id}/confirm", json={"kind": kind})


# ------------------------------------------------------------------ «Eu também vi»
def test_confirmar_da_pontos_a_quem_confirma_e_ao_autor(client, app):
    login(client, "ana@teste.pt")
    incident_id = create_incident(client)
    client.post("/logout")
    login(client, "carlos@teste.pt")
    resp = confirm(client, incident_id)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["points"] == cm.POINTS["confirm_seen"] and data["counts"]["seen"] == 1
    assert points_of(app, "carlos@teste.pt") == cm.POINTS["confirm_seen"]
    assert points_of(app, "ana@teste.pt") == cm.POINTS["report"] + cm.POINTS["confirmed_by"]


def test_nao_confirma_duas_vezes(client):
    login(client, "ana@teste.pt")
    incident_id = create_incident(client)
    client.post("/logout")
    login(client, "carlos@teste.pt")
    assert confirm(client, incident_id).status_code == 200
    resp = confirm(client, incident_id)
    assert resp.status_code == 400 and "Já tinha confirmado" in resp.get_json()["error"]


def test_nao_confirma_a_propria_ocorrencia(client):
    login(client, "ana@teste.pt")
    incident_id = create_incident(client)
    resp = confirm(client, incident_id)
    assert resp.status_code == 400 and "própria" in resp.get_json()["error"]


def test_confirmar_exige_sessao_de_municipe(client):
    login(client, "ana@teste.pt")
    incident_id = create_incident(client)
    client.post("/logout")
    assert confirm(client, incident_id).status_code == 401          # anónimo
    login(client, "rui@teste.pt")
    assert confirm(client, incident_id).status_code == 403          # administrador


def test_nao_confirma_ocorrencia_encerrada(client):
    login(client, "ana@teste.pt")
    incident_id = create_incident(client)
    client.post("/logout")
    login(client, "rui@teste.pt")
    update(client, incident_id, "Rejeitada", "Fora do âmbito municipal.")
    client.post("/logout")
    login(client, "carlos@teste.pt")
    assert confirm(client, incident_id).status_code == 400


def test_confirmacao_por_formulario_sem_javascript(client, app):
    login(client, "ana@teste.pt")
    incident_id = create_incident(client)
    client.post("/logout")
    login(client, "carlos@teste.pt")
    resp = client.post(f"/incidents/{incident_id}/confirm", data={"kind": "fixed", "next": "/mapa"})
    assert resp.status_code == 302 and resp.headers["Location"] == "/mapa"
    with app.app_context():
        assert cm.confirmation_counts(get_db(), incident_id)["fixed"] == 1


def test_api_confirmar_exige_csrf_no_cabecalho(tmp_path):
    from app import create_app
    from db import init_db
    from security import hash_password
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "c.db"), "UPLOAD_FOLDER": str(tmp_path / "u")})
    with app.app_context():
        init_db()
        sv.create_user(get_db(), "Ana", "ana@teste.pt", hash_password("Teste1234"))
    c = app.test_client()
    page = c.get("/login").get_data(as_text=True)
    token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
    c.post("/login", data={"email": "ana@teste.pt", "password": "Teste1234", "csrf_token": token})
    # O login renova a sessão (proteção contra "session fixation"), por isso o token muda.
    token = re.search(r'name="csrf-token" content="([^"]+)"', c.get("/citizen").get_data(as_text=True)).group(1)
    assert c.post("/api/v1/incidents/1/confirm", json={"kind": "seen"}).status_code == 400  # sem token
    resp = c.post("/api/v1/incidents/1/confirm", json={"kind": "seen"}, headers={"X-CSRF-Token": token})
    assert resp.status_code == 404  # passou o CSRF; a ocorrência é que não existe


# ------------------------------------------------------------------ Mapa vivo
def test_mapa_publico_sem_dados_pessoais(client):
    login(client, "ana@teste.pt")
    create_incident(client)
    client.post("/logout")
    resp = client.get("/api/v1/map")
    assert resp.status_code == 200
    item = resp.get_json()["incidents"][0]
    raw = resp.get_data(as_text=True)
    for private in ("Ana", "ana@teste.pt", "Buraco na estrada", "Buraco grande", "/media/"):
        assert private not in raw
    assert {"ticket", "category", "status", "lat", "lng", "seen", "icon"} <= item.keys()
    assert client.get("/mapa").status_code == 200


def test_mapa_publico_esconde_rejeitadas(client):
    login(client, "ana@teste.pt")
    incident_id = create_incident(client)
    client.post("/logout")
    login(client, "rui@teste.pt")
    update(client, incident_id, "Rejeitada", "Não é da responsabilidade municipal.")
    assert client.get("/api/v1/map").get_json()["incidents"] == []


def test_mapa_indica_as_minhas_e_as_confirmadas(client):
    login(client, "ana@teste.pt")
    mine = create_incident(client)
    client.post("/logout")
    login(client, "carlos@teste.pt")
    confirm(client, mine)
    item = client.get("/api/v1/map").get_json()["incidents"][0]
    assert item["mine"] is False and item["confirmed"] == ["seen"]
    client.post("/logout")
    login(client, "ana@teste.pt")
    assert client.get("/api/v1/map").get_json()["incidents"][0]["mine"] is True


# ------------------------------------------------------------------ Duplicados (RF14)
def test_ocorrencias_proximas(client):
    login(client, "ana@teste.pt")
    create_incident(client)  # em 40.1405, -7.5012
    near = client.get("/api/v1/incidents/nearby?lat=40.1407&lng=-7.5013&category_id=1").get_json()
    assert len(near["incidents"]) == 1 and near["incidents"][0]["distance_m"] < 50
    far = client.get("/api/v1/incidents/nearby?lat=40.1450&lng=-7.5012").get_json()
    assert far["incidents"] == []
    other_cat = client.get("/api/v1/incidents/nearby?lat=40.1407&lng=-7.5013&category_id=2").get_json()
    assert other_cat["incidents"] == []
    assert client.get("/api/v1/incidents/nearby").status_code == 400


def test_distancia_haversine():
    # 0,001° de latitude ≈ 111 m
    assert 105 < cm.distance_m(40.0, -7.5, 40.001, -7.5) < 116


def test_admin_ve_possiveis_duplicados(client):
    login(client, "ana@teste.pt")
    first = create_incident(client)
    create_incident(client)
    client.post("/logout")
    login(client, "rui@teste.pt")
    html = client.get(f"/admin/incidents/{first}").get_data(as_text=True)
    assert "Possíveis duplicados" in html


# ------------------------------------------------------------------ Comunicar em 3 passos
def test_titulo_opcional_usa_categoria(client, app):
    login(client, "ana@teste.pt")
    resp = client.post("/incidents/new", data=incident_data(title=""), content_type="multipart/form-data")
    assert resp.status_code == 302
    with app.app_context():
        assert get_db().execute("SELECT title FROM incidents").fetchone()[0] == "Vias e pavimentos"


# ------------------------------------------------------------------ Pontos, níveis e ranking
def test_pontos_ao_comunicar_e_ao_resolver(client, app):
    login(client, "ana@teste.pt")
    incident_id = create_incident(client)
    assert points_of(app, "ana@teste.pt") == cm.POINTS["report"]
    client.post("/logout")
    login(client, "rui@teste.pt")
    update(client, incident_id, "Em análise")
    update(client, incident_id, "Em tratamento")
    update(client, incident_id, "Resolvida", "Reparado.")
    assert points_of(app, "ana@teste.pt") == cm.POINTS["report"] + cm.POINTS["resolved"]
    assert points_of(app, "rui@teste.pt") == 0


def test_niveis():
    assert cm.level_for(0)["name"] == "Olho novo"
    lvl = cm.level_for(42)
    assert lvl["name"] == "Olho atento" and lvl["missing"] == 58 and 0 < lvl["progress"] < 100
    assert cm.level_for(10_000)["next_name"] is None


def test_ranking_protege_nomes(client, app):
    with app.app_context():
        db = get_db()
        db.execute("UPDATE users SET name = 'Ana Maria Martins', parish_code = '050406' WHERE email = 'ana@teste.pt'")
        db.commit()
    login(client, "ana@teste.pt")
    create_incident(client)
    client.post("/logout")
    html = client.get("/ranking").get_data(as_text=True)
    assert "Ana M." in html and "Ana Maria Martins" not in html
    assert "Alpedrinha" in html


def test_registo_com_freguesia(client, app):
    resp = client.post("/register", data={"name": "Rita", "email": "rita@teste.pt", "password": "Segura123",
                                          "confirm": "Segura123", "terms": "1", "parish": "050413"})
    assert resp.status_code == 302
    client.post("/logout")
    bad = client.post("/register", data={"name": "Zé", "email": "ze@teste.pt", "password": "Segura123",
                                         "confirm": "Segura123", "terms": "1", "parish": "Lisboa"})
    assert bad.status_code == 200 and "Escolha uma freguesia da lista." in bad.get_data(as_text=True)
    with app.app_context():
        assert get_db().execute("SELECT parish_code FROM users WHERE email = 'rita@teste.pt'").fetchone()[0] == "050413"


def test_api_me(client):
    assert client.get("/api/v1/me").status_code == 401
    login(client, "ana@teste.pt")
    create_incident(client)
    data = client.get("/api/v1/me").get_json()
    assert data["points"] == cm.POINTS["report"] and data["level"]["name"] == "Olho novo"


# ------------------------------------------------------------------ API e PWA
def test_api_categorias_e_erros_em_json(client):
    cats = client.get("/api/v1/categories").get_json()
    assert len(cats) == 8 and all("icon" in c and "color" in c for c in cats)
    resp = client.get("/api/v1/nao-existe")
    assert resp.status_code == 404 and resp.is_json


def test_pwa(client):
    manifest = client.get("/manifest.webmanifest")
    assert manifest.status_code == 200 and manifest.get_json()["short_name"] == "Townly Up"
    sw = client.get("/sw.js")
    assert sw.status_code == 200 and "javascript" in sw.headers["Content-Type"]
    assert sw.headers.get("Service-Worker-Allowed") == "/"
    assert client.get("/offline").status_code == 200
    assert 'rel="manifest"' in client.get("/").get_data(as_text=True)


# ------------------------------------------------------------------ Câmara
def test_formulario_tem_camara_e_galeria(client):
    login(client, "ana@teste.pt")
    html = client.get("/incidents/new").get_data(as_text=True)
    assert 'id="open-camera"' in html and 'id="camera-video"' in html     # câmara na aplicação
    assert 'id="photos-gallery"' in html                                    # galeria
    assert 'capture="environment"' in html                                  # alternativa: câmara nativa
    assert 'name="photos"' in html and "photos.js" in html                  # campo enviado ao servidor


def test_foto_da_camera_jpeg_e_aceite(client, app):
    """O JavaScript converte o fotograma da câmara em JPEG; o servidor valida-o como qualquer foto."""
    login(client, "ana@teste.pt")
    resp = client.post("/incidents/new", data=incident_data(photos=[(image_bytes("JPEG"), "foto-1731270000000.jpg")]),
                       content_type="multipart/form-data")
    assert resp.status_code == 302


# ------------------------------------------------------------------ Freguesias (CAOP / DGT)
def _fake_geojson(n=23):
    feats = [{"type": "Feature", "properties": {"dicofre": f"0504{i:02d}", "freguesia": f"Freguesia {i}", "concelho": "Fundão"},
              "geometry": {"type": "Polygon", "coordinates": [[[-7.5, 40.1], [-7.49, 40.1], [-7.49, 40.11], [-7.5, 40.1]]]}}
             for i in range(n)]
    return {"type": "FeatureCollection", "features": feats}


def test_comando_freguesias_guarda_geojson(app, monkeypatch, tmp_path):
    import io
    import json
    import urllib.request
    app.static_folder = str(tmp_path)
    body = json.dumps(_fake_geojson()).encode()
    monkeypatch.setattr(urllib.request, "urlopen", lambda url, **kw: io.BytesIO(body))
    result = app.test_cli_runner().invoke(args=["freguesias"])
    assert result.exit_code == 0, result.output
    saved = json.loads((tmp_path / "data" / "freguesias_fundao.geojson").read_text(encoding="utf-8"))
    assert len(saved["features"]) == 23


def test_comando_freguesias_recusa_resposta_errada(app, monkeypatch, tmp_path):
    import io
    import json
    import urllib.request
    app.static_folder = str(tmp_path)
    body = json.dumps(_fake_geojson(5)).encode()
    monkeypatch.setattr(urllib.request, "urlopen", lambda url, **kw: io.BytesIO(body))
    result = app.test_cli_runner().invoke(args=["freguesias"])
    assert result.exit_code != 0 and "esperadas 23" in result.output
    assert not (tmp_path / "data" / "freguesias_fundao.geojson").exists()


def test_paginas_incluem_mapa_das_freguesias(client):
    home = client.get("/").get_data(as_text=True)
    assert 'id="territory"' in home and "parishes.js" in home
    live = client.get("/mapa").get_data(as_text=True)
    assert 'id="toggle-parishes"' in live and 'id="parish-filter"' in live and "parishes.js" in live


# ------------------------------------------------------------------ GPS (Google Maps)
def test_detalhe_tem_gps_google_maps(client):
    login(client, "ana@teste.pt")
    incident_id = create_incident(client)  # 40.1405, -7.5012
    html = client.get(f"/incidents/{incident_id}").get_data(as_text=True)
    assert "https://www.google.com/maps/dir/?api=1&amp;destination=40.1405,-7.5012&amp;travelmode=driving" in html
    assert "https://www.google.com/maps/search/?api=1&amp;query=40.1405,-7.5012" in html
