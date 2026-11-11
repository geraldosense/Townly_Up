"""Regras de negócio do Townly Up.

Toda a lógica importante está aqui (e não nas rotas) para poder ser testada
e explicada separadamente: validação, criação de tickets, transições de
estado, histórico e notificações.
"""
import io
import os
import re
import secrets
from datetime import datetime, timezone

from PIL import Image, ImageOps, UnidentifiedImageError

import community

# ------------------------------------------------------------------ Estados
REGISTADA = "Registada"
EM_ANALISE = "Em análise"
EM_TRATAMENTO = "Em tratamento"
RESOLVIDA = "Resolvida"
REJEITADA = "Rejeitada"
DUPLICADA = "Duplicada"

ACTIVE_STATUSES = (REGISTADA, EM_ANALISE, EM_TRATAMENTO)
CLOSED_STATUSES = (RESOLVIDA, REJEITADA, DUPLICADA)
ALL_STATUSES = ACTIVE_STATUSES + CLOSED_STATUSES

# Fluxo permitido (regra de negócio do caderno de encargos).
TRANSITIONS = {
    REGISTADA: (EM_ANALISE, REJEITADA, DUPLICADA),
    EM_ANALISE: (EM_TRATAMENTO, REJEITADA, DUPLICADA),
    EM_TRATAMENTO: (RESOLVIDA,),
    RESOLVIDA: (),
    REJEITADA: (),
    DUPLICADA: (),
}

STATUS_DESCRIPTIONS = {
    REGISTADA: "Recebemos a sua ocorrência.",
    EM_ANALISE: "A central está a verificar os dados.",
    EM_TRATAMENTO: "A situação foi encaminhada para intervenção.",
    RESOLVIDA: "A intervenção foi registada como concluída.",
    REJEITADA: "Fora do âmbito ou informação insuficiente.",
    DUPLICADA: "Já existe um ticket sobre o mesmo problema.",
}

PRIORITIES = ("Baixa", "Média", "Alta")
SERVICES = (
    "Obras Municipais",
    "Espaços Verdes",
    "Iluminação Pública",
    "Limpeza Urbana",
    "Trânsito e Sinalização",
    "Águas e Saneamento",
)

# Limites aproximados do concelho do Fundão (validação da localização).
FUNDAO_CENTER = (40.1405, -7.5012)
BOUNDS = {"lat_min": 39.95, "lat_max": 40.35, "lng_min": -7.85, "lng_max": -7.20}

# ------------------------------------------------------------------ Imagens
MAX_IMAGES = 3
ALLOWED_FORMATS = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}
Image.MAX_IMAGE_PIXELS = 40_000_000  # proteção contra "bombas" de descompressão
TICKET_RE = re.compile(r"TU-\d{4}-\d{4,}")


class ValidationError(Exception):
    """Erros de validação por campo: {'title': 'mensagem', ...}."""

    def __init__(self, errors):
        self.errors = errors
        super().__init__("; ".join(errors.values()))


def utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def status_slug(status):
    """'Em análise' -> 'em-analise' (usado nas classes CSS)."""
    table = str.maketrans("áàâãéêíóôõúç", "aaaaeeiooouc")
    return (status or "").lower().translate(table).replace(" ", "-")


def process_image(file_storage, max_bytes):
    """Valida e normaliza uma imagem enviada (RNF04).

    1. limita o tamanho;
    2. confirma pelo CONTEÚDO (não pela extensão) que é JPEG/PNG/WebP;
    3. volta a gravar a imagem com o Pillow — isto remove metadados EXIF,
       incluindo coordenadas GPS e dados do telemóvel (privacidade).
    Devolve (bytes, extensão). Lança ValueError com uma mensagem clara.
    """
    data = file_storage.read(max_bytes + 1)
    if not data:
        raise ValueError("o ficheiro está vazio")
    if len(data) > max_bytes:
        raise ValueError(f"excede o limite de {max_bytes // (1024 * 1024)} MB")
    try:
        with Image.open(io.BytesIO(data)) as probe:
            fmt = probe.format
            probe.verify()
        if fmt not in ALLOWED_FORMATS:
            raise ValueError("formato não permitido (use JPEG, PNG ou WebP)")
        with Image.open(io.BytesIO(data)) as img:
            img = ImageOps.exif_transpose(img)
            img.thumbnail((1920, 1920))
            if fmt == "JPEG" and img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            out = io.BytesIO()
            options = {"quality": 85} if fmt in ("JPEG", "WEBP") else {"optimize": True}
            img.save(out, format=fmt, **options)
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError):
        raise ValueError("não é uma imagem válida") from None
    return out.getvalue(), ALLOWED_FORMATS[fmt]


