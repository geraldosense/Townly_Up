"""«Continuar com o Google» (o Google é simulado: os testes não saem para a internet)."""
import base64
import json
import time
import urllib.parse

import pytest

import oauth
from db import get_db
from test_flow import PASSWORD, app, client, login  # noqa: F401

CLIENT_ID = "cliente-teste.apps.googleusercontent.com"


def jwt(claims):
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
    return f"{enc({'alg': 'RS256'})}.{enc(claims)}.assinatura"


@pytest.fixture
def google(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "segredo")
    account = {"sub": "1234567890", "email": "Maria.Silva@gmail.com", "name": "Maria Silva", "email_verified": True}
    sent = {}

    def fake_http(url, data=None):
        if data is None:
            return {}  # documento de descoberta → usa os endereços por omissão
        sent.update(data)
        return {"id_token": jwt({"iss": "https://accounts.google.com", "aud": CLIENT_ID, "exp": time.time() + 300,
                                 "nonce": account.pop("_nonce"), **account})}
    monkeypatch.setattr(oauth, "_http_json", fake_http)
    oauth._discovery["doc"] = None
    return {"account": account, "sent": sent}


def go_google(client, google, state=None):
    resp = client.get("/auth/google")
    assert resp.status_code == 302
    query = urllib.parse.parse_qs(urllib.parse.urlparse(resp.headers["Location"]).query)
    assert query["scope"] == ["openid email profile"] and query["code_challenge_method"] == ["S256"]
    google["account"]["_nonce"] = query["nonce"][0]
    return client.get(f"/auth/google/callback?code=abc&state={state or query['state'][0]}")


def test_botao_aparece_no_entrar_e_no_registo(client, google):
    assert "Continuar com o Google" in client.get("/login").get_data(as_text=True)
    assert "Criar conta com o Google" in client.get("/register").get_data(as_text=True)


def test_primeira_vez_conclui_registo(client, app, google):
    resp = go_google(client, google)
    assert resp.headers["Location"].endswith("/auth/google/concluir")
    assert google["sent"]["code_verifier"] and google["sent"]["grant_type"] == "authorization_code"
    page = client.get("/auth/google/concluir").get_data(as_text=True)
    assert "Maria Silva" in page and "maria.silva@gmail.com" in page
    assert "aceitar as regras" in client.post("/auth/google/concluir", data={"parish": "050406"}).get_data(as_text=True)
    resp = client.post("/auth/google/concluir", data={"parish": "050406", "terms": "1"})
    assert resp.status_code == 302 and client.get("/citizen").status_code == 200
    with app.app_context():
        db = get_db()
        u = db.execute("SELECT id, role, has_password, parish_code FROM users WHERE email = 'maria.silva@gmail.com'").fetchone()
        assert u["role"] == "citizen" and not u["has_password"] and u["parish_code"] == "050406"
        assert db.execute("SELECT subject FROM user_identities WHERE user_id = ?", (u["id"],)).fetchone()[0] == "1234567890"


def test_segunda_vez_entra_logo(client, google):
    go_google(client, google)
    client.post("/auth/google/concluir", data={"terms": "1"})
    client.post("/logout")
    resp = go_google(client, google)
    assert resp.headers["Location"].endswith("/citizen")


def test_liga_a_conta_existente_do_municipe(client, app, google):
    google["account"]["email"] = "ana@teste.pt"
    resp = go_google(client, google)
    assert resp.headers["Location"].endswith("/citizen")
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM user_identities").fetchone()[0] == 1


def test_contas_das_entidades_nao_entram_com_google(client, google):
    google["account"]["email"] = "rui@teste.pt"  # administrador
    resp = go_google(client, google)
    assert resp.headers["Location"].endswith("/login")
    assert client.get("/admin").status_code in (302, 401, 403)


def test_state_errado_e_recusado(client, google):
    assert go_google(client, google, state="forjado").status_code == 400


def test_email_nao_verificado_e_recusado(client, google):
    google["account"]["email_verified"] = False
    assert go_google(client, google).headers["Location"].endswith("/login")


def test_conta_suspensa_nao_entra(client, app, google):
    google["account"]["email"] = "ana@teste.pt"
    with app.app_context():
        db = get_db()
        with db:
            db.execute("UPDATE users SET active = FALSE WHERE email = 'ana@teste.pt'")
    assert go_google(client, google).headers["Location"].endswith("/login")


def test_conta_google_pode_criar_palavra_passe(client, google):
    go_google(client, google)
    client.post("/auth/google/concluir", data={"terms": "1"})
    assert "Criar uma palavra-passe" in client.get("/conta").get_data(as_text=True)
    assert client.post("/conta", data={"password": "Nova12345", "confirm": "Nova12345"}).status_code == 302
    client.post("/logout")
    assert client.post("/login", data={"email": "maria.silva@gmail.com", "password": "Nova12345"}).status_code == 302


def test_sem_configuracao_avisa(client, monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    resp = client.get("/auth/google")
    assert resp.headers["Location"].endswith("/login")
