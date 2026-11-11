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
import geo
import reference

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
    REGISTADA: "Recebida e encaminhada para a entidade responsável.",
    EM_ANALISE: "A entidade responsável está a verificar os dados.",
    EM_TRATAMENTO: "A situação foi encaminhada para intervenção.",
    RESOLVIDA: "A intervenção foi registada como concluída.",
    REJEITADA: "Fora do âmbito ou informação insuficiente.",
    DUPLICADA: "Já existe um ticket sobre o mesmo problema.",
}

PRIORITIES = ("Baixa", "Média", "Alta")
SERVICES = reference.SERVICES

ROLE_LABELS = {"citizen": "Munícipe", "staff": "Técnico", "admin": "Central municipal"}

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


CONTENT_TYPES = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}


def _store_images(upload_dir, images):
    """Prepara as imagens para gravar NA BASE DE DADOS, com nomes aleatórios gerados no servidor.
    (Não depende do disco do servidor — nos alojamentos gratuitos o disco apaga-se ao reiniciar.)
    Devolve [(nome, tipo, bytes)]."""
    return [(f"{secrets.token_hex(16)}.{ext}", CONTENT_TYPES[ext], data) for data, ext in images]


def _uploaded(files):
    return [f for f in files if f and f.filename]


def parse_coordinate(value):
    try:
        return float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ Contas
PARISH_CODES = {code for code, _ in reference.PARISHES}


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
    if parish is not None and parish not in PARISH_CODES:
        errors["parish"] = "Escolha uma freguesia da lista."
    if not form.get("terms"):
        errors["terms"] = "Tem de aceitar as condições de utilização."
    if errors:
        raise ValidationError(errors)
    return name, email, password, parish


def create_user(db, name, email, password_hash, role="citizen", parish=None, organization_id=None):
    """Cria a conta. Um munícipe passa a seguir automaticamente a sua freguesia (alertas)."""
    with db:
        user_id = db.execute(
            """INSERT INTO users (name, email, password_hash, role, organization_id, parish_code, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING id""",
            (name, email.lower(), password_hash, role, organization_id, parish, utcnow()),
        ).fetchone()[0]
        if parish:
            db.execute("INSERT INTO user_parishes (user_id, parish_code) VALUES (?, ?) ON CONFLICT DO NOTHING",
                       (user_id, parish))
    return user_id


def organization_by(db, kind, value=None):
    if kind == "camara":
        return db.execute("SELECT * FROM organizations WHERE kind = 'camara' ORDER BY id LIMIT 1").fetchone()
    column = "service" if kind == "servico" else "parish_code"
    return db.execute(f"SELECT * FROM organizations WHERE kind = ? AND {column} = ?", (kind, value)).fetchone()


def followed_parishes(db, user_id):
    return [r["parish_code"] for r in db.execute(
        "SELECT parish_code FROM user_parishes WHERE user_id = ? ORDER BY parish_code", (user_id,)).fetchall()]


def set_followed_parishes(db, user_id, codes):
    codes = [c for c in dict.fromkeys(codes) if c in PARISH_CODES]
    with db:
        db.execute("DELETE FROM user_parishes WHERE user_id = ?", (user_id,))
        for code in codes:
            db.execute("INSERT INTO user_parishes (user_id, parish_code) VALUES (?, ?)", (user_id, code))
    return codes


# ------------------------------------------------------------------ Permissões por entidade
def scope_clause(user):
    """Que ocorrências cada perfil trata: admin = todas; técnico = as da sua entidade
    (uma Junta vê também tudo o que acontece na sua freguesia)."""
    if user["role"] == "admin":
        return "", []
    if user["role"] == "staff":
        if user["org_kind"] == "junta":
            return "(i.organization_id = ? OR i.parish_code = ?)", [user["organization_id"], user["org_parish"]]
        if user["org_kind"] == "camara":
            return "", []
        return "i.organization_id = ?", [user["organization_id"]]
    return "i.author_id = ?", [user["id"]]


def can_manage(user, incident):
    if user is None:
        return False
    if user["role"] == "admin" or (user["role"] == "staff" and user["org_kind"] == "camara"):
        return True
    if user["role"] == "staff":
        return incident["organization_id"] == user["organization_id"] or (
            user["org_kind"] == "junta" and incident["parish_code"] == user["org_parish"])
    return False


def can_view(user, incident):
    return user is not None and (incident["author_id"] == user["id"] or can_manage(user, incident))


