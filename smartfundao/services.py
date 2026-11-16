"""Regras de negócio do Townly Up.

Toda a lógica importante está aqui (e não nas rotas) para poder ser testada
e explicada separadamente: validação, criação de tickets, transições de
estado, histórico e notificações.
"""
import io
import os
import re
import secrets
from datetime import datetime, timedelta, timezone

from PIL import Image, ImageOps, UnidentifiedImageError

try:                                    # fotografias HEIC/HEIF do iPhone (opcional: pip install pillow-heif)
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:                       # sem o pacote, o navegador converte antes de enviar
    pillow_heif = None

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

ROLE_LABELS = {"citizen": "Munícipe", "staff": "Câmara / entidade", "admin": "Conta principal da plataforma"}

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

    def __init__(self, errors, similar=None, banned=False, warned=False):
        self.errors = errors
        self.similar = similar or []      # ocorrências parecidas (deteção de repetidos)
        self.banned = banned              # 2.º ato: conta banida automaticamente
        self.warned = warned              # 1.º ato: aviso formal registado
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
            img.thumbnail((1600, 1600))  # suficiente para ver o problema; poupa espaço na base de dados
            if fmt == "JPEG" and img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            out = io.BytesIO()
            options = {"quality": 80, "optimize": True} if fmt in ("JPEG", "WEBP") else {"optimize": True}
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


def password_problem(password):
    if len(password) < 8 or not re.search(r"\d", password) or not re.search(r"[A-Za-z]", password):
        return "A palavra-passe precisa de 8+ caracteres, com letras e números."
    return None


def _check_password(errors, password, confirm, field="password"):
    problem = password_problem(password)
    if problem:
        errors[field] = problem
    elif password != confirm:
        errors["confirm"] = "As palavras-passe não coincidem."


def _check_name_email(db, errors, name, email):
    if not 2 <= len(name) <= 100:
        errors["name"] = "Indique o nome (2 a 100 caracteres)."
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) or len(email) > 120:
        errors["email"] = "Indique um email válido."
    elif db.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
        errors["email"] = "Já existe uma conta com este email."


def validate_registration(db, form, cache_dir=None):
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
    _check_password(errors, password, confirm)
    district, municipality = _check_territory(errors, form)
    parish = home_parish(errors, municipality, form.get("parish", ""), cache_dir)
    if not form.get("terms"):
        errors["terms"] = "Tem de aceitar as condições de utilização."
    if "name" not in errors:
        import profanity
        if profanity.find(name):
            errors["name"] = "Esse nome tem linguagem ofensiva. Indique o seu nome verdadeiro."
    if errors:
        raise ValidationError(errors)
    return name, email, password, parish, district, municipality


def home_parish(errors, municipality, code, cache_dir=None):
    """Freguesia (código DICOFRE) dentro do concelho escolhido. Opcional."""
    import territory
    code = (code or "").strip() or None
    if code is None or "municipality" in errors or "district" in errors:
        return None
    if municipality in territory.ACTIVE:
        if code not in PARISH_CODES:
            errors["parish"] = "Escolha uma freguesia da lista."
        return code
    codes = [c for c, _ in territory.parish_codes(municipality, cache_dir)]
    if (codes and code not in codes) or not re.fullmatch(r"\d{6}", code):
        errors["parish"] = "Escolha uma freguesia da lista."
    return code


def _check_territory(errors, form):
    """Distrito → concelho (os 308 de Portugal)."""
    import territory
    district = form.get("district", "").strip()
    municipality = form.get("municipality", "").strip()
    if district not in territory.DISTRICTS:
        errors["district"] = "Escolha o seu distrito."
    elif not territory.valid(district, municipality):
        errors["municipality"] = "Escolha o seu concelho."
    return district, municipality


def set_territory(db, user, form, cache_dir=None):
    """Mudar de distrito/concelho e freguesia (Definições). A freguesia abre o mapa já nela."""
    errors = {}
    district, municipality = _check_territory(errors, form)
    parish = home_parish(errors, municipality, form.get("parish", ""), cache_dir)
    if errors:
        raise ValidationError(errors)
    local = parish if parish in PARISH_CODES else None   # freguesias com Junta na plataforma (alertas)
    with db:
        db.execute("UPDATE users SET district = ?, municipality = ?, home_parish = ?, parish_code = ? WHERE id = ?",
                   (district, municipality, parish, local, user["id"]))
        if local:
            db.execute("INSERT INTO user_parishes (user_id, parish_code) VALUES (?, ?) ON CONFLICT DO NOTHING", (user["id"], local))
    return municipality


def create_user(db, name, email, password_hash, role="citizen", parish=None, organization_id=None,
                district="Castelo Branco", municipality="Fundão"):
    home = parish
    parish = parish if parish in PARISH_CODES else None
    """Cria a conta. Um munícipe passa a seguir automaticamente a sua freguesia (alertas)."""
    with db:
        user_id = db.execute(
            """INSERT INTO users (name, email, password_hash, role, organization_id, parish_code, created_at, district, municipality, home_parish)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id""",
            (name, email.lower(), password_hash, role, organization_id, parish, utcnow(), district, municipality, home),
        ).fetchone()[0]
        if parish:
            db.execute("INSERT INTO user_parishes (user_id, parish_code) VALUES (?, ?) ON CONFLICT DO NOTHING",
                       (user_id, parish))
    return user_id


# ------------------------------------------------------------------ Contas das entidades (geridas pela Câmara)
ENTITY_KINDS = {"camara": "Câmara Municipal", "junta": "Junta de Freguesia", "servico": "Serviço técnico"}


