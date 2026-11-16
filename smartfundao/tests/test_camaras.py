"""Uma Câmara Municipal por concelho: a conta principal (plataforma) cria a conta da Câmara (Distrito → Concelho),
o concelho fica ativo e as ocorrências de lá vão só para essa Câmara."""
from db import get_db
from test_flow import PASSWORD, app, client, create_incident, login  # noqa: F401
from test_territorio import fake_geoapi, register


def criar_camara(client, municipality="Sintra", district="Lisboa", email="cm@sintra.pt"):
    login(client, "rui@teste.pt")
    resp = client.post("/admin/camaras", data={"district": district, "municipality": municipality, "name": "Ana Sintra",
                                               "email": email, "password": "Sintra2026x"})
    client.post("/logout")
    return resp


def test_plataforma_cria_camara_e_ativa_o_concelho(client, app, monkeypatch):
    fake_geoapi(monkeypatch, [])
    page = (login(client, "rui@teste.pt"), client.get("/admin/contas").get_data(as_text=True))[1]
    assert "Nova Câmara Municipal" in page and 'id="c-district"' in page and "Câmaras na plataforma" in page
    client.post("/logout")

    # antes: Sintra ainda não está disponível
    register(client, "Sintra", "Lisboa", email="maria@sintra.pt", name="Maria Sintra")
    assert "Ainda não disponível no seu concelho" in client.get("/incidents/new").get_data(as_text=True)
    client.post("/logout")

    assert criar_camara(client).status_code == 302
    with app.app_context():
        org = get_db().execute("SELECT * FROM organizations WHERE kind = 'camara' AND municipality = 'Sintra'").fetchone()
        assert org["name"] == "Câmara Municipal de Sintra"
    assert client.get("/api/v1/territorio?concelho=Sintra").get_json()["active"] is True

    # depois: a munícipe de Sintra comunica e a ocorrência vai para a Câmara de Sintra
    login(client, "maria@sintra.pt")
    assert 'id="incident-form"' in client.get("/incidents/new").get_data(as_text=True)
    fora = client.post("/incidents/new", data=__import__("test_flow").incident_data(), content_type="multipart/form-data")
    assert fora.status_code == 400 and "fora do concelho de Sintra" in fora.get_data(as_text=True)
    create_incident(client, latitude="38.85", longitude="-9.35")
    with app.app_context():
        inc = get_db().execute("SELECT * FROM incidents ORDER BY id DESC LIMIT 1").fetchone()
        assert inc["municipality"] == "Sintra" and inc["organization_id"] == org["id"]
    client.post("/logout")

    # a Câmara de Sintra vê-a e pode tratá-la; a do Fundão não
    client.post("/login", data={"email": "cm@sintra.pt", "password": "Sintra2026x"})
    assert inc["ticket"] in client.get("/admin").get_data(as_text=True)
    assert client.get(f"/admin/incidents/{inc['id']}").status_code == 200
    client.post("/logout")
    login(client, "camara@teste.pt")
    assert inc["ticket"] not in client.get("/admin").get_data(as_text=True)
    assert client.get(f"/admin/incidents/{inc['id']}").status_code in (403, 404)


def test_desativar_a_camara_volta_a_por_o_concelho_inativo(client, app, monkeypatch):
    fake_geoapi(monkeypatch, [])
    criar_camara(client)
    with app.app_context():
        uid = get_db().execute("SELECT id FROM users WHERE email = 'cm@sintra.pt'").fetchone()["id"]
    login(client, "rui@teste.pt")
    client.post(f"/admin/contas/{uid}/estado", data={"active": "0"})
    assert client.get("/api/v1/territorio?concelho=Sintra").get_json()["active"] is False


def test_formulario_da_camara_valida_e_vem_do_pedido(client, app):
    login(client, "rui@teste.pt")
    resp = client.post("/admin/camaras", data={"district": "Lisboa", "municipality": "Porto", "name": "", "email": "x",
                                               "password": "curta"}, follow_redirects=True)
    html = resp.get_data(as_text=True)
    assert "Escolha o concelho." in html and "Indique um email válido." in html
    # pedido de acesso de uma Câmara → «Criar conta» preenche o formulário
    client.post("/logout")
    client.post("/register", data={"access": "entidade", "kind": "camara", "district": "Faro", "municipality": "Loulé",
                                   "entity_name": "Câmara Municipal de Loulé", "contact_name": "Rita Loulé",
                                   "email": "rita@cm-loule.pt", "phone": "912345678", "terms": "1"})
    login(client, "rui@teste.pt")
    with app.app_context():
        rid = get_db().execute("SELECT id FROM entity_requests ORDER BY id DESC LIMIT 1").fetchone()
    if rid:
        page = client.get(f"/admin/contas?pedido={rid['id']}").get_data(as_text=True)
        assert 'value="rita@cm-loule.pt"' in page and 'data-selected="Loulé"' in page
