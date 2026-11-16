"""Fotografia de perfil: adicionar, trocar, remover; quadrada, 320×320 e sem metadados."""
import io

from PIL import Image

from db import get_db
from test_flow import app, client, login  # noqa: F401


def picture(size=(800, 500), fmt="JPEG", exif=False):
    buf = io.BytesIO()
    img = Image.new("RGB", size, (30, 90, 160))
    kwargs = {}
    if exif:
        e = Image.Exif(); e[0x010F] = "Telemovel X"; kwargs["exif"] = e
    img.save(buf, format=fmt, **kwargs)
    buf.seek(0)
    return buf


def upload(client, data):
    return client.post("/conta/fotografia", data={"avatar": (data, "eu.jpg")}, content_type="multipart/form-data")


def user_row(app, email="ana@teste.pt"):
    with app.app_context():
        return get_db().execute("SELECT id, avatar, avatar_updated_at FROM users WHERE email = ?", (email,)).fetchone()


def test_adicionar_fotografia(client, app):
    login(client, "ana@teste.pt")
    assert "Adicionar fotografia" in client.get("/conta").get_data(as_text=True)
    assert upload(client, picture(exif=True)).status_code == 302
    row = user_row(app)
    with Image.open(io.BytesIO(bytes(row["avatar"]))) as img:
        assert img.size == (320, 320) and not img.getexif()
    resp = client.get(f"/avatar/{row['id']}")
    assert resp.status_code == 200 and resp.headers["Content-Type"] == "image/jpeg"
    page = client.get("/conta").get_data(as_text=True)
    assert "Alterar fotografia" in page and f"/avatar/{row['id']}?v=" in page   # também no menu do topo


def test_trocar_muda_o_endereco(client, app):
    login(client, "ana@teste.pt")
    upload(client, picture())
    first = user_row(app)["avatar_updated_at"]
    with app.app_context():
        db = get_db()
        with db:
            db.execute("UPDATE users SET avatar_updated_at = '2020-01-01 00:00:00' WHERE email = 'ana@teste.pt'")
    upload(client, picture((300, 900), fmt="PNG"))
    assert user_row(app)["avatar_updated_at"] != "2020-01-01 00:00:00" and first


def test_remover_fotografia(client, app):
    login(client, "ana@teste.pt")
    upload(client, picture())
    client.post("/conta/fotografia", data={"action": "remover"})
    row = user_row(app)
    assert row["avatar"] is None and client.get(f"/avatar/{row['id']}").status_code == 404


def test_ficheiro_invalido_e_recusado(client, app):
    login(client, "ana@teste.pt")
    resp = client.post("/conta/fotografia", data={"avatar": (io.BytesIO(b"nao sou imagem"), "x.jpg")},
                       content_type="multipart/form-data", follow_redirects=True)
    assert "Não foi possível ler esta imagem" in resp.get_data(as_text=True) and user_row(app)["avatar"] is None


def test_precisa_de_sessao(client):
    assert client.post("/conta/fotografia").status_code in (302, 401, 403)


def test_aceita_qualquer_formato_de_imagem(client, app):
    """GIF animado, BMP, TIFF, PNG com transparência e imagens grandes: tudo vira um JPEG 320×320."""
    login(client, "ana@teste.pt")
    def make(fmt, mode="RGB", size=(900, 600), **kw):
        buf = io.BytesIO()
        img = Image.new(mode, size, (200, 40, 40, 0) if mode == "RGBA" else (200, 40, 40))
        img.save(buf, format=fmt, **kw); buf.seek(0); return buf
    gif = io.BytesIO()
    frames = [Image.new("P", (300, 300), i) for i in range(3)]
    frames[0].save(gif, format="GIF", save_all=True, append_images=frames[1:]); gif.seek(0)
    for name, data in [("a.gif", gif), ("a.bmp", make("BMP")), ("a.tiff", make("TIFF")), ("a.png", make("PNG", "RGBA")),
                       ("grande.png", make("PNG", size=(5000, 3000)))]:
        before = user_row(app)["avatar_updated_at"]
        with app.app_context():
            db = get_db()
            with db:
                db.execute("UPDATE users SET avatar = NULL, avatar_updated_at = NULL WHERE email = 'ana@teste.pt'")
        client.post("/conta/fotografia", data={"avatar": (data, name)}, content_type="multipart/form-data")
        row = user_row(app)
        assert row["avatar"] is not None, name
        with Image.open(io.BytesIO(bytes(row["avatar"]))) as img:
            assert img.format == "JPEG" and img.size == (320, 320), name
