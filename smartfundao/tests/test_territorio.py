"""Portugal inteiro (distrito → concelho) e segurança (palavrões/imagens: aviso → banimento)."""
import profanity
import territory
from db import get_db
from test_flow import PASSWORD, app, client, create_incident, incident_data, login  # noqa: F401


def register(client, municipality, district, email="novo@teste.pt", name="Marta Silva"):
    return client.post("/register", data={"name": name, "email": email, "password": PASSWORD, "confirm": PASSWORD,
                                          "terms": "1", "district": district, "municipality": municipality})


def user(app, email):
    with app.app_context():
        return get_db().execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()


# ------------------------------------------------------------------ Território
def test_dados_de_portugal():
    assert len(territory.DISTRICTS) == 20                                   # 18 distritos + 2 regiões autónomas
    assert sum(len(v) for v in territory.DISTRICTS.values()) == 308
    assert "Fundão" in territory.DISTRICTS["Castelo Branco"]
    assert "Ponta Delgada" in territory.DISTRICTS["Região Autónoma dos Açores"]
    assert "Funchal" in territory.DISTRICTS["Região Autónoma da Madeira"]
    assert territory.valid("Castelo Branco", "Fundão") and not territory.valid("Lisboa", "Fundão")
    assert territory.is_active("Fundão") and not territory.is_active("Lisboa")


def test_registo_pede_distrito_e_concelho(client, app):
    html = client.get("/register").get_data(as_text=True)
    assert 'name="district"' in html and 'name="municipality"' in html and "Região Autónoma da Madeira" in html
    bad = register(client, "Fundão", "Lisboa")
    assert bad.status_code == 200 and "Escolha o seu concelho." in bad.get_data(as_text=True)
    assert "Escolha o seu distrito." in register(client, "", "").get_data(as_text=True)
    assert register(client, "Fundão", "Castelo Branco").status_code == 302
    u = user(app, "novo@teste.pt")
    assert (u["district"], u["municipality"]) == ("Castelo Branco", "Fundão")


def test_concelho_sem_adesao_nao_comunica(client, app):
    assert register(client, "Sintra", "Lisboa").status_code == 302
    page = client.get("/incidents/new").get_data(as_text=True)
    assert "Ainda não disponível no seu concelho" in page and 'id="incident-form"' not in page
    resp = client.post("/incidents/new", data=incident_data(), content_type="multipart/form-data")
    assert resp.status_code == 400 and "Ainda não disponível" in resp.get_data(as_text=True)
    mapa = client.get("/mapa").get_data(as_text=True)
    assert "Ainda não disponível no seu concelho" in mapa and "Câmara de Sintra" in mapa
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == 0


def test_mudar_de_concelho_nas_definicoes(client, app):
    register(client, "Sintra", "Lisboa")
    assert "O meu concelho" in client.get("/definicoes").get_data(as_text=True)
    client.post("/conta/concelho", data={"district": "Castelo Branco", "municipality": "Fundão"})
    assert user(app, "novo@teste.pt")["municipality"] == "Fundão"
    assert 'id="incident-form"' in client.get("/incidents/new").get_data(as_text=True)
    create_incident(client)
    with app.app_context():
        assert get_db().execute("SELECT municipality FROM incidents").fetchone()[0] == "Fundão"


def test_mapa_limitado_ao_concelho(client):
    html = client.get("/mapa").get_data(as_text=True)
    assert 'data-area="Fundão"' in html and "data-territory=" in html and 'data-active="1"' in html
    for label in ("Recebida", "Em execução", "Concluída", "Reportar"):
        assert label in html


# ------------------------------------------------------------------ Palavrões
def test_filtro_apanha_fintas_e_evita_falsos_positivos():
    for bad in ("Que m3rd@ de rua", "f.o.d.a-se", "CARALHOOO", "vocês são uns idiotas", "p u t a"):
        assert profanity.find(bad), bad
    for ok in ("Buraco enorme na Rua de Condeixa", "Caixas de tomates abandonadas", "A vaca está na estrada",
               "Fila na bicha do multibanco", "Estrada da Pica", "Computador velho no passeio", "Sinal estúpido? não"):
        assert profanity.find(ok) is None, ok