def create_entity_request(db, form):
    """Pedido de acesso de uma entidade. Não cria conta: a conta principal verifica e cria-a."""
    import profanity
    import territory
    errors = {}
    kind = form.get("kind", "")
    if kind not in ENTITY_KINDS:
        errors["kind"] = "Escolha o tipo de entidade."
    district, municipality = _check_territory(errors, form)
    entity = " ".join(form.get("entity_name", "").split())
    contact = " ".join(form.get("contact_name", "").split())
    email = form.get("email", "").strip().lower()
    phone = re.sub(r"[^\d+]", "", form.get("phone", ""))[:20] or None
    message = " ".join(form.get("message", "").split())[:600] or None
    if not 3 <= len(entity) <= 120:
        errors["entity_name"] = "Indique o nome da entidade (ex.: Junta de Freguesia de Alpedrinha)."
    if not 2 <= len(contact) <= 100:
        errors["contact_name"] = "Indique o nome do responsável."
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) or len(email) > 120:
        errors["email"] = "Indique um email profissional válido."
    if not form.get("terms"):
        errors["terms"] = "Confirme que representa esta entidade."
    for field, text in (("entity_name", entity), ("contact_name", contact), ("message", message or "")):
        if field not in errors and profanity.find(text):
            errors[field] = "Esse texto tem linguagem ofensiva."
    if errors:
        raise ValidationError(errors)
    with db:
        db.execute("""INSERT INTO entity_requests (kind, district, municipality, entity_name, contact_name, email, phone, message, created_at)
                      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                   (kind, district, municipality, entity, contact, email, phone, message, utcnow()))
    return entity


def entity_requests(db, status="pendente"):
    return db.execute("SELECT * FROM entity_requests WHERE status = ? ORDER BY created_at DESC LIMIT 100", (status,)).fetchall()


def close_entity_request(db, request_id):
    with db:
        db.execute("UPDATE entity_requests SET status = 'tratado' WHERE id = ?", (request_id,))


def convert_to_staff(db, form):
    """A conta principal transforma uma conta de cidadão numa conta de entidade (mantém email e palavra-passe)."""
    email = form.get("email", "").strip().lower()
    errors = {}
    user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone() if email else None
    if user is None:
        errors["email"] = "Não existe nenhuma conta com esse email."
    elif user["role"] != "citizen":
        errors["email"] = "Essa conta já é de uma entidade."
    org = None
    try:
        org = db.execute("SELECT * FROM organizations WHERE id = ? AND active = TRUE", (int(form.get("organization_id", "")),)).fetchone()
    except ValueError:
        pass
    if org is None:
        errors["organization_id"] = "Escolha a entidade."
    if errors:
        raise ValidationError(errors)
    with db:
        db.execute("UPDATE users SET role = 'staff', organization_id = ?, parish_code = NULL WHERE id = ?", (org["id"], user["id"]))
        db.execute("DELETE FROM user_parishes WHERE user_id = ?", (user["id"],))
    return user["name"]


def staff_accounts(db):
    return db.execute(
        """SELECT u.id, u.name, u.email, u.role, u.active, u.created_at, u.last_login_at,
                  o.name AS org_name, o.kind AS org_kind, o.municipality AS org_municipality
             FROM users u JOIN organizations o ON o.id = u.organization_id
            ORDER BY o.kind, o.name, u.name""").fetchall()


def organizations_for_select(db):
    return db.execute(
        "SELECT id, name, kind FROM organizations WHERE active = TRUE ORDER BY kind, name").fetchall()


def create_staff(db, form, password_hash_fn):
    """A Câmara cria a conta de um técnico (Junta, serviço técnico ou a própria Câmara)."""
    name = form.get("name", "").strip()
    email = form.get("email", "").strip().lower()
    password = form.get("password", "")
    errors = {}
    _check_name_email(db, errors, name, email)
    _check_password(errors, password, password)
    org = None
    try:
        org = db.execute("SELECT * FROM organizations WHERE id = ? AND active = TRUE",
                         (int(form.get("organization_id", "")),)).fetchone()
    except ValueError:
        pass
    if org is None:
        errors["organization_id"] = "Escolha a entidade."
    role = "admin" if org is not None and org["kind"] == "camara" and form.get("central") == "1" else "staff"
    if errors:
        raise ValidationError(errors)
    return create_user(db, name, email, password_hash_fn(password), role=role, organization_id=org["id"])


def _check_phone(errors, raw, required=False):
    """Devolve o telemóvel normalizado (+3519XXXXXXXX), None se vazio; regista o erro se for inválido."""
    import sms
    raw = (raw or "").strip()
    if not raw:
        if required:
            errors["phone"] = "Indique o telemóvel que vai receber as SMS."
        return None
    phone = sms.normalize_phone(raw)
    if phone is None:
        errors["phone"] = "Indique um telemóvel português válido (ex.: 912 345 678)."
    return phone


def update_profile(db, user, form, verify_fn):
    """Nome, email e telemóvel da própria conta. Mudar o email pede a palavra-passe atual."""
    name = form.get("name", "").strip()
    email = form.get("email", "").strip().lower()
    errors = {}
    if not 2 <= len(name) <= 100:
        errors["name"] = "Indique o nome (2 a 100 caracteres)."
    else:
        import profanity
        if profanity.find(name):
            errors["name"] = "Esse nome tem linguagem ofensiva. Indique o seu nome verdadeiro."
    if email != user["email"]:
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) or len(email) > 120:
            errors["email"] = "Indique um email válido."
        elif db.execute("SELECT 1 FROM users WHERE email = ? AND id <> ?", (email, user["id"])).fetchone():
            errors["email"] = "Já existe uma conta com este email."
        row = db.execute("SELECT password_hash FROM users WHERE id = ?", (user["id"],)).fetchone()
        if not verify_fn(row["password_hash"], form.get("current_for_email", "")):
            errors["current_for_email"] = "Para mudar o email, indique a palavra-passe atual."
    if errors:
        raise ValidationError(errors)
    with db:
        db.execute("UPDATE users SET name = ?, email = ? WHERE id = ?", (name, email, user["id"]))


# ------------------------------------------------------------------ Definições
def set_notification_prefs(db, user, form):
    """Definições › Notificações: comprovativo por email; para os técnicos, telemóvel e SMS."""
    errors = {}
    phone, sms_alerts = user["phone"], user["sms_alerts"]
    if user["role"] == "staff":
        phone = _check_phone(errors, form.get("phone"))
        sms_alerts = form.get("sms_alerts") == "1"
    if errors:
        raise ValidationError(errors)
    with db:
        db.execute("UPDATE users SET email_receipts = ?, phone = ?, sms_alerts = ? WHERE id = ?",
                   (form.get("email_receipts") == "1", phone, bool(sms_alerts), user["id"]))


def export_user_data(db, user):
    """RGPD (direito de acesso/portabilidade): tudo o que a plataforma guarda sobre a pessoa, em JSON."""
    def rows(sql, params):
        return [{k: (str(v) if v is not None and not isinstance(v, (int, float, bool, str)) else v)
                 for k, v in dict(r).items()} for r in db.execute(sql, params).fetchall()]
    uid = user["id"]
    profile = rows("""SELECT id, name, email, role, district, municipality, parish_code, created_at, last_login_at, alert_scope,
                             email_receipts, phone, sms_alerts FROM users WHERE id = ?""", (uid,))[0]
    return {
        "exportado_em": utcnow(),
        "plataforma": "Townly Up",
        "perfil": profile,
        "freguesias_acompanhadas": [r["parish_code"] for r in rows("SELECT parish_code FROM user_parishes WHERE user_id = ?", (uid,))],
        "ocorrencias": rows("""SELECT i.ticket, i.title, i.description, c.name AS categoria, i.status, i.latitude, i.longitude,
                                      i.parish_code, i.created_at, i.resolved_at, i.deleted_at
                                 FROM incidents i JOIN categories c ON c.id = i.category_id
                                WHERE i.author_id = ? ORDER BY i.id""", (uid,)),
        "confirmacoes": rows("""SELECT i.ticket, f.kind, f.created_at FROM confirmations f JOIN incidents i ON i.id = f.incident_id
                                 WHERE f.user_id = ? ORDER BY f.id""", (uid,)),
        "pontos": rows("SELECT reason, points, created_at FROM points_log WHERE user_id = ? ORDER BY id", (uid,)),
        "alertas": rows("SELECT kind, message, is_read, created_at FROM notifications WHERE user_id = ? ORDER BY id", (uid,)),
        "avisos": rows("SELECT kind, context, led_to_ban, created_at FROM user_warnings WHERE user_id = ? ORDER BY id", (uid,)),
        "avaliacoes": rows("""SELECT i.ticket, r.stars, r.comment, r.reopened, r.created_at FROM resolution_ratings r
                                JOIN incidents i ON i.id = r.incident_id WHERE r.user_id = ? ORDER BY r.id""", (uid,)),
    }


def delete_own_account(db, user, form, verify_fn):
    """RGPD (direito ao apagamento): o munícipe apaga a sua conta. Os dados pessoais são eliminados; as ocorrências
    ficam no histórico público do concelho, mas sem nome nem email (autor anónimo)."""
    if user["role"] != "citizen":
        raise ValidationError({"delete": "As contas das entidades são geridas pela conta principal."})
    row = db.execute("SELECT password_hash FROM users WHERE id = ?", (user["id"],)).fetchone()
    errors = {}
    if not verify_fn(row["password_hash"], form.get("password", "")):
        errors["delete_password"] = "A palavra-passe está errada."
    if form.get("confirm", "").strip().upper() != "APAGAR":
        errors["delete_confirm"] = "Escreva APAGAR para confirmar."
    if errors:
        raise ValidationError(errors)
    now = utcnow()
    with db:
        db.execute("DELETE FROM notifications WHERE user_id = ?", (user["id"],))
        db.execute("DELETE FROM user_parishes WHERE user_id = ?", (user["id"],))
        db.execute("""UPDATE users SET name = 'Conta eliminada', email = ?, password_hash = '!', has_password = FALSE,
                             phone = NULL, avatar = NULL, avatar_updated_at = NULL, active = FALSE, deleted_at = ?
                       WHERE id = ?""", (f"eliminada-{user['id']}@townlyup.invalid", now, user["id"]))


def delete_council_accounts(db):
    """Apaga TODAS as contas da Câmara/entidades (conta principal incluída). Como nas contas apagadas pelos munícipes,
    os dados pessoais desaparecem e o histórico das ocorrências fica, sem nome. O email fica livre para uma conta nova.
    Devolve a lista (id, email) das contas apagadas."""
    rows = db.execute("SELECT id, email FROM users WHERE role IN ('admin', 'staff') AND deleted_at IS NULL ORDER BY id").fetchall()
    now = utcnow()
    with db:
        for r in rows:
            for table in ("notifications", "user_parishes"):
                db.execute(f"DELETE FROM {table} WHERE user_id = ?", (r["id"],))
            db.execute("""UPDATE users SET name = 'Conta eliminada', email = ?, password_hash = '!', has_password = FALSE,
                                 role = 'citizen', organization_id = NULL, phone = NULL, sms_alerts = FALSE,
                                 avatar = NULL, avatar_type = NULL, avatar_updated_at = NULL, active = FALSE, deleted_at = ?
                           WHERE id = ?""", (f"eliminada-{r['id']}@townlyup.invalid", now, r["id"]))
    return [(r["id"], r["email"]) for r in rows]


def set_user_active(db, actor, user_id, active):
    if user_id == actor["id"]:
        raise ValidationError({"user": "Não pode desativar a sua própria conta."})
    target = db.execute("SELECT id, role FROM users WHERE id = ?", (user_id,)).fetchone()
    if target is None or target["role"] == "citizen":
        raise ValidationError({"user": "Conta não encontrada."})
    with db:
        db.execute("UPDATE users SET active = ? WHERE id = ?", (bool(active), user_id))


# ------------------------------------------------------------------ Fotografia de perfil
AVATAR_SIZE = 320


def set_avatar(db, user, file_storage, max_bytes):
    """Fotografia de perfil: aceita QUALQUER imagem que o Pillow consiga ler (JPEG, PNG, WebP, GIF, BMP, TIFF,
    AVIF, HEIC/HEIF se houver pillow-heif…). Usa só a 1.ª imagem (GIF animado), endireita-a (EXIF), põe fundo branco
    nas transparências, corta em quadrado e grava 320×320 em JPEG — sem metadados (localização, telemóvel)."""
    if not file_storage or not file_storage.filename:
        raise ValidationError({"avatar": "Escolha uma fotografia."})
    data = file_storage.read(max_bytes + 1)
    if not data:
        raise ValidationError({"avatar": "Fotografia: o ficheiro está vazio."})
    if len(data) > max_bytes:
        raise ValidationError({"avatar": f"Fotografia: excede o limite de {max_bytes // (1024 * 1024)} MB."})
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.seek(0)
            img.load()
            img = ImageOps.exif_transpose(img)
            if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
                rgba = img.convert("RGBA")
                bg = Image.new("RGB", rgba.size, (255, 255, 255))
                bg.paste(rgba, mask=rgba.getchannel("A"))
                img = bg
            img = ImageOps.fit(img.convert("RGB"), (AVATAR_SIZE, AVATAR_SIZE), Image.Resampling.LANCZOS, centering=(.5, .4))
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=86, optimize=True)
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError, ValueError):
        raise ValidationError({"avatar": "Não foi possível ler esta imagem. Experimente outra fotografia ou uma captura de ecrã."}) from None
    with db:
        db.execute("UPDATE users SET avatar = ?, avatar_type = 'image/jpeg', avatar_updated_at = ? WHERE id = ?",
                   (out.getvalue(), utcnow(), user["id"]))


def remove_avatar(db, user):
    with db:
        db.execute("UPDATE users SET avatar = NULL, avatar_type = NULL, avatar_updated_at = NULL WHERE id = ?", (user["id"],))


def change_password(db, user, form, verify_fn, hash_fn, require_current=True):
    """Muda a palavra-passe (ou define-a, se a conta ainda não tiver uma)."""
    errors = {}
    row = db.execute("SELECT password_hash FROM users WHERE id = ?", (user["id"],)).fetchone()
    if require_current and not verify_fn(row["password_hash"], form.get("current", "")):
        errors["current"] = "A palavra-passe atual está errada."
    new = form.get("password", "")
    _check_password(errors, new, form.get("confirm", ""))
    if errors:
        raise ValidationError(errors)
    with db:
        db.execute("UPDATE users SET password_hash = ?, has_password = TRUE WHERE id = ?", (hash_fn(new), user["id"]))


def organization_by(db, kind, value=None):
    """Entidade por tipo. Câmara: a do concelho indicado (sem concelho: a do Fundão, onde a plataforma começou)."""
    if kind == "camara":
        return db.execute("SELECT * FROM organizations WHERE kind = 'camara' AND municipality = ?",
                          (value or "Fundão",)).fetchone()
    column = "service" if kind == "servico" else "parish_code"
    return db.execute(f"SELECT * FROM organizations WHERE kind = ? AND {column} = ?", (kind, value)).fetchone()


def active_municipalities(db):
    """Concelhos ativos: os que têm a Câmara Municipal com pelo menos uma conta ativa (criada pela plataforma).
    A conta principal (plataforma) não conta: por razões técnicas fica ligada à 1.ª Câmara, mas não a ativa."""
    rows = db.execute(
        """SELECT DISTINCT o.municipality FROM organizations o JOIN users u ON u.organization_id = o.id
            WHERE o.kind = 'camara' AND o.active = TRUE AND u.role = 'staff' AND u.active = TRUE AND u.deleted_at IS NULL
              AND o.municipality IS NOT NULL""").fetchall()
    return {r[0] for r in rows}


def camara_name(municipality):
    """«Câmara Municipal do Fundão», «de Lisboa», «da Covilhã», «de Vila Real»… (nome oficial aproximado)."""
    import territory
    first = municipality.split()[0]
    art = {"Fundão": "do", "Porto": "do", "Seixal": "do", "Cartaxo": "do", "Bombarral": "do", "Crato": "do",
           "Entroncamento": "do", "Funchal": "do", "Fundao": "do", "Gavião": "do", "Lourinhã": "da", "Covilhã": "da",
           "Guarda": "da", "Amadora": "da", "Batalha": "da", "Figueira": "da", "Golegã": "da", "Lousã": "da", "Maia": "da",
           "Mealhada": "da", "Moita": "da", "Nazaré": "da", "Horta": "da", "Calheta": "da", "Ribeira": "da",
           "Trofa": "da", "Sertã": "da", "Pampilhosa": "da", "Mêda": "da", "Murtosa": "da", "Chamusca": "da",
           "Azambuja": "da", "Barquinha": "da", "Lagoa": "da", "Madalena": "da", "Praia": "da", "Povoação": "da"}.get(first, "de")
    return f"Câmara Municipal {art} {municipality}"


def camaras(db):
    """As Câmaras Municipais na plataforma, com o número de contas ativas de cada uma."""
    return db.execute(
        """SELECT o.id, o.name, o.municipality, o.active,
                  (SELECT COUNT(*) FROM users u WHERE u.organization_id = o.id AND u.role = 'staff' AND u.active = TRUE AND u.deleted_at IS NULL) AS accounts,
                  (SELECT COUNT(*) FROM incidents i WHERE i.municipality = o.municipality AND i.deleted_at IS NULL) AS incidents
             FROM organizations o WHERE o.kind = 'camara' ORDER BY o.municipality""").fetchall()


def create_camara_account(db, form, password_hash_fn):
    """A conta principal (plataforma) cria a conta da Câmara Municipal de um concelho: Distrito → Concelho.
    Cria a entidade «Câmara Municipal de …» se ainda não existir; a partir daí o concelho fica ativo."""
    import territory
    errors = {}
    district = form.get("district", "").strip()
    municipality = form.get("municipality", "").strip()
    name = " ".join(form.get("name", "").split())
    email = form.get("email", "").strip().lower()
    password = form.get("password", "")
    if district not in territory.DISTRICTS:
        errors["district"] = "Escolha o distrito."
    elif not territory.valid(district, municipality):
        errors["municipality"] = "Escolha o concelho."
    _check_name_email(db, errors, name, email)
    problem = password_problem(password)
    if problem:
        errors["password"] = problem
    phone = _check_phone(errors, form.get("phone", ""))
    if errors:
        raise ValidationError(errors)
    with db:
        org = organization_by(db, "camara", municipality)
        if org is None:
            org_id = db.execute("INSERT INTO organizations (name, kind, municipality, active) VALUES (?, 'camara', ?, TRUE) RETURNING id",
                                (camara_name(municipality), municipality)).fetchone()[0]
        else:
            org_id = org["id"]
            if not org["active"]:
                db.execute("UPDATE organizations SET active = TRUE WHERE id = ?", (org_id,))
    user_id = create_user(db, name, email, password_hash_fn(password), role="staff", organization_id=org_id,
                          district=district, municipality=municipality)
    if phone:
        with db:
            db.execute("UPDATE users SET phone = ? WHERE id = ?", (phone, user_id))
    return user_id, municipality


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
def _org_municipality(user):
    keys = user.keys() if hasattr(user, "keys") else []
    return (user["org_municipality"] if "org_municipality" in keys else None) or "Fundão"


def scope_clause(user):
    """Que ocorrências cada perfil trata: admin = todas; técnico = as da sua entidade
    (uma Junta vê também tudo o que acontece na sua freguesia)."""
    if user["role"] == "admin":
        return "", []
    if user["role"] == "staff":
        if user["org_kind"] == "junta":
            return "(i.organization_id = ? OR i.parish_code = ?)", [user["organization_id"], user["org_parish"]]
        if user["org_kind"] == "camara":                     # a Câmara vê tudo o que acontece no seu concelho
            return "(i.municipality = ? OR i.organization_id = ?)", [_org_municipality(user), user["organization_id"]]
        return "i.organization_id = ?", [user["organization_id"]]
    return "i.author_id = ?", [user["id"]]


def can_manage(user, incident):
    if user is None:
        return False
    if user["role"] == "admin":
        return True
    if user["role"] == "staff" and user["org_kind"] == "camara":
        return incident["municipality"] == _org_municipality(user) or incident["organization_id"] == user["organization_id"]
    if user["role"] == "staff":
        return incident["organization_id"] == user["organization_id"] or (
            user["org_kind"] == "junta" and incident["parish_code"] == user["org_parish"])
    return False


def can_view(user, incident):
    return user is not None and (incident["author_id"] == user["id"] or can_manage(user, incident))


def route_incident(db, category_id, parish_code, municipality="Fundão"):
    """Encaminhamento automático: serviço técnico da categoria → Junta da freguesia → Câmara.
    Fora do Fundão (onde só há a conta da Câmara) vai direto para a Câmara Municipal desse concelho."""
    if municipality != "Fundão":
        return organization_by(db, "camara", municipality)
    cat = db.execute("SELECT default_service FROM categories WHERE id = ?", (category_id,)).fetchone()
    org = None
    if cat and cat["default_service"]:
        org = organization_by(db, "servico", cat["default_service"])
    if org is None and parish_code:
        org = organization_by(db, "junta", parish_code)
    if org is None:
        org = organization_by(db, "camara", "Fundão")
    return org


def _notify(db, user_ids, incident_id, kind, message, now, exclude=()):
    for uid in dict.fromkeys(user_ids):
        if uid in exclude:
            continue
        db.execute("INSERT INTO notifications (user_id, incident_id, kind, message, created_at) VALUES (?, ?, ?, ?, ?)",
                   (uid, incident_id, kind, message, now))


# ------------------------------------------------------------------ Alertas da comunidade
def _community_filter(user):
    """Que ocorrências entram no feed de alertas de um munícipe."""
    where = ["i.deleted_at IS NULL", "i.author_id != ?", "i.created_at >= ?", "i.municipality = ?"]
    params = [user["id"], user["created_at"], user.get("municipality") or "Fundão"]
    if user.get("alert_scope", "all") != "all":
        where.append("""(i.parish_code = ? OR i.parish_code IN
                         (SELECT f.parish_code FROM user_parishes f WHERE f.user_id = ?))""")
        params += [user["parish_code"], user["id"]]
    return " AND ".join(where), params


def community_feed(db, user, limit=50):
    """Alertas de novos problemas na comunidade, calculados na hora (não se guarda uma cópia por pessoa).
    Cada item diz se é «na sua freguesia» (onde vive ou que segue) e se ainda não foi visto."""
    if user["role"] != "citizen":
        return []
    where, params = _community_filter(user)
    rows = db.execute(
        f"""SELECT i.id AS incident_id, i.ticket, i.created_at, i.parish_code, p.short_name AS parish, c.name AS category,
                   (i.parish_code = ? OR i.parish_code IN (SELECT f.parish_code FROM user_parishes f WHERE f.user_id = ?)) AS mine
              FROM incidents i JOIN categories c ON c.id = i.category_id LEFT JOIN parishes p ON p.code = i.parish_code
             WHERE {where} ORDER BY i.created_at DESC, i.id DESC LIMIT ?""",
        [user["parish_code"], user["id"], *params, limit]).fetchall()
    seen = user.get("alerts_seen_at")
    feed = []
    for r in rows:
        where_txt = r["parish"] or "no concelho"
        msg = (f"Novo problema na sua freguesia ({where_txt}): {r['category']} · {r['ticket']}." if r["mine"]
               else f"Novo problema em {where_txt}: {r['category']} · {r['ticket']}.")
        feed.append({"incident_id": r["incident_id"], "kind": "zone", "message": msg, "created_at": r["created_at"],
                     "is_read": bool(seen and r["created_at"] <= seen), "ticket": r["ticket"]})
    return feed


def community_unread(db, user):
    if user["role"] != "citizen":
        return 0
    where, params = _community_filter(user)
    if user.get("alerts_seen_at"):
        where += " AND i.created_at > ?"
        params.append(user["alerts_seen_at"])
    return db.execute(f"SELECT COUNT(*) FROM (SELECT 1 FROM incidents i WHERE {where} LIMIT 100) t", params).fetchone()[0]


def mark_alerts_seen(db, user):
    with db:
        db.execute("UPDATE notifications SET is_read = TRUE WHERE user_id = ?", (user["id"],))
        db.execute("UPDATE users SET alerts_seen_at = ? WHERE id = ?", (utcnow(), user["id"]))


def set_alert_scope(db, user, scope):
    if scope not in ("all", "followed"):
        raise ValidationError({"scope": "Opção inválida."})
    with db:
        db.execute("UPDATE users SET alert_scope = ? WHERE id = ?", (scope, user["id"]))


# ------------------------------------------------------------------ Apagar (pelo autor)
DELETABLE_STATUSES = (REGISTADA, EM_ANALISE)


def can_delete(user, incident):
    return (user is not None and user["role"] == "citizen" and incident["author_id"] == user["id"]
            and incident["status"] in DELETABLE_STATUSES)


def delete_incident(db, user, incident):
    """O autor apaga a ocorrência enquanto a entidade ainda não a está a tratar.
    Deixa de aparecer em todo o lado, as fotografias são eliminadas e os pontos ligados a ela retirados.
    O registo e o histórico ficam (RF10/RF11: o histórico é imutável e as ocorrências não se apagam fisicamente)."""
    if not can_delete(user, incident):
        raise ValidationError({"delete": "Esta ocorrência já está a ser tratada e não pode ser apagada."})
    now = utcnow()
    with db:
        db.execute("UPDATE incidents SET deleted_at = ?, updated_at = ? WHERE id = ?", (now, now, incident["id"]))
        db.execute("""INSERT INTO status_history (incident_id, actor_id, previous_status, new_status, note, changed_at)
                      VALUES (?, ?, ?, ?, ?, ?)""",
                   (incident["id"], user["id"], incident["status"], incident["status"], "Ocorrência apagada pelo autor.", now))
        db.execute("DELETE FROM incident_images WHERE incident_id = ?", (incident["id"],))
        db.execute("DELETE FROM confirmations WHERE incident_id = ?", (incident["id"],))
        db.execute("DELETE FROM points_log WHERE incident_id = ?", (incident["id"],))
        db.execute("DELETE FROM notifications WHERE incident_id = ?", (incident["id"],))


# ------------------------------------------------------------------ Moderação
# ------------------------------------------------------------------ Segurança: avisos e banimento
# Regra da plataforma: 1.º ato (palavrões/insultos ou imagem imprópria/sem relação) → conteúdo bloqueado e
# aviso formal registado; 2.º ato → a conta é banida automaticamente. A Câmara vê tudo na Supervisão
# e pode levantar o banimento (por exemplo, se a análise automática se enganou).
BAN_AFTER = 2
WARNING_KIND_LABELS = {"linguagem": "Linguagem ofensiva", "imagem": "Imagem imprópria ou sem relação"}


def record_offence(db, user_id, kind, context, detail):
    """Regista o ato e aplica a regra. Devolve 'aviso' ou 'banida'."""
    now = utcnow()
    with db:
        previous = db.execute("SELECT COUNT(*) FROM user_warnings WHERE user_id = ?", (user_id,)).fetchone()[0]
        banned = previous + 1 >= BAN_AFTER
        db.execute("""INSERT INTO user_warnings (user_id, kind, context, detail, led_to_ban, created_at)
                      VALUES (?, ?, ?, ?, ?, ?)""", (user_id, kind, context[:60], (detail or "")[:300] or None, banned, now))
        if banned:
            db.execute("UPDATE users SET active = FALSE, banned_at = ?, ban_reason = ? WHERE id = ?",
                       (now, f"{WARNING_KIND_LABELS[kind]} repetida ({context})."[:300], user_id))
    return "banida" if banned else "aviso"


def offence_message(outcome, what):
    if outcome == "banida":
        return (f"{what} Como já tinha recebido um aviso, a conta foi banida automaticamente, "
                "conforme as regras da comunidade.")
    return (f"{what} Isto conta como um aviso formal: se voltar a acontecer, a conta é banida automaticamente.")


def check_language(db, user, fields, context):
    """Bloqueia texto com palavrões/insultos. Com conta: regista o ato (aviso → banimento)."""
    import profanity
    hit = profanity.check(fields)
    if hit is None:
        return
    field, word = hit
    what = "O texto tem linguagem ofensiva e não foi publicado."
    if user is None:
        raise ValidationError({field: "Esse texto tem linguagem ofensiva. Escreva de forma respeitosa."})
    outcome = record_offence(db, user["id"], "linguagem", context, f"«{word}» em {field}")
    raise ValidationError({field: offence_message(outcome, what)}, banned=outcome == "banida",
                          warned=outcome == "aviso")


def warnings_for(db, user_id):
    return db.execute("SELECT * FROM user_warnings WHERE user_id = ? ORDER BY created_at DESC", (user_id,)).fetchall()


def unseen_warning(db, user_id):
    return db.execute("SELECT * FROM user_warnings WHERE user_id = ? AND seen_at IS NULL ORDER BY created_at DESC LIMIT 1",
                      (user_id,)).fetchone()


def mark_warnings_seen(db, user_id):
    with db:
        db.execute("UPDATE user_warnings SET seen_at = ? WHERE user_id = ? AND seen_at IS NULL", (utcnow(), user_id))


def safety_overview(db):
    """Para a Supervisão: últimos avisos e contas banidas."""
    warnings = db.execute(
        """SELECT w.*, u.name, u.email, u.municipality FROM user_warnings w JOIN users u ON u.id = w.user_id
            ORDER BY w.created_at DESC LIMIT 30""").fetchall()
    banned = db.execute(
        """SELECT id, name, email, municipality, banned_at, ban_reason FROM users
            WHERE banned_at IS NOT NULL AND active = FALSE AND deleted_at IS NULL ORDER BY banned_at DESC""").fetchall()
    return {"warnings": warnings, "banned": banned}


def lift_ban(db, user_id):
    """A Câmara levanta o banimento: a conta volta e os avisos anteriores deixam de contar."""
    with db:
        row = db.execute("SELECT id FROM users WHERE id = ? AND banned_at IS NOT NULL", (user_id,)).fetchone()
        if row is None:
            return False
        db.execute("UPDATE users SET active = TRUE, banned_at = NULL, ban_reason = NULL WHERE id = ?", (user_id,))
        db.execute("DELETE FROM user_warnings WHERE user_id = ?", (user_id,))
    return True


def _moderate(db, author, images, category_name):
    """Recusa fotografias impróprias ou sem relação com o espaço público («imagens desnecessárias»).
    Cada recusa fica registada e aplica a regra: 1.º ato aviso, 2.º ato banimento."""
    import moderation
    try:
        moderation.review(images, category_name)
    except moderation.ModerationError:
        raise ValidationError({"photos": "Não foi possível verificar as fotografias neste momento. "
                                         "Tente novamente daqui a pouco."}) from None
    except moderation.Rejected as rej:
        with db:
            db.execute("INSERT INTO moderation_events (user_id, reason, description, created_at) VALUES (?, ?, ?, ?)",
                       (author["id"], rej.reason, rej.description[:300], utcnow()))
        outcome = record_offence(db, author["id"], "imagem", "fotografia de ocorrência", f"{rej.reason}: {rej.description}")
        n = f"A fotografia {rej.index + 1}" if len(images) > 1 else "A fotografia"
        what = f"{n} foi recusada: {rej}."
        if not rej.inappropriate:
            what += " Fotografe só o problema na rua (buraco, lixo, iluminação...)."
        raise ValidationError({"photos": offence_message(outcome, what)}, banned=outcome == "banida",
                              warned=outcome == "aviso") from None


# ------------------------------------------------------------------ Ocorrências
def can_report(user):
    """Só se comunica no concelho da conta, e só se a Câmara desse concelho já aderiu.
    Contas antigas sem concelho são do Fundão (a plataforma começou lá)."""
    import territory
    municipality = (user["municipality"] if "municipality" in user.keys() else None) or territory.DEFAULT
    return territory.is_active(municipality)


def point_in_municipality(municipality, lat, lng):
    """True/False se o ponto fica dentro do contorno oficial do concelho; None se o contorno não estiver disponível."""
    import territory
    try:
        from flask import current_app
        cache = current_app.config.get("GEO_CACHE_DIR") or current_app.instance_path
    except Exception:
        cache = None
    try:
        g = territory.geometry(municipality, cache)["outline"]["geometry"]
    except Exception:
        return None
    polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
    for poly in polys:
        if geo._in_ring(poly[0], lng, lat) and not any(geo._in_ring(h, lng, lat) for h in poly[1:]):
            return True
    return False


def create_incident(db, upload_dir, author, form, files, max_bytes):
    """RF03–RF06: valida, grava imagens, cria o ticket, encaminha e avisa quem deve receber."""
    if author["role"] != "citizen":
        raise PermissionError("Apenas munícipes podem registar ocorrências.")
    municipality = author["municipality"] if "municipality" in author.keys() else None
    if not can_report(author):
        raise ValidationError({"location": f"Ainda não disponível no seu concelho ({municipality}). "
                                           "A Câmara Municipal ainda não aderiu ao Townly Up."})

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

    subcategory = None
    if category is not None and form.get("subcategory_id"):
        try:
            subcategory = db.execute("SELECT id, name FROM subcategories WHERE id = ? AND category_id = ? AND active = TRUE",
                                     (int(form.get("subcategory_id")), category["id"])).fetchone()
        except ValueError:
            pass
        if subcategory is None:
            errors["subcategory_id"] = "Escolha um tipo de problema desta categoria."
    address = " ".join(form.get("address", "").split())[:200] or None   # morada da rua (OpenStreetMap), opcional

    # Comunicar em 3 passos: o título é opcional (usa-se o subtipo ou o nome da categoria).
    title = form.get("title", "").strip()
    if not title and category is not None:
        title = subcategory["name"] if subcategory is not None and subcategory["name"] != "Outro" else category["name"]
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
    elif (municipality or "Fundão") == "Fundão":
        if not (BOUNDS["lat_min"] <= lat <= BOUNDS["lat_max"] and BOUNDS["lng_min"] <= lng <= BOUNDS["lng_max"]):
            errors["location"] = "A localização tem de ficar no concelho do Fundão."
        else:
            parish_code = geo.parish_at(lat, lng)
            if parish_code is None and geo.available():
                errors["location"] = "Esse ponto fica fora do concelho do Fundão."
    else:
        inside = point_in_municipality(municipality, lat, lng)
        if inside is False:
            errors["location"] = f"Esse ponto fica fora do concelho de {municipality}."

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

    # 0) Linguagem: palavrões ou insultos bloqueiam o envio (aviso; à 2.ª a conta é banida).
    check_language(db, author, {"title": form.get("title", ""), "description": description}, "ocorrência")

    # 1) Repetidos: compara local, categoria, texto e «impressão digital» das fotografias.
    import similarity
    hashes = similarity.hashes_for(images)
    dismissed = set()
    for value in (form.getlist("not_duplicate") if hasattr(form, "getlist") else []):
        if str(value).isdigit():
            dismissed.add(int(value))
    similar = similarity.find_similar(db, lat, lng, category["id"], f"{title} {description}", hashes)
    pending = [s for s in similar if s["id"] not in dismissed]
    if pending:
        raise ValidationError({"duplicate": "Este problema parece já ter sido comunicado. Confirme se é o mesmo."},
                              similar=pending)
    similar_to = similar[0]["id"] if similar else None

    # 2) Moderação automática das fotografias (conteúdo impróprio ou sem relação com o espaço público).
    _moderate(db, author, images, category["name"])

    municipality = municipality or "Fundão"
    org = route_incident(db, category["id"], parish_code, municipality)
    if org is None:
        raise ValidationError({"location": f"Ainda não disponível no seu concelho ({municipality})."})
    saved = _store_images(upload_dir, images)
    now = utcnow()
    with db:  # transação: ou grava tudo, ou não grava nada
        incident_id = db.execute(
            """INSERT INTO incidents (author_id, category_id, subcategory_id, parish_code, organization_id, title, description,
                   latitude, longitude, address, status, created_at, updated_at, similar_to, municipality)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id""",
            (author["id"], category["id"], subcategory["id"] if subcategory is not None else None, parish_code, org["id"],
             title, description, round(lat, 6), round(lng, 6), address, REGISTADA, now, now, similar_to, municipality),
        ).fetchone()[0]
        ticket = f"TU-{now[:4]}-{incident_id:04d}"  # RF06: TU-AAAA-NNNN
        db.execute("UPDATE incidents SET ticket = ? WHERE id = ?", (ticket, incident_id))
        db.executemany(
            """INSERT INTO incident_images (incident_id, stored_filename, content_type, data, phash, kind, uploaded_at)
               VALUES (?, ?, ?, ?, ?, 'report', ?)""",
            [(incident_id, name, ctype, data, h, now) for (name, ctype, data), h in zip(saved, hashes)],
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

        # Recetores 2: a comunidade (como no Waze) — o alerta aparece no feed de cada munícipe (community_feed),
        # calculado na hora: não se gravam milhares de cópias por ocorrência.
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
    SELECT i.*, c.name AS category, sc.name AS subcategory, u.name AS author_name, a.name AS assigned_name,
           p.short_name AS parish, o.name AS organization, o.kind AS organization_kind,
           (SELECT im.id FROM incident_images im
             WHERE im.incident_id = i.id AND im.kind = 'report' ORDER BY im.id LIMIT 1) AS thumb_id,
           (SELECT COUNT(*) FROM confirmations cf WHERE cf.incident_id = i.id AND cf.kind = 'seen') AS seen_count,
           (SELECT COUNT(*) FROM confirmations cf WHERE cf.incident_id = i.id AND cf.kind = 'fixed') AS fixed_count
      FROM incidents i
      JOIN categories c ON c.id = i.category_id
      LEFT JOIN subcategories sc ON sc.id = i.subcategory_id
      JOIN users u ON u.id = i.author_id
      LEFT JOIN users a ON a.id = i.assigned_to
      LEFT JOIN parishes p ON p.code = i.parish_code
      LEFT JOIN organizations o ON o.id = i.organization_id
"""


def get_incident(db, incident_id):
    return db.execute(INCIDENT_SELECT + " WHERE i.id = ? AND i.deleted_at IS NULL", (incident_id,)).fetchone()


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
                     user=None, parish="", limit=None, offset=0, count=False):
    """RF08: pesquisa e filtros (sempre com parâmetros — nunca concatenar texto do utilizador)."""
    where, params = ["i.deleted_at IS NULL"], []
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
    if count:   # só o total (para a paginação)
        return db.execute("SELECT COUNT(*) FROM incidents i" + (" WHERE " + " AND ".join(where) if where else ""), params).fetchone()[0]
    sql = INCIDENT_SELECT + (" WHERE " + " AND ".join(where) if where else "")
    if sort == "confirmadas":  # as mais confirmadas pela comunidade primeiro
        sql += " ORDER BY seen_count DESC, i.created_at DESC, i.id DESC"
    else:
        sql += " ORDER BY i.created_at DESC, i.id DESC"
    if limit:
        sql += f" LIMIT {int(limit)} OFFSET {max(0, int(offset))}"
    return db.execute(sql, params).fetchall()


