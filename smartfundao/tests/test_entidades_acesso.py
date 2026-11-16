"""Entrar: separadores Cidadão / Câmara·Junta e conversão de conta de cidadão em conta de entidade."""
import services as sv
from db import get_db
from test_flow import PASSWORD, app, client, login  # noqa: F401


def post_login(client, email, access):
    return client.post("/login", data={"email": email, "password": PASSWORD, "access": access})


def test_separadores_no_entrar(client):
    html = client.get("/login").get_data(as_text=True)
    assert 'aria-label="Tipo de conta"' in html and ">Câmara</a>" in html and 'name="access" value="cidadao"' in html
    ent = client.get("/login?acesso=entidade").get_data(as_text=True)
    assert "Entrar no painel municipal" in ent and 'name="access" value="entidade"' in ent and "Entrar como convidado" not in ent


def test_cada_conta_entra_pelo_seu_separador(client):
    bad = post_login(client, "ana@teste.pt", "entidade").get_data(as_text=True)
    assert "conta de cidadão" in bad
    bad = post_login(client, "rui@teste.pt", "cidadao").get_data(as_text=True)
    assert "conta de entidade" in bad
    assert post_login(client, "rui@teste.pt", "entidade").headers["Location"] == "/admin"
    client.post("/logout")
    assert post_login(client, "ana@teste.pt", "cidadao").status_code == 302


def test_converter_conta_de_cidadao(client, app):
    with app.app_context():
        camara = sv.organization_by(get_db(), "camara")["id"]
    login(client, "rui@teste.pt")
    client.post("/admin/contas/converter", data={"email": "carlos@teste.pt", "organization_id": camara})
    with app.app_context():
        u = get_db().execute("SELECT role, organization_id FROM users WHERE email = 'carlos@teste.pt'").fetchone()
    assert (u["role"], u["organization_id"]) == ("staff", camara)
    page = client.post("/admin/contas/converter", data={"email": "carlos@teste.pt", "organization_id": camara}, follow_redirects=True)
    assert "já é de uma entidade" in page.get_data(as_text=True)
    client.post("/logout")
    assert post_login(client, "carlos@teste.pt", "entidade").headers["Location"] == "/admin"
    assert client.get("/incidents/new").status_code in (302, 403)          # já não comunica como cidadão


def test_entidade_pede_acesso_em_vez_de_criar_conta(client, app):
    page = client.get("/register?acesso=entidade").get_data(as_text=True)
    assert "Pedir acesso para a sua entidade" in page and 'name="password"' not in page
    bad = client.post("/register", data={"access": "entidade", "kind": "junta"}).get_data(as_text=True)
    assert "Indique o nome da entidade" in bad
    resp = client.post("/register", data={"access": "entidade", "kind": "junta", "district": "Castelo Branco", "municipality": "Fundão",
                                          "entity_name": "Junta de Freguesia de Alpedrinha", "contact_name": "Maria Costa",
                                          "email": "geral@jf-alpedrinha.pt", "phone": "275 123 456", "terms": "1"})
    assert resp.status_code == 302 and "acesso=entidade" in resp.headers["Location"]
    with app.app_context():
        db = get_db()
        assert db.execute("SELECT COUNT(*) FROM users WHERE email = 'geral@jf-alpedrinha.pt'").fetchone()[0] == 0   # não cria conta
        req = db.execute("SELECT * FROM entity_requests").fetchone()
    assert req["status"] == "pendente" and req["phone"] == "275123456"
    login(client, "rui@teste.pt")
    contas = client.get("/admin/contas").get_data(as_text=True)
    assert "Junta de Freguesia de Alpedrinha" in contas and "geral@jf-alpedrinha.pt" in contas
    client.post(f"/admin/pedidos/{req['id']}/tratado")
    assert "geral@jf-alpedrinha.pt" not in client.get("/admin/contas").get_data(as_text=True)