def route_incident(db, category_id, parish_code):
    """Encaminhamento automático: serviço técnico da categoria → Junta da freguesia → Câmara."""
    cat = db.execute("SELECT default_service FROM categories WHERE id = ?", (category_id,)).fetchone()
    org = None
    if cat and cat["default_service"]:
        org = organization_by(db, "servico", cat["default_service"])
    if org is None and parish_code:
        org = organization_by(db, "junta", parish_code)
    if org is None:
        org = organization_by(db, "camara")
    return org


def _notify(db, user_ids, incident_id, kind, message, now, exclude=()):
    for uid in dict.fromkeys(user_ids):
        if uid in exclude:
            continue
        db.execute("INSERT INTO notifications (user_id, incident_id, kind, message, created_at) VALUES (?, ?, ?, ?, ?)",
                   (uid, incident_id, kind, message, now))


# ------------------------------------------------------------------ Ocorrências
def create_incident(db, upload_dir, author, form, files, max_bytes):
    """RF03–RF06: valida, grava imagens, cria o ticket, encaminha e avisa quem deve receber."""
    if author["role"] != "citizen":
        raise PermissionError("Apenas munícipes podem registar ocorrências.")

    errors = {}
    category = None
    try:
        category_id = int(form.get("category_id", ""))
        category = db.execute(
            "SELECT id, name FROM categories WHERE id = ? AND active = TRUE", (category_id,)
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
    parish_code = None
    if lat is None or lng is None:
        errors["location"] = "Marque o local no mapa ou indique as coordenadas."
    elif not (BOUNDS["lat_min"] <= lat <= BOUNDS["lat_max"] and BOUNDS["lng_min"] <= lng <= BOUNDS["lng_max"]):
        errors["location"] = "A localização tem de ficar no concelho do Fundão."
    else:
        parish_code = geo.parish_at(lat, lng)
        if parish_code is None and geo.available():
            errors["location"] = "Esse ponto fica fora do concelho do Fundão."

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

    org = route_incident(db, category["id"], parish_code)
    saved = _store_images(upload_dir, images)
    now = utcnow()
    with db:  # transação: ou grava tudo, ou não grava nada
        incident_id = db.execute(
            """INSERT INTO incidents (author_id, category_id, parish_code, organization_id, title, description,
                   latitude, longitude, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id""",
            (author["id"], category["id"], parish_code, org["id"], title, description,
             round(lat, 6), round(lng, 6), REGISTADA, now, now),
        ).fetchone()[0]
        ticket = f"TU-{now[:4]}-{incident_id:04d}"  # RF06: TU-AAAA-NNNN
        db.execute("UPDATE incidents SET ticket = ? WHERE id = ?", (ticket, incident_id))
        db.executemany(
            """INSERT INTO incident_images (incident_id, stored_filename, content_type, data, kind, uploaded_at)
               VALUES (?, ?, ?, ?, 'report', ?)""",
            [(incident_id, name, ctype, data, now) for name, ctype, data in saved],
        )
        db.execute(
            """INSERT INTO status_history (incident_id, actor_id, previous_status, new_status, note, changed_at)
               VALUES (?, ?, NULL, ?, ?, ?)""",
            (incident_id, author["id"], REGISTADA, f"Ocorrência registada e encaminhada para {org['name']}.", now),
        )
        community.award(db, author["id"], "report", incident_id, now)

        # Recetores 1: os técnicos da entidade responsável (e da Junta do local).
        staff = [r["id"] for r in db.execute(
            """SELECT u.id FROM users u JOIN organizations o ON o.id = u.organization_id
                WHERE u.role = 'staff' AND u.active = TRUE AND (o.id = ? OR (o.kind = 'junta' AND o.parish_code = ?))""",
            (org["id"], parish_code)).fetchall()]
        _notify(db, staff, incident_id, "assigned", f"Nova ocorrência {ticket}: {category['name']}.", now)

        # Recetores 2: todas as pessoas que seguem esta freguesia (alertas, como no Waze).
        if parish_code:
            place = db.execute("SELECT short_name FROM parishes WHERE code = ?", (parish_code,)).fetchone()
            followers = [r["user_id"] for r in db.execute(
                "SELECT user_id FROM user_parishes WHERE parish_code = ?", (parish_code,)).fetchall()]
            _notify(db, followers, incident_id, "zone",
                    f"Novo problema em {place['short_name']}: {category['name']} ({ticket}).", now,
                    exclude={author["id"], *staff})
    return incident_id, ticket


def change_status(db, upload_dir, actor, incident, form, files, max_bytes):
    """RF09: altera estado/classificação com nota, numa única transação."""
    if not can_manage(actor, incident):
        raise PermissionError("Sem permissão para tratar esta ocorrência.")

    current = incident["status"]
    if current in CLOSED_STATUSES:
        raise ValidationError({"new_status": "Esta ocorrência já está encerrada."})

    new_status = form.get("new_status", current)
    note = form.get("note", "").strip()
    priority = form.get("priority", "") or None
    errors = {}

    # Só a central pode passar a ocorrência para outra entidade.
    org_id = incident["organization_id"]
    new_org = None
    if form.get("organization_id") and str(form.get("organization_id")) != str(org_id):
        if actor["role"] != "admin":
            errors["organization_id"] = "Só a central municipal pode reencaminhar ocorrências."
        else:
            new_org = db.execute("SELECT * FROM organizations WHERE id = ? AND active = TRUE",
                                 (form.get("organization_id"),)).fetchone()
            if new_org is None:
                errors["organization_id"] = "Entidade inválida."

    if new_status != current and new_status not in TRANSITIONS.get(current, ()):
        errors["new_status"] = f"Transição não permitida: {current} → {new_status}."
    if not 3 <= len(note) <= 1000:
        errors["note"] = "Escreva uma observação (3 a 1000 caracteres)."
    if priority is not None and priority not in PRIORITIES:
        errors["priority"] = "Prioridade inválida."

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
    if new_org is not None:
        changes.append(f"Encaminhada para: {new_org['name']}")
    full_note = note + (f"\n[{' · '.join(changes)}]" if changes else "")

    saved = _store_images(upload_dir, images)
    now = utcnow()
    resolved = new_status == RESOLVIDA
    with db:
        # Uma só instrução: a regra «Resolvida ⇒ tem data de resolução» é verificada pela base de dados.
        db.execute(
            """UPDATE incidents
                  SET status = ?, priority = ?, organization_id = ?, assigned_to = ?, updated_at = ?,
                      resolved_at = COALESCE(?, resolved_at), resolution_note = COALESCE(?, resolution_note)
                WHERE id = ?""",
            (new_status, priority, new_org["id"] if new_org else org_id, actor["id"], now,
             now if resolved else None, note if resolved else None, incident["id"]),
        )
        db.execute(
            """INSERT INTO status_history (incident_id, actor_id, previous_status, new_status, note, changed_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (incident["id"], actor["id"], current, new_status, full_note, now),
        )
        db.executemany(
            """INSERT INTO incident_images (incident_id, stored_filename, content_type, data, kind, uploaded_at)
               VALUES (?, ?, ?, ?, 'resolution', ?)""",
            [(incident["id"], name, ctype, data, now) for name, ctype, data in saved],
        )
        if resolved:
            community.award(db, incident["author_id"], "resolved", incident["id"], now)
        if new_status != current:
            # O autor e quem confirmou «Eu também vi» acompanham a evolução.
            _notify(db, [incident["author_id"]], incident["id"], "status",
                    f"A sua ocorrência {incident['ticket']} passou a «{new_status}».", now)
            watchers = [r["user_id"] for r in db.execute(
                "SELECT user_id FROM confirmations WHERE incident_id = ? AND kind = 'seen'",
                (incident["id"],)).fetchall()]
            _notify(db, watchers, incident["id"], "status",
                    f"{incident['ticket']}, que confirmou, passou a «{new_status}».", now,
                    exclude={incident["author_id"]})
        if new_org is not None:
            staff = [r["id"] for r in db.execute(
                "SELECT id FROM users WHERE role = 'staff' AND active = TRUE AND organization_id = ?",
                (new_org["id"],)).fetchall()]
            _notify(db, staff, incident["id"], "assigned",
                    f"{incident['ticket']} foi-lhe encaminhada pela central municipal.", now)
    return new_status


# ------------------------------------------------------------------ Consultas
INCIDENT_SELECT = """
    SELECT i.*, c.name AS category, u.name AS author_name, a.name AS assigned_name,
           p.short_name AS parish, o.name AS organization, o.kind AS organization_kind,
           (SELECT im.id FROM incident_images im
             WHERE im.incident_id = i.id AND im.kind = 'report' ORDER BY im.id LIMIT 1) AS thumb_id,
           (SELECT COUNT(*) FROM confirmations cf WHERE cf.incident_id = i.id AND cf.kind = 'seen') AS seen_count,
           (SELECT COUNT(*) FROM confirmations cf WHERE cf.incident_id = i.id AND cf.kind = 'fixed') AS fixed_count
      FROM incidents i
      JOIN categories c ON c.id = i.category_id
      JOIN users u ON u.id = i.author_id
      LEFT JOIN users a ON a.id = i.assigned_to
      LEFT JOIN parishes p ON p.code = i.parish_code
      LEFT JOIN organizations o ON o.id = i.organization_id
"""


def get_incident(db, incident_id):
    return db.execute(INCIDENT_SELECT + " WHERE i.id = ?", (incident_id,)).fetchone()


def get_history(db, incident_id):
    return db.execute(
        """SELECT h.*, u.name AS actor_name, u.role AS actor_role, o.name AS actor_org
             FROM status_history h JOIN users u ON u.id = h.actor_id
             LEFT JOIN organizations o ON o.id = u.organization_id
            WHERE h.incident_id = ? ORDER BY h.changed_at, h.id""",
        (incident_id,),
    ).fetchall()


def get_images(db, incident_id):
    return db.execute(
        "SELECT id, incident_id, stored_filename, kind, uploaded_at FROM incident_images WHERE incident_id = ? ORDER BY id",
        (incident_id,)
    ).fetchall()


def search_incidents(db, view="ativas", status="", category_id="", q="", author_id=None, sort="recentes",
                     user=None, parish=""):
    """RF08: pesquisa e filtros (sempre com parâmetros — nunca concatenar texto do utilizador)."""
    where, params = [], []
    if user is not None:
        clause, extra = scope_clause(user)
        if clause:
            where.append(clause)
            params.extend(extra)
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
    if parish in PARISH_CODES:
        where.append("i.parish_code = ?")
        params.append(parish)
    if q:
        where.append("(lower(i.ticket) LIKE ? OR lower(i.title) LIKE ? OR lower(i.description) LIKE ?)")
        like = f"%{q.lower()}%"
        params.extend([like, like, like])
    sql = INCIDENT_SELECT + (" WHERE " + " AND ".join(where) if where else "")
    if sort == "confirmadas":  # as mais confirmadas pela comunidade primeiro
        sql += " ORDER BY seen_count DESC, i.created_at DESC, i.id DESC"
    else:
        sql += " ORDER BY i.created_at DESC, i.id DESC"
    return db.execute(sql, params).fetchall()


def status_counts(db, author_id=None, user=None):
    where, params = [], []
    if user is not None:
        clause, extra = scope_clause(user)
        if clause:
            where.append(clause)
            params.extend(extra)
    if author_id is not None:
        where.append("i.author_id = ?")
        params.append(author_id)
    sql = "SELECT i.status, COUNT(*) AS n FROM incidents i" + (" WHERE " + " AND ".join(where) if where else "")
    rows = db.execute(sql + " GROUP BY i.status", params).fetchall()
    counts = {s: 0 for s in ALL_STATUSES}
    counts.update({r["status"]: r["n"] for r in rows})
    return counts


def category_counts(db, user=None):
    clause, params = scope_clause(user) if user is not None else ("", [])
    join_filter = f" AND {clause}" if clause else ""
    return db.execute(
        f"""SELECT c.name, COUNT(i.id) AS n FROM categories c
              LEFT JOIN incidents i ON i.category_id = c.id{join_filter}
             GROUP BY c.id, c.name ORDER BY n DESC, c.name""",
        params,
    ).fetchall()


def parish_overview(db):
    """Por freguesia: ocorrências ativas/resolvidas (para o mapa e as páginas de freguesia)."""
    active = ",".join("?" * len(ACTIVE_STATUSES))
    return db.execute(
        f"""SELECT p.code, p.name, p.short_name, p.landmark, p.image_credit,
                   COUNT(CASE WHEN i.status IN ({active}) THEN 1 END) AS active,
                   COUNT(CASE WHEN i.status = ? THEN 1 END) AS resolved
              FROM parishes p LEFT JOIN incidents i ON i.parish_code = p.code
             GROUP BY p.code, p.name, p.short_name, p.landmark, p.image_credit
             ORDER BY p.short_name""",
        (*ACTIVE_STATUSES, RESOLVIDA),
    ).fetchall()