def status_counts(db, author_id=None, user=None):
    where, params = ["i.deleted_at IS NULL"], []
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
    join_filter = " AND i.deleted_at IS NULL" + (f" AND {clause}" if clause else "")
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
              FROM parishes p LEFT JOIN incidents i ON i.parish_code = p.code AND i.deleted_at IS NULL
             GROUP BY p.code, p.name, p.short_name, p.landmark, p.image_credit
             ORDER BY p.short_name""",
        (*ACTIVE_STATUSES, RESOLVIDA),
    ).fetchall()


# ------------------------------------------------------------------ Subcategorias
def subcategories_by_category(db):
    """{category_id: [{id, name}, …]} para o passo 1 de «Comunicar»."""
    out = {}
    for r in db.execute("SELECT id, category_id, name FROM subcategories WHERE active = TRUE ORDER BY category_id, position, id"):
        out.setdefault(r["category_id"], []).append({"id": r["id"], "name": r["name"]})
    return out


# ------------------------------------------------------------------ Avaliação da resolução (fecha o ciclo)
def latest_rating(db, incident):
    """Avaliação feita depois da última resolução (None se ainda não avaliou esta resolução)."""
    if incident["status"] != RESOLVIDA or not incident["resolved_at"]:
        return None
    return db.execute("SELECT * FROM resolution_ratings WHERE incident_id = ? AND created_at >= ? ORDER BY id DESC LIMIT 1",
                      (incident["id"], incident["resolved_at"])).fetchone()


def can_rate(user, incident):
    return (user is not None and user["role"] == "citizen" and incident["author_id"] == user["id"]
            and incident["status"] == RESOLVIDA and incident["deleted_at"] is None)


def rate_resolution(db, user, incident, form):
    """O autor avalia a resolução (1–5 estrelas). Se disser que o problema continua, a ocorrência é reaberta
    («Em tratamento») e a entidade é avisada — fica tudo no histórico."""
    if not can_rate(user, incident):
        raise PermissionError("Só o autor pode avaliar uma ocorrência resolvida.")
    if latest_rating(db, incident) is not None:
        raise ValidationError({"stars": "Já avaliou esta resolução."})
    try:
        stars = int(form.get("stars", ""))
    except ValueError:
        stars = 0
    errors = {}
    if not 1 <= stars <= 5:
        errors["stars"] = "Escolha de 1 a 5 estrelas."
    comment = " ".join(form.get("comment", "").split())[:500] or None
    reopen = form.get("reopen") == "1"
    if reopen and not comment:
        errors["comment"] = "Diga-nos o que continua por resolver."
    if errors:
        raise ValidationError(errors)
    check_language(db, user, {"comment": comment or ""}, "avaliação")
    now = utcnow()
    with db:
        db.execute("""INSERT INTO resolution_ratings (incident_id, user_id, stars, comment, reopened, created_at)
                      VALUES (?, ?, ?, ?, ?, ?)""", (incident["id"], user["id"], stars, comment, reopen, now))
        if reopen:
            db.execute("UPDATE incidents SET status = ?, resolved_at = NULL, updated_at = ? WHERE id = ?",
                       (EM_TRATAMENTO, now, incident["id"]))
            db.execute("""INSERT INTO status_history (incident_id, actor_id, previous_status, new_status, note, changed_at)
                          VALUES (?, ?, ?, ?, ?, ?)""",
                       (incident["id"], user["id"], RESOLVIDA, EM_TRATAMENTO, f"Reaberta pelo munícipe: {comment}", now))
            staff = [r["id"] for r in db.execute(
                "SELECT id FROM users WHERE role = 'staff' AND active = TRUE AND organization_id = ?",
                (incident["organization_id"],)).fetchall()]
            _notify(db, staff, incident["id"], "assigned",
                    f"{incident['ticket']} foi reaberta pelo munícipe: o problema continua.", now)
    return reopen


def satisfaction(db, organization_id=None):
    """Média das estrelas (e nº de avaliações), no total ou de uma entidade."""
    sql = "SELECT AVG(r.stars) AS avg, COUNT(*) AS n FROM resolution_ratings r JOIN incidents i ON i.id = r.incident_id"
    params = []
    if organization_id is not None:
        sql += " WHERE i.organization_id = ?"
        params.append(organization_id)
    row = db.execute(sql, params).fetchone()
    return {"avg": round(float(row["avg"]), 1) if row["avg"] is not None else None, "n": row["n"]}


# ------------------------------------------------------------------ «Antes e depois»
def before_after(db, limit=24):
    """Ocorrências resolvidas com fotografia do problema e da resolução (públicas nesta página)."""
    return db.execute(INCIDENT_SELECT + """
        WHERE i.deleted_at IS NULL AND i.status = 'Resolvida'
          AND EXISTS (SELECT 1 FROM incident_images r WHERE r.incident_id = i.id AND r.kind = 'resolution')
          AND EXISTS (SELECT 1 FROM incident_images b WHERE b.incident_id = i.id AND b.kind = 'report')
        ORDER BY i.resolved_at DESC LIMIT ?""", (limit,)).fetchall()