def _store_images(upload_dir, images):
    """Grava as imagens com nomes aleatórios gerados no servidor."""
    names = []
    for data, ext in images:
        name = f"{secrets.token_hex(16)}.{ext}"
        with open(os.path.join(upload_dir, name), "wb") as fh:
            fh.write(data)
        names.append(name)
    return names


def _remove_files(upload_dir, names):
    for name in names:
        try:
            os.remove(os.path.join(upload_dir, name))
        except OSError:
            pass


def _uploaded(files):
    return [f for f in files if f and f.filename]


def parse_coordinate(value):
    try:
        return float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ Contas
def validate_registration(db, form):
    name = form.get("name", "").strip()
    email = form.get("email", "").strip().lower()
    password = form.get("password", "")
    confirm = form.get("confirm", "")
    errors = {}
    if not 2 <= len(name) <= 100:
        errors["name"] = "Indique o seu nome (2 a 100 caracteres)."
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) or len(email) > 120:
        errors["email"] = "Indique um email válido."
    elif db.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
        errors["email"] = "Já existe uma conta com este email."
    if len(password) < 8 or not re.search(r"\d", password) or not re.search(r"[A-Za-z]", password):
        errors["password"] = "A palavra-passe precisa de 8+ caracteres, com letras e números."
    elif password != confirm:
        errors["confirm"] = "As palavras-passe não coincidem."
    parish = form.get("parish", "").strip() or None
    if parish is not None and parish not in community.PARISHES:
        errors["parish"] = "Escolha uma freguesia da lista."
    if not form.get("terms"):
        errors["terms"] = "Tem de aceitar as condições de utilização."
    if errors:
        raise ValidationError(errors)
    return name, email, password, parish


def create_user(db, name, email, password_hash, role="citizen", parish=None):
    with db:
        cur = db.execute(
            "INSERT INTO users (name, email, password_hash, role, parish, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (name, email.lower(), password_hash, role, parish, utcnow()),
        )
    return cur.lastrowid


