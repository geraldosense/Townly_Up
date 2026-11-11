"""Comunidade estilo Waze: confirmações, pontos, níveis, proximidade e mapa público.

Ideia: quanto mais munícipes confirmam um problema, mais visível ele fica
para a central. Quem participa ganha pontos e sobe de nível.
"""
import math
import sqlite3
from datetime import datetime, timedelta, timezone

import services as sv  # usado só dentro das funções (evita importação circular)

# ------------------------------------------------------------------ Freguesias
# 23 freguesias do concelho do Fundão (Lei n.º 11-A/2013).
PARISHES = (
    "Alcaide", "Alcaria", "Alcongosta", "Alpedrinha", "Barroca", "Bogas de Cima",
    "Capinha", "Castelejo", "Castelo Novo", "Enxames", "Fatela",
    "Fundão, Valverde, Donas, Aldeia de Joanes e Aldeia Nova do Cabo",
    "Janeiro de Cima e Bogas de Baixo", "Lavacolhos", "Orca", "Pêro Viseu",
    "Póvoa de Atalaia e Atalaia do Campo", "Silvares", "Soalheira", "Souto da Casa",
    "Telhado", "Três Povos", "Vale de Prazeres e Mata da Rainha",
)

# ------------------------------------------------------------------ Categorias
# Ícone (Bootstrap Icons) e cor de cada categoria — usados no mapa e no formulário.
CATEGORY_STYLE = {
    "Vias e pavimentos": ("cone-striped", "#e8590c"),
    "Iluminação pública": ("lightbulb", "#f2b705"),
    "Espaços verdes": ("tree", "#2f9e44"),
    "Limpeza urbana": ("trash3", "#0c8599"),
    "Mobiliário urbano": ("bricks", "#7048e8"),
    "Sinalização e trânsito": ("sign-stop", "#e03131"),
    "Água e saneamento": ("droplet", "#1971c2"),
    "Outro": ("three-dots", "#5c677d"),
}


def category_style(name):
    icon, color = CATEGORY_STYLE.get(name, ("geo-alt", "#5c677d"))
    return {"icon": icon, "color": color}


# ------------------------------------------------------------------ Pontos e níveis
POINTS = {
    "report": 10,        # comunicar uma ocorrência
    "confirm_seen": 2,   # «Eu também vi»
    "confirm_fixed": 1,  # «Já foi resolvido?»
    "confirmed_by": 1,   # alguém confirmou a minha ocorrência
    "resolved": 15,      # a minha ocorrência foi resolvida
}

POINT_REASONS = {
    "report": "Comunicou uma ocorrência",
    "confirm_seen": "Confirmou «Eu também vi»",
    "confirm_fixed": "Indicou que já foi resolvido",
    "confirmed_by": "A sua ocorrência foi confirmada",
    "resolved": "A sua ocorrência foi resolvida",
}

LEVELS = (
    (0, "Olho novo", "eye"),
    (30, "Olho atento", "eye-fill"),
    (100, "Vigilante do bairro", "binoculars"),
    (250, "Guardião da cidade", "shield-check"),
    (500, "Embaixador Townly", "trophy"),
)

NEARBY_RADIUS_M = 50


def award(db, user_id, reason, incident_id=None, now=None):
    """Regista pontos. Não faz commit: corre dentro da transação de quem chama."""
    db.execute(
        "INSERT INTO points_log (user_id, incident_id, reason, points, created_at) VALUES (?, ?, ?, ?, ?)",
        (user_id, incident_id, reason, POINTS[reason], now or sv.utcnow()),
    )
    return POINTS[reason]


def user_points(db, user_id):
    return db.execute(
        "SELECT COALESCE(SUM(points), 0) FROM points_log WHERE user_id = ?", (user_id,)
    ).fetchone()[0]


def level_for(points):
    """Devolve o nível atual e o progresso até ao seguinte."""
    current = LEVELS[0]
    nxt = None
    for i, level in enumerate(LEVELS):
        if points >= level[0]:
            current = level
            nxt = LEVELS[i + 1] if i + 1 < len(LEVELS) else None
    progress = 100
    if nxt:
        progress = round((points - current[0]) / (nxt[0] - current[0]) * 100)
    return {
        "points": points, "name": current[1], "icon": current[2], "number": LEVELS.index(current) + 1,
        "next_name": nxt[1] if nxt else None, "next_min": nxt[0] if nxt else None,
        "missing": (nxt[0] - points) if nxt else 0, "progress": progress,
    }


def recent_points(db, user_id, limit=5):
    rows = db.execute(
        """SELECT p.*, i.ticket FROM points_log p LEFT JOIN incidents i ON i.id = p.incident_id
            WHERE p.user_id = ? ORDER BY p.id DESC LIMIT ?""",
        (user_id, limit),
    ).fetchall()
    return [dict(r, label=POINT_REASONS.get(r["reason"], r["reason"])) for r in rows]


# ------------------------------------------------------------------ Confirmações
def confirmation_counts(db, incident_id):
    rows = db.execute(
        "SELECT kind, COUNT(*) AS n FROM confirmations WHERE incident_id = ? GROUP BY kind", (incident_id,)
    ).fetchall()
    counts = {"seen": 0, "fixed": 0}
    counts.update({r["kind"]: r["n"] for r in rows})
    return counts


def user_confirmations(db, user_id):
    """{incident_id: {'seen', 'fixed'}} com o que este utilizador já confirmou."""
    result = {}
    for r in db.execute("SELECT incident_id, kind FROM confirmations WHERE user_id = ?", (user_id,)):
        result.setdefault(r["incident_id"], set()).add(r["kind"])
    return result