def test_palavrao_bloqueia_avisa_e_depois_bane(client, app):
    login(client, "ana@teste.pt")
    resp = client.post("/incidents/new", data=incident_data(description="Esta merda de buraco nunca mais é tapado."),
                       content_type="multipart/form-data")
    html = resp.get_data(as_text=True)
    assert resp.status_code == 400 and "linguagem ofensiva" in html and "aviso formal" in html
    with app.app_context():
        db = get_db()
        assert db.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == 0
        w = db.execute("SELECT * FROM user_warnings").fetchone()
        assert w["kind"] == "linguagem" and not w["led_to_ban"]
    # o aviso fica visível até ser confirmado
    assert "Aviso formal" in client.get("/citizen").get_data(as_text=True)
    client.post("/conta/avisos/visto", data={"next": "/citizen"})
    assert "Aviso formal ·" not in client.get("/citizen").get_data(as_text=True)
    assert "Avisos da comunidade" in client.get("/conta/atividade").get_data(as_text=True)
    # texto limpo continua a funcionar
    create_incident(client, description="Buraco grande junto à paragem do autocarro.")
    # 2.º ato: banida
    resp = client.post("/incidents/new", data=incident_data(title="Isto é uma porra"), content_type="multipart/form-data")
    assert resp.status_code == 302 and "/login" in resp.headers["Location"]
    u = user(app, "ana@teste.pt")
    assert not u["active"] and u["banned_at"] and "Linguagem" in u["ban_reason"]
    # a Câmara vê e pode levantar o banimento
    client.post("/logout")
    login(client, "rui@teste.pt")
    sup = client.get("/admin/supervisao").get_data(as_text=True)
    assert "Contas banidas" in sup and "ana@teste.pt" in sup
    client.post(f"/admin/contas/{u['id']}/levantar-banimento")
    u = user(app, "ana@teste.pt")
    assert u["active"] and u["banned_at"] is None
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM user_warnings").fetchone()[0] == 0


def test_nome_ofensivo_no_registo(client, app):
    resp = register(client, "Fundão", "Castelo Branco", name="Filho da puta")
    assert resp.status_code == 200 and "linguagem ofensiva" in resp.get_data(as_text=True)
    assert user(app, "novo@teste.pt") is None


# ------------------------------------------------------------------ Mapa nacional (contornos e freguesias)
def fake_geoapi(monkeypatch, calls):
    """Simula a GEO API PT (sem internet nos testes)."""
    import io as _io, json as _json
    sq = lambda x, y: {"type": "Polygon", "coordinates": [[[x, y], [x + .1, y], [x + .1, y + .1], [x, y + .1], [x, y]]]}
    body = {"geojsons": {"municipio": {"type": "Feature", "properties": {}, "geometry": sq(-9.4, 38.8)},
                         "freguesias": [{"type": "Feature", "properties": {"Dicofre": "111111", "Freguesia": "Algueirão"}, "geometry": sq(-9.4, 38.8)},
                                        {"type": "Feature", "properties": {"Dicofre": "111112", "Freguesia": "Colares"}, "geometry": sq(-9.35, 38.85)}]}}

    class Resp(_io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def urlopen(req, timeout=0, context=None):
        calls.append(req.full_url)
        return Resp(_json.dumps(body).encode())
    monkeypatch.setattr(territory._urlrequest, "urlopen", urlopen)
    territory._geo_mem.clear()


def test_territorio_do_fundao_sem_internet(client):
    data = client.get("/api/v1/territorio?concelho=Fundão").get_json()
    assert data["active"] and len(data["parishes"]["features"]) == 23 and len(data["bbox"]) == 4
    leve = client.get("/api/v1/territorio?concelho=Fundão&leve=1").get_json()
    assert len(leve["parishes"]) == 23 and {"code", "name"} <= set(leve["parishes"][0])
    assert client.get("/api/v1/territorio?concelho=Atlantida").status_code == 404


def test_territorio_de_outro_concelho_fica_guardado(client, app, monkeypatch):
    calls = []
    fake_geoapi(monkeypatch, calls)
    data = client.get("/api/v1/territorio?concelho=Sintra").get_json()
    assert not data["active"] and [f["properties"]["freguesia"] for f in data["parishes"]["features"]] == ["Algueirão", "Colares"]
    territory._geo_mem.clear()                                   # 2.º pedido vem da cópia em instance/geo, sem internet
    assert client.get("/api/v1/territorio?concelho=Sintra&leve=1").get_json()["parishes"][0]["code"] == "111111"
    assert len(calls) == 1 and "Sintra" in calls[0]


def test_territorio_sem_internet_responde_503(client, monkeypatch):
    def fail(*a, **k): raise OSError("sem rede")
    monkeypatch.setattr(territory._urlrequest, "urlopen", fail)
    territory._geo_mem.clear()
    assert client.get("/api/v1/territorio?concelho=Mafra").status_code == 503


def test_mapa_abre_na_freguesia_da_pessoa(client, app, monkeypatch):
    fake_geoapi(monkeypatch, [])
    register(client, "Sintra", "Lisboa")
    client.post("/conta/concelho", data={"district": "Lisboa", "municipality": "Sintra", "parish": "111112"})
    u = user(app, "novo@teste.pt")
    assert u["home_parish"] == "111112" and u["parish_code"] is None
    html = client.get("/mapa").get_data(as_text=True)
    assert 'data-my-parish="111112"' in html and 'data-area="Sintra"' in html and 'data-active="0"' in html
    bad = client.post("/conta/concelho", data={"district": "Lisboa", "municipality": "Sintra", "parish": "999999"}, follow_redirects=True)
    assert "Escolha uma freguesia da lista." in bad.get_data(as_text=True)
    client.post("/conta/concelho", data={"district": "Castelo Branco", "municipality": "Fundão", "parish": "050413"})
    u = user(app, "novo@teste.pt")
    assert u["home_parish"] == "050413" and u["parish_code"] == "050413"       # no Fundão também recebe os alertas da Junta