# ------------------------------------------------------------------ Ocorrências
def create_incident(db, upload_dir, author, form, files, max_bytes):
    """RF03–RF06: valida, grava imagens, cria o ticket e o 1.º evento do histórico."""
    if author["role"] != "citizen":
        raise PermissionError("Apenas munícipes podem registar ocorrências.")

    errors = {}
    category = None
    try:
        category_id = int(form.get("category_id", ""))
        category = db.execute(
            "SELECT id, name FROM categories WHERE id = ? AND active = 1", (category_id,)
        ).fetchone()
    except ValueError:
        pass
    if category is None:
        errors["category_id"] = "Escolha uma categoria."

    # Comunicar em 3 passos: o título é opcional (usa-se o nome da categoria).
    title = form.get("title", "").strip()
    if not title and category is not None:
        title = category["name"]
    if not 3 <= len(title) <= 120:
        errors["title"] = "O título deve ter entre 3 e 120 caracteres."
    description = form.get("description", "").strip()
    if not 10 <= len(description) <= 2000:
        errors["description"] = "A descrição deve ter entre 10 e 2000 caracteres."

    lat = parse_coordinate(form.get("latitude"))
    lng = parse_coordinate(form.get("longitude"))
    if lat is None or lng is None:
        errors["location"] = "Marque o local no mapa ou indique as coordenadas."
    elif not (BOUNDS["lat_min"] <= lat <= BOUNDS["lat_max"] and BOUNDS["lng_min"] <= lng <= BOUNDS["lng_max"]):
        errors["location"] = "A localização tem de ficar no concelho do Fundão."

    if not form.get("privacy"):
        errors["privacy"] = "Confirme que as fotografias não identificam pessoas nem matrículas."

    images = []
    uploads = _uploaded(files)
    if not uploads:
        errors["photos"] = "Anexe pelo menos uma fotografia do problema."
    elif len(uploads) > MAX_IMAGES:
        errors["photos"] = f"Pode anexar no máximo {MAX_IMAGES} fotografias."
    else:
        for f in uploads:
            try:
                images.append(process_image(f, max_bytes))
            except ValueError as exc:
                errors["photos"] = f"«{f.filename}»: {exc}."
                break

    if errors:
        raise ValidationError(errors)

    saved = _store_images(upload_dir, images)
    try:
        now = utcnow()
        with db:  # transação: ou grava tudo, ou não grava nada
            cur = db.execute(
                """INSERT INTO incidents (author_id, category_id, title, description,
                       latitude, longitude, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (author["id"], category["id"], title, description,
                 round(lat, 6), round(lng, 6), REGISTADA, now, now),
            )
            incident_id = cur.lastrowid
            ticket = f"TU-{now[:4]}-{incident_id:04d}"  # RF06: TU-AAAA-NNNN
            db.execute("UPDATE incidents SET ticket = ? WHERE id = ?", (ticket, incident_id))
            db.executemany(
                "INSERT INTO incident_images (incident_id, stored_filename, kind, uploaded_at) VALUES (?, ?, 'report', ?)",
                [(incident_id, name, now) for name in saved],
            )
            db.execute(
                """INSERT INTO status_history (incident_id, actor_id, previous_status, new_status, note, changed_at)
                   VALUES (?, ?, NULL, ?, ?, ?)""",
                (incident_id, author["id"], REGISTADA, "Ocorrência registada pelo munícipe.", now),
            )
            community.award(db, author["id"], "report", incident_id, now)
    except Exception:
        _remove_files(upload_dir, saved)
        raise
    return incident_id, ticket


def change_status(db, upload_dir, actor, incident, form, files, max_bytes):
    """RF09: altera estado/classificação com nota, numa única transação."""
    if actor["role"] != "admin":
        raise PermissionError("Apenas administradores podem alterar o estado.")

    current = incident["status"]
    if current in CLOSED_STATUSES:
        raise ValidationError({"new_status": "Esta ocorrência já está encerrada."})

    new_status = form.get("new_status", current)
    note = form.get("note", "").strip()
    priority = form.get("priority", "") or None
    service = form.get("service", "") or None
    errors = {}

    if new_status != current and new_status not in TRANSITIONS.get(current, ()):
        errors["new_status"] = f"Transição não permitida: {current} → {new_status}."
    if not 3 <= len(note) <= 1000:
        errors["note"] = "Escreva uma observação (3 a 1000 caracteres)."
    if priority is not None and priority not in PRIORITIES:
        errors["priority"] = "Prioridade inválida."
    if service is not None and service not in SERVICES:
        errors["service"] = "Serviço inválido."

    if new_status == DUPLICADA:
        refs = [r for r in TICKET_RE.findall(note) if r != incident["ticket"]]
        if not refs or not db.execute("SELECT 1 FROM incidents WHERE ticket = ?", (refs[0],)).fetchone():
            errors["note"] = "Indique na observação o ticket original (ex.: TU-2026-0001)."

    images = []
    uploads = _uploaded(files)
    if uploads:
        if new_status != RESOLVIDA:
            errors["resolution_photo"] = "A fotografia de conclusão só se anexa ao resolver."
        else:
            try:
                images.append(process_image(uploads[0], max_bytes))
            except ValueError as exc:
                errors["resolution_photo"] = f"Fotografia: {exc}."

    if errors:
        raise ValidationError(errors)

    # Regista na nota também as mudanças de classificação (ficam no histórico).
    changes = []
    if priority != incident["priority"]:
        changes.append(f"Prioridade: {priority or '—'}")
    if service != incident["service"]:
        changes.append(f"Serviço: {service or '—'}")
    full_note = note + (f"\n[{' · '.join(changes)}]" if changes else "")

    saved = _store_images(upload_dir, images)
    try:
        now = utcnow()
        resolved = new_status == RESOLVIDA
        with db:
            db.execute(
                """UPDATE incidents
                      SET status = ?, priority = ?, service = ?, assigned_to = ?, updated_at = ?,
                          resolved_at = CASE WHEN ? THEN ? ELSE resolved_at END,
                          resolution_note = CASE WHEN ? THEN ? ELSE resolution_note END
                    WHERE id = ?""",
                (new_status, priority, service, actor["id"], now,
                 resolved, now, resolved, note, incident["id"]),
            )
            db.execute(
                """INSERT INTO status_history (incident_id, actor_id, previous_status, new_status, note, changed_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (incident["id"], actor["id"], current, new_status, full_note, now),
            )
            db.executemany(
                "INSERT INTO incident_images (incident_id, stored_filename, kind, uploaded_at) VALUES (?, ?, 'resolution', ?)",
                [(incident["id"], name, now) for name in saved],
            )
            if resolved:
                community.award(db, incident["author_id"], "resolved", incident["id"], now)
            if new_status != current:
                db.execute(
                    "INSERT INTO notifications (user_id, incident_id, message, created_at) VALUES (?, ?, ?, ?)",
                    (incident["author_id"], incident["id"],
                     f"A sua ocorrência {incident['ticket']} passou a «{new_status}».", now),
                )
    except Exception:
        _remove_files(upload_dir, saved)
        raise
    return new_status


# ------------------------------------------------------------------ Consultas
INCIDENT_SELECT = """
    SELECT i.*, c.name AS category, u.name AS author_name, a.name AS assigned_name,
           (SELECT im.id FROM incident_images im
             WHERE im.incident_id = i.id AND im.kind = 'report' ORDER BY im.id LIMIT 1) AS thumb_id,
           (SELECT COUNT(*) FROM confirmations cf WHERE cf.incident_id = i.id AND cf.kind = 'seen') AS seen_count,
           (SELECT COUNT(*) FROM confirmations cf WHERE cf.incident_id = i.id AND cf.kind = 'fixed') AS fixed_count
      FROM incidents i
      JOIN categories c ON c.id = i.category_id
      JOIN users u ON u.id = i.author_id
      LEFT JOIN users a ON a.id = i.assigned_to
"""


def get_incident(db, incident_id):
    return db.execute(INCIDENT_SELECT + " WHERE i.id = ?", (incident_id,)).fetchone()


def get_history(db, incident_id):
    return db.execute(
        """SELECT h.*, u.name AS actor_name, u.role AS actor_role
             FROM status_history h JOIN users u ON u.id = h.actor_id
            WHERE h.incident_id = ? ORDER BY h.changed_at, h.id""",
        (incident_id,),
    ).fetchall()


def get_images(db, incident_id):
    return db.execute(
        "SELECT * FROM incident_images WHERE incident_id = ? ORDER BY id", (incident_id,)
    ).fetchall()


def search_incidents(db, view="ativas", status="", category_id="", q="", author_id=None, sort="recentes"):
    """RF08: pesquisa e filtros (sempre com parâmetros — nunca concatenar texto do utilizador)."""
    where, params = [], []
    if author_id is not None:
        where.append("i.author_id = ?")
        params.append(author_id)
    if status in ALL_STATUSES:
        where.append("i.status = ?")
        params.append(status)
    elif view == "ativas":
        where.append(f"i.status IN ({','.join('?' * len(ACTIVE_STATUSES))})")
        params.extend(ACTIVE_STATUSES)
    elif view == "arquivo":
        where.append(f"i.status IN ({','.join('?' * len(CLOSED_STATUSES))})")
        params.extend(CLOSED_STATUSES)
    if str(category_id).isdigit():
        where.append("i.category_id = ?")
        params.append(int(category_id))
    if q:
        where.append("(i.ticket LIKE ? OR i.title LIKE ? OR i.description LIKE ?)")
        like = f"%{q}%"
        params.extend([like, like, like])
    sql = INCIDENT_SELECT + (" WHERE " + " AND ".join(where) if where else "")
    if sort == "confirmadas":  # as mais confirmadas pela comunidade primeiro
        sql += " ORDER BY seen_count DESC, i.created_at DESC, i.id DESC"
    else:
        sql += " ORDER BY i.created_at DESC, i.id DESC"
    return db.execute(sql, params).fetchall()


def status_counts(db, author_id=None):
    sql = "SELECT status, COUNT(*) AS n FROM incidents"
    params = ()
    if author_id is not None:
        sql += " WHERE author_id = ?"
        params = (author_id,)
    rows = db.execute(sql + " GROUP BY status", params).fetchall()
    counts = {s: 0 for s in ALL_STATUSES}
    counts.update({r["status"]: r["n"] for r in rows})
    return counts


def category_counts(db):
    return db.execute(
        """SELECT c.name, COUNT(i.id) AS n FROM categories c
             LEFT JOIN incidents i ON i.category_id = c.id
            GROUP BY c.id ORDER BY n DESC, c.name"""
    ).fetchall()