def confirm(db, user, incident_id, kind):
    """«Eu também vi» (seen) ou «Já foi resolvido?» (fixed)."""
    if kind not in ("seen", "fixed"):
        raise sv.ValidationError({"kind": "Tipo de confirmação inválido."})
    if user["role"] != "citizen":
        raise PermissionError("Apenas munícipes podem confirmar ocorrências.")
    incident = db.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,)).fetchone()
    if incident is None:
        raise LookupError("Ocorrência não encontrada.")
    if incident["status"] not in sv.ACTIVE_STATUSES:
        raise sv.ValidationError({"kind": "Esta ocorrência já está encerrada."})
    if incident["author_id"] == user["id"]:
        raise sv.ValidationError({"kind": "Não pode confirmar a sua própria ocorrência."})

    now = sv.utcnow()
    try:
        with db:
            db.execute(
                "INSERT INTO confirmations (incident_id, user_id, kind, created_at) VALUES (?, ?, ?, ?)",
                (incident_id, user["id"], kind, now),
            )
            earned = award(db, user["id"], "confirm_" + kind, incident_id, now)
            if kind == "seen":
                award(db, incident["author_id"], "confirmed_by", incident_id, now)
    except sqlite3.IntegrityError:
        raise sv.ValidationError({"kind": "Já tinha confirmado esta ocorrência."}) from None
    return {"points": earned, "counts": confirmation_counts(db, incident_id)}


# ------------------------------------------------------------------ Proximidade
def distance_m(lat1, lng1, lat2, lng2):
    """Distância em metros entre dois pontos (fórmula de Haversine)."""
    r = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def nearby(db, lat, lng, radius_m=NEARBY_RADIUS_M, category_id=None, exclude_id=None):
    """RF14: ocorrências ativas a menos de `radius_m` metros (possíveis duplicados)."""
    # Pré-filtro rápido por "caixa" de coordenadas; depois distância exata.
    dlat = radius_m / 111_320
    dlng = radius_m / (111_320 * max(math.cos(math.radians(lat)), 0.01))
    sql = sv.INCIDENT_SELECT + f""" WHERE i.status IN ({','.join('?' * len(sv.ACTIVE_STATUSES))})
        AND i.latitude BETWEEN ? AND ? AND i.longitude BETWEEN ? AND ?"""
    params = [*sv.ACTIVE_STATUSES, lat - dlat, lat + dlat, lng - dlng, lng + dlng]
    if category_id:
        sql += " AND i.category_id = ?"
        params.append(category_id)
    if exclude_id:
        sql += " AND i.id != ?"
        params.append(exclude_id)
    result = []
    for row in db.execute(sql, params):
        d = distance_m(lat, lng, row["latitude"], row["longitude"])
        if d <= radius_m:
            result.append({**public_incident(row), "distance_m": round(d)})
    return sorted(result, key=lambda x: x["distance_m"])


# ------------------------------------------------------------------ Mapa público
def public_incident(row):
    """Só dados seguros para o público: sem nomes, emails, títulos livres nem fotografias."""
    style = category_style(row["category"])
    return {
        "id": row["id"], "ticket": row["ticket"], "category": row["category"],
        "category_id": row["category_id"], "icon": style["icon"], "color": style["color"],
        "status": row["status"], "slug": sv.status_slug(row["status"]),
        "lat": row["latitude"], "lng": row["longitude"], "created_at": row["created_at"],
        "seen": row["seen_count"], "fixed": row["fixed_count"],
    }


def public_map(db, days_resolved=30):
    """Ocorrências ativas + resolvidas nos últimos `days_resolved` dias.
    Rejeitadas e duplicadas não aparecem no mapa público."""
    since = (datetime.now(timezone.utc) - timedelta(days=days_resolved)).strftime("%Y-%m-%d %H:%M:%S")
    rows = db.execute(
        sv.INCIDENT_SELECT + f""" WHERE i.status IN ({','.join('?' * len(sv.ACTIVE_STATUSES))})
            OR (i.status = ? AND i.resolved_at >= ?) ORDER BY i.created_at DESC""",
        (*sv.ACTIVE_STATUSES, sv.RESOLVIDA, since),
    ).fetchall()
    return [public_incident(r) for r in rows]


# ------------------------------------------------------------------ Ranking
def short_name(name):
    """'Ana Martins' -> 'Ana M.' (privacidade no ranking)."""
    parts = (name or "").split()
    if not parts:
        return "Munícipe"
    return parts[0] + (f" {parts[-1][0]}." if len(parts) > 1 else "")


def ranking_people(db, limit=10):
    rows = db.execute(
        """SELECT u.id, u.name, u.parish, COALESCE(SUM(p.points), 0) AS points
             FROM users u LEFT JOIN points_log p ON p.user_id = u.id
            WHERE u.role = 'citizen'
            GROUP BY u.id HAVING points > 0
            ORDER BY points DESC, u.id LIMIT ?""",
        (limit,),
    ).fetchall()
    return [{"id": r["id"], "name": short_name(r["name"]), "parish": r["parish"],
             "points": r["points"], "level": level_for(r["points"])} for r in rows]


def ranking_parishes(db):
    return db.execute(
        """SELECT u.parish, COUNT(DISTINCT u.id) AS members, COALESCE(SUM(p.points), 0) AS points
             FROM users u LEFT JOIN points_log p ON p.user_id = u.id
            WHERE u.role = 'citizen' AND u.parish IS NOT NULL AND u.parish != ''
            GROUP BY u.parish HAVING points > 0
            ORDER BY points DESC, u.parish"""
    ).fetchall()
