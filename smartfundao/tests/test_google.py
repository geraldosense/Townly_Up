"""«Continuar com o Google»: sem configuração avisa; com configuração liga/cria a conta de munícipe em segurança."""
import time

import google_auth
from db import get_db
from test_flow import app, client, login  # noqa: F401


def setup(monkeypatch, claims_extra=None, sub="g-123", email="nova@gmail.com"):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "segredo-de-teste")
    def fake_exchange(code, redirect_uri, ssl_context=None):
        claims = {"iss": "https://accounts.google.com", "aud": "cid.apps.googleusercontent.com", "exp": time.time() + 300,
                  "sub": sub, "email": email, "email_verified": True, "name": "Nova Pessoa", "nonce": fake_exchange.nonce}
        claims.update(claims_extra or {})
        return claims
    monkeypatch.setattr(google_auth, "exchange_code", fake_exchange)
    return fake_exchange


def go(client, fake, state_ok=True):
    resp = client.get("/entrar/google")
    assert resp.status_code == 302 and resp.headers["Location"].startswith(google_auth.AUTH_URL)
    with client.session_transaction() as s:
        pending = s["google_oauth"]
    fake.nonce = pending["nonce"]
    return client.get(f"/entrar/google/retorno?code=abc&state={pending['state'] if state_ok else 'outro'}")


def test_sem_configuracao_volta_ao_login(client, monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    assert "Continuar com o Google" in client.get("/login").get_data(as_text=True)
    resp = client.get("/entrar/google", follow_redirects=True)
    assert "ainda não está configurado" in resp.get_data(as_text=True)


def test_cria_conta_de_municipe_e_pede_o_concelho(client, app, monkeypatch):
    fake = setup(monkeypatch)
    resp = go(client, fake)
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/definicoes#concelho")
    with app.app_context():
        u = get_db().execute("SELECT * FROM users WHERE email = 'nova@gmail.com'").fetchone()
        assert u["role"] == "citizen" and not u["has_password"]
        assert get_db().execute("SELECT COUNT(*) FROM user_identities WHERE user_id = ?", (u["id"],)).fetchone()[0] == 1
    client.post("/logout")
    assert go(client, fake).headers["Location"].endswith("/citizen")          # 2.ª vez: entra direto


def test_liga_a_conta_existente_com_o_mesmo_email(client, app, monkeypatch):
    fake = setup(monkeypatch, email="ana@teste.pt", sub="g-ana")
    go(client, fake)
    with app.app_context():
        n = get_db().execute("SELECT COUNT(*) FROM users WHERE email = 'ana@teste.pt'").fetchone()[0]
    assert n == 1 and client.get("/citizen").status_code == 200


def test_recusa_estado_errado_email_nao_verificado_e_contas_de_entidade(client, app, monkeypatch):
    fake = setup(monkeypatch)
    assert "expirou" in client.get(go(client, fake, state_ok=False).headers["Location"]).get_data(as_text=True)
    assert client.get("/citizen").status_code in (302, 401, 403)                 # não entrou
    fake = setup(monkeypatch, {"email_verified": False})
    html = client.get(go(client, fake).headers["Location"]).get_data(as_text=True)
    assert "email confirmado" in html
    fake = setup(monkeypatch, {"aud": "outra-app"})
    assert client.get(go(client, fake).headers["Location"]).status_code == 200
    assert client.get("/citizen").status_code in (302, 401, 403)
    fake = setup(monkeypatch, email="camara@teste.pt", sub="g-camara")
    html = client.get(go(client, fake).headers["Location"]).get_data(as_text=True)
    assert "conta de entidade" in html
