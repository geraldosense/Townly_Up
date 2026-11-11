"""Moderação automática das fotografias (Claude API — visão).

Antes de uma ocorrência ser gravada, cada fotografia é analisada por um modelo de visão:
  • conteúdo impróprio (sexual, nudez, violência explícita, ódio)  → recusada;
  • fotografia sem relação com um problema no espaço público
    (selfie, meme, captura de ecrã, interior de casa, animal de estimação...) → recusada.
Quem insistir em enviar conteúdo impróprio fica com a conta suspensa (ver services).

Configuração (variáveis de ambiente):
  ANTHROPIC_API_KEY   chave da API (sem ela a moderação fica DESLIGADA — útil em desenvolvimento)
  MODERATION_MODEL    modelo a usar (por omissão claude-haiku-5-5, o mais económico)

Usa só a biblioteca padrão (urllib): não é preciso instalar nada.
"""
import base64
import io
import json
import os
import urllib.error
import urllib.request

from PIL import Image

API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-haiku-5-5"
TIMEOUT_S = 25

# Motivos devolvidos pelo modelo → mensagem para o munícipe
REASONS = {
    "sexual": "a fotografia tem conteúdo sexual ou nudez",
    "violencia": "a fotografia tem violência explícita",
    "odio": "a fotografia tem conteúdo ofensivo ou de ódio",
    "sem_relacao": "a fotografia não mostra um problema no espaço público",
}
INAPPROPRIATE = {"sexual", "violencia", "odio"}

SYSTEM_PROMPT = """És o moderador de fotografias do Townly Up, uma plataforma onde os munícipes do concelho \
do Fundão (Portugal) comunicam problemas no espaço público: buracos e pavimento danificado, iluminação \
avariada, lixo e contentores, árvores e jardins, sinalização, água e saneamento, mobiliário urbano, etc.

Analisa a fotografia e decide:
- "sexual": nudez ou conteúdo sexual (mesmo parcial ou artístico).
- "violencia": violência explícita, sangue, armas apontadas a pessoas, crueldade.
- "odio": símbolos ou mensagens de ódio, insultos ou gestos ofensivos como tema principal.
- "sem_relacao": não mostra um espaço público nem um problema plausível (selfie ou retrato, meme, \
desenho, captura de ecrã, documento, interior de casa, comida, animal de estimação como tema, imagem \
totalmente escura/desfocada sem nada reconhecível).
- "ok": mostra rua, passeio, estrada, praça, jardim, edifício público, equipamento urbano ou natureza \
do concelho, com um problema visível ou plausível. Na dúvida sobre a relevância, escolhe "ok"; \
na dúvida sobre conteúdo sexual ou violento, recusa.
Responde apenas com a ferramenta registar_avaliacao."""

TOOL = {
    "name": "registar_avaliacao",
    "description": "Regista a avaliação da fotografia enviada para o Townly Up.",
    "input_schema": {
        "type": "object",
        "properties": {
            "veredicto": {"type": "string", "enum": ["ok", "sexual", "violencia", "odio", "sem_relacao"]},
            "descricao": {"type": "string", "description": "O que a fotografia mostra, numa frase curta em português."},
        },
        "required": ["veredicto", "descricao"],
    },
}


class ModerationError(Exception):
    """A análise não pôde ser feita (rede, chave inválida, limite da API...)."""


class Rejected(Exception):
    def __init__(self, reason, index, description=""):
        self.reason, self.index, self.description = reason, index, description
        super().__init__(REASONS.get(reason, reason))

    @property
    def inappropriate(self):
        return self.reason in INAPPROPRIATE


def enabled():
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _small_jpeg(data, max_side=1024):
    """Reduz a imagem antes de a enviar (mais rápido e mais barato)."""
    with Image.open(io.BytesIO(data)) as img:
        img = img.convert("RGB")
        img.thumbnail((max_side, max_side))
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=80)
        return out.getvalue()


def _ask(image_bytes, category):
    body = {
        "model": os.environ.get("MODERATION_MODEL", DEFAULT_MODEL),
        "max_tokens": 200,
        "system": SYSTEM_PROMPT,
        "tools": [TOOL],
        "tool_choice": {"type": "tool", "name": TOOL["name"]},
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                             "data": base64.b64encode(_small_jpeg(image_bytes)).decode()}},
                {"type": "text", "text": f"Categoria escolhida pelo munícipe: {category}."},
            ],
        }],
    }
    req = urllib.request.Request(API_URL, data=json.dumps(body).encode(), method="POST", headers={
        "x-api-key": os.environ["ANTHROPIC_API_KEY"],
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    })
    try:
        from app import _ssl_context  # certificados (Python de python.org no Mac)
        with urllib.request.urlopen(req, timeout=TIMEOUT_S, context=_ssl_context()) as resp:
            answer = json.load(resp)
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise ModerationError(str(exc)) from exc
    for block in answer.get("content", []):
        if block.get("type") == "tool_use":
            verdict = block.get("input", {}).get("veredicto")
            if verdict in ("ok", *REASONS):
                return verdict, block["input"].get("descricao", "")
    raise ModerationError("resposta inesperada do modelo")


# Ponto de entrada usado pelos serviços (os testes substituem esta função).
def review(images, category):
    """Analisa [(bytes, ext)]. Lança Rejected na 1.ª fotografia recusada, ModerationError se falhar.
    Sem ANTHROPIC_API_KEY não faz nada (moderação desligada)."""
    if not enabled():
        return
    for index, (data, _ext) in enumerate(images):
        verdict, description = _ask(data, category)
        if verdict != "ok":
            raise Rejected(verdict, index, description)
