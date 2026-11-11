"""Sistema inteligente: deteção de ocorrências repetidas e moderação automática das fotografias."""
import io

import pytest
from PIL import Image, ImageDraw

import moderation
import similarity
from db import get_db
from test_flow import app, client, create_incident, incident_data, login  # noqa: F401


def photo(seed=0, fmt="JPEG"):
    """Fotografias sintéticas diferentes entre si (padrões distintos)."""
    img = Image.new("RGB", (400, 300), (90 + seed * 40 % 160, 100, 110))
    d = ImageDraw.Draw(img)
    for k in range(6):
        x = (seed * 53 + k * 67) % 400
        d.rectangle((x, (k * 41 + seed * 29) % 300, x + 60, (k * 41 + seed * 29) % 300 + 40), fill=(240, 240 - k * 30, 20 * k))
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    buf.seek(0)
    return buf


def post(client, **kw):
    return client.post("/incidents/new", data=incident_data(**kw), content_type="multipart/form-data")


def count(app, table):
    with app.app_context():
        return get_db().execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# ------------------------------------------------------------------ Repetidos
def test_impressao_digital_das_fotografias():
    a = similarity.dhash(photo(1).getvalue())
    smaller = io.BytesIO()
    Image.open(photo(1)).resize((200, 150)).save(smaller, format="JPEG", quality=60)
    assert similarity.hamming(a, similarity.dhash(smaller.getvalue())) <= 6   # a mesma, reduzida e comprimida
    assert similarity.hamming(a, similarity.dhash(photo(4).getvalue())) > 12  # outra fotografia


def test_texto_ignora_acentos_e_palavras_vazias():
    assert similarity.text_score("Candeeiro apagado na rua", "candeeiro APAGADO junto à escola") >= 0.4
    assert similarity.text_score("Buraco na estrada", "Árvore caída") == 0


def test_mesmo_problema_pede_confirmacao(client, app):
    login(client, "ana@teste.pt")
    first = create_incident(client, photos=[(photo(1), "a.jpg")])
    client.post("/logout")
    login(client, "carlos@teste.pt")
    resp = post(client, photos=[(photo(1), "b.jpg")], latitude="40.14055")  # ~5 m ao lado, a mesma foto
    html = resp.get_data(as_text=True)
    assert resp.status_code == 400 and f'data-duplicate-id="{first}"' in html
    assert "parece já ter sido comunicado" in html and count(app, "incidents") == 1


def test_e_outro_problema_cria_e_fica_registado(client, app):
    login(client, "ana@teste.pt")
    first = create_incident(client, photos=[(photo(1), "a.jpg")])
    resp = post(client, photos=[(photo(1), "b.jpg")], not_duplicate=[str(first)])
    assert resp.status_code == 302
    with app.app_context():
        assert get_db().execute("SELECT similar_to FROM incidents ORDER BY id DESC").fetchone()[0] == first


def test_longe_e_diferente_nao_e_repetido(client, app):
    login(client, "ana@teste.pt")
    create_incident(client, photos=[(photo(1), "a.jpg")])
    resp = post(client, photos=[(photo(5), "b.jpg")], category_id="3", latitude="40.1430",
                description="Contentor do lixo cheio há vários dias.")
    assert resp.status_code == 302


def test_api_de_semelhantes(client, app):
    login(client, "ana@teste.pt")
    first = create_incident(client, photos=[(photo(2), "a.jpg")])
    data = client.post("/api/v1/incidents/similar", data=incident_data(photos=[(photo(2), "b.jpg")]),
                       content_type="multipart/form-data").get_json()
    assert data["incidents"][0]["id"] == first
    assert "fotografia muito parecida" in data["incidents"][0]["reasons"]


# ------------------------------------------------------------------ Moderação
@pytest.fixture
def verdict(monkeypatch):
    """Simula a resposta do modelo de visão (os testes não chamam a API verdadeira)."""
    state = {"value": "ok"}

    def fake_review(images, category):
        if state["value"] == "erro":
            raise moderation.ModerationError("sem rede")
        if state["value"] != "ok":
            raise moderation.Rejected(state["value"], 0, "descrição")
    monkeypatch.setattr(moderation, "review", fake_review)
    return state


def test_fotografia_impropria_e_recusada(client, app, verdict):
    verdict["value"] = "sexual"
    login(client, "ana@teste.pt")
    html = post(client).get_data(as_text=True)
    assert "foi recusada" in html and "conteúdo sexual" in html
    assert count(app, "incidents") == 0 and count(app, "moderation_events") == 1


def test_fotografia_sem_relacao_e_recusada_sem_suspender(client, app, verdict):
    verdict["value"] = "sem_relacao"
    login(client, "ana@teste.pt")
    for _ in range(4):
        assert "não mostra um problema" in post(client).get_data(as_text=True)
    with app.app_context():
        assert get_db().execute("SELECT active FROM users WHERE email = 'ana@teste.pt'").fetchone()[0]


def test_tres_envios_improprios_suspendem_a_conta(client, app, verdict):
    verdict["value"] = "violencia"
    login(client, "ana@teste.pt")
    post(client), post(client)
    resp = post(client)
    assert resp.status_code == 302 and "/login" in resp.headers["Location"]
    with app.app_context():
        assert not get_db().execute("SELECT active FROM users WHERE email = 'ana@teste.pt'").fetchone()[0]
    assert client.get("/citizen").status_code in (302, 401, 403)          # sessão terminada
    assert "incorretos" in client.post("/login", data={"email": "ana@teste.pt", "password": "Teste1234"}).get_data(as_text=True)


def test_falha_da_analise_nao_publica(client, app, verdict):
    verdict["value"] = "erro"
    login(client, "ana@teste.pt")
    assert "Não foi possível verificar" in post(client).get_data(as_text=True)
    assert count(app, "incidents") == 0


def test_sem_chave_a_moderacao_fica_desligada(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert moderation.review([(b"x", "jpg")], "Vias") is None


def test_pedido_a_api_e_leitura_da_resposta(monkeypatch):
    """Formato do pedido (Messages API, ferramenta obrigatória) e leitura do veredicto."""
    import json
    monkeypatch.setenv("ANTHROPIC_API_KEY", "chave-de-teste")
    sent = {}

    class Resp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=None, context=None):
        sent["url"], sent["headers"], sent["body"] = req.full_url, dict(req.header_items()), json.loads(req.data)
        answer = {"content": [{"type": "tool_use", "name": "registar_avaliacao",
                               "input": {"veredicto": "sem_relacao", "descricao": "Uma selfie."}}]}
        return Resp(json.dumps(answer).encode())
    monkeypatch.setattr(moderation.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(moderation.Rejected) as exc:
        moderation.review([(photo(1).getvalue(), "jpg")], "Vias e pavimentos")
    assert exc.value.reason == "sem_relacao" and not exc.value.inappropriate
    body = sent["body"]
    assert sent["url"] == "https://api.anthropic.com/v1/messages"
    assert sent["headers"]["X-api-key"] == "chave-de-teste"
    assert body["tool_choice"] == {"type": "tool", "name": "registar_avaliacao"}
    assert body["messages"][0]["content"][0]["source"]["media_type"] == "image/jpeg"
