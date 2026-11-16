"""Apagar as contas da Câmara (conta principal incluída) e criar uma nova conta principal."""
from db import get_db
from test_flow import PASSWORD, app, client, create_incident, login  # noqa: F401


def test_apagar_contas_da_camara_e_criar_outra(client, app):
    login(client, "ana@teste.pt")
    create_incident(client)                                   # fica no histórico
    client.post("/logout")
    with app.app_context():
        before = get_db().execute("SELECT COUNT(*) FROM users WHERE role IN ('admin','staff')").fetchone()[0]
        emails = [r[0] for r in get_db().execute("SELECT email FROM users WHERE role IN ('admin','staff')")]
    assert before >= 1
    runner = app.test_cli_runner()
    res = runner.invoke(args=["apagar-contas-camara"], input="nao\n")
    assert "Cancelado" in res.output
    res = runner.invoke(args=["apagar-contas-camara"], input="APAGAR\n")
    assert f"Apagadas {before} conta(s)" in res.output, res.output
    with app.app_context():
        db = get_db()
        assert db.execute("SELECT COUNT(*) FROM users WHERE role IN ('admin','staff')").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM users WHERE email IN (%s)" % ",".join("?" * len(emails)), emails).fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == 1
    assert client.post("/login", data={"email": emails[0], "password": PASSWORD, "access": "entidade"}).status_code == 200  # já não entra
    # nova conta principal (pode até reutilizar o mesmo email)
    res = runner.invoke(args=["repor-admin", "--email", emails[0], "--name", "Câmara Municipal do Fundão"], input="NovaConta2026\nNovaConta2026\n")
    assert "criada" in res.output, res.output
    resp = client.post("/login", data={"email": emails[0], "password": "NovaConta2026", "access": "entidade"})
    assert resp.status_code == 302
