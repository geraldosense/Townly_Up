"""Deteção inteligente de ocorrências repetidas.

Compara uma nova ocorrência com as ocorrências ATIVAS próximas e dá-lhes uma pontuação de 0 a 1,
juntando quatro sinais:
  1. distância           — quanto mais perto, mais provável (até 150 m);
  2. categoria           — mesma categoria;
  3. descrição           — palavras importantes em comum (sem acentos nem palavras vazias);
  4. fotografia          — «impressão digital» da imagem (dHash): fotos do mesmo sítio ficam parecidas
                           mesmo com outro tamanho, compressão ou pequenas diferenças de luz.
Acima de LIKELY a ocorrência é mostrada ao munícipe como possível repetição.
"""
import io
import re
import unicodedata

from PIL import Image

import community as cm

SEARCH_RADIUS_M = 150
LIKELY = 0.5      # «parece o mesmo problema»
STRONG = 0.7      # «quase de certeza o mesmo problema»

STOPWORDS = set("""a o as os um uma uns umas de do da dos das no na nos nas em ao aos à às por para com sem
e ou que se ja já mais muito muita muitos muitas esta este isto essa esse isso aqui ali la lá rua há ha
tem está esta estao estão ser foi sao são ha nao não the and""".split())


# ------------------------------------------------------------------ fotografias
def dhash(data, size=8):
    """«Impressão digital» de 64 bits: compara o brilho de píxeis vizinhos numa versão 9×8 a cinzento."""
    with Image.open(io.BytesIO(data)) as img:
        small = img.convert("L").resize((size + 1, size), Image.Resampling.LANCZOS)
        px = list(small.tobytes())
    bits = 0
    for row in range(size):
        for col in range(size):
            left, right = px[row * (size + 1) + col], px[row * (size + 1) + col + 1]
            bits = (bits << 1) | (left > right)
    return f"{bits:016x}"


def hamming(a, b):
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def photo_score(new_hashes, old_hashes):
    """1 = fotografias praticamente iguais; 0 = diferentes."""
    if not new_hashes or not old_hashes:
        return 0.0
    best = min(hamming(a, b) for a in new_hashes for b in old_hashes)
    if best <= 6:
        return 1.0
    if best <= 12:
        return 0.6
    if best <= 18:
        return 0.25
    return 0.0


# ------------------------------------------------------------------ texto
def words(text):
    text = unicodedata.normalize("NFKD", (text or "").lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return {w for w in re.findall(r"[a-z0-9]+", text) if len(w) > 2 and w not in STOPWORDS}


def text_score(a, b):
    wa, wb = words(a), words(b)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


# ------------------------------------------------------------------ pontuação
def score(distance_m, same_category, text_sim, photo_sim):
    """Combina os sinais (pesos somam 1). Devolve (pontuação, motivos legíveis)."""
    reasons = []
    if distance_m <= 25:
        place = 1.0
    elif distance_m <= 50:
        place = 0.75
    elif distance_m <= 100:
        place = 0.4
    else:
        place = 0.15
    reasons.append(f"a {round(distance_m)} m")
    if same_category:
        reasons.append("mesma categoria")
    if text_sim >= 0.2:
        reasons.append("descrição parecida")
    if photo_sim >= 0.6:
        reasons.append("fotografia muito parecida")
    elif photo_sim > 0:
        reasons.append("fotografia semelhante")
    total = 0.35 * place + 0.25 * same_category + 0.2 * min(1.0, text_sim * 2) + 0.2 * photo_sim
    # Fotografia quase igual no mesmo sítio: é praticamente certo que é a mesma coisa.
    if photo_sim == 1.0 and distance_m <= 50:
        total = max(total, STRONG)
    return round(min(total, 1.0), 2), reasons


def find_similar(db, lat, lng, category_id, text, hashes, exclude_id=None, limit=3):
    """Ocorrências ativas parecidas, da mais provável para a menos provável (só acima de LIKELY)."""
    candidates = cm.nearby(db, lat, lng, radius_m=SEARCH_RADIUS_M, exclude_id=exclude_id)
    if not candidates:
        return []
    ids = [c["id"] for c in candidates]
    marks = ",".join("?" * len(ids))
    texts = {r["id"]: f"{r['title']} {r['description']}" for r in db.execute(
        f"SELECT id, title, description FROM incidents WHERE id IN ({marks})", ids)}
    photos = {}
    for r in db.execute(f"SELECT incident_id, phash FROM incident_images WHERE phash IS NOT NULL "
                        f"AND kind = 'report' AND incident_id IN ({marks})", ids):
        photos.setdefault(r["incident_id"], []).append(r["phash"])
    result = []
    for c in candidates:
        value, reasons = score(c["distance_m"], str(c["category_id"]) == str(category_id),
                               text_score(text, texts.get(c["id"], "")), photo_score(hashes, photos.get(c["id"], [])))
        if value >= LIKELY:
            result.append({**c, "score": value, "reasons": reasons, "strong": value >= STRONG})
    return sorted(result, key=lambda x: -x["score"])[:limit]


def hashes_for(images):
    """dHash das fotografias processadas [(bytes, ext)]."""
    return [dhash(data) for data, _ext in images]

