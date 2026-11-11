"""API JSON v1 — usada pelo mapa vivo e base das futuras apps móveis.

Respostas sempre em JSON. Nesta versão a autenticação é a sessão do site
(cookie) + token CSRF no cabeçalho X-CSRF-Token. Para as apps da Play Store /
App Store, o passo seguinte será acrescentar autenticação por token.

  GET  /api/v1/categories                 categorias com ícone e cor
  GET  /api/v1/map                        ocorrências do mapa público
  GET  /api/v1/incidents/nearby?lat&lng   ocorrências ativas a menos de 50 m
  POST /api/v1/incidents/<id>/confirm     {"kind": "seen" | "fixed"}
  GET  /api/v1/me                         pontos e nível do utilizador
"""
from flask import Blueprint, abort, g, jsonify, request

import community as cm
import services as sv
from db import get_db

bp = Blueprint("api", __name__, url_prefix="/api/v1")


def error(message, status):
    return jsonify({"error": message}), status


def require_citizen():
    if g.user is None:
        abort(401)
    if g.user["role"] != "citizen":
        abort(403)


@bp.get("/categories")
def categories():
    rows = get_db().execute("SELECT id, name FROM categories WHERE active = 1 ORDER BY id").fetchall()
    return jsonify([{"id": r["id"], "name": r["name"], **cm.category_style(r["name"])} for r in rows])


@bp.get("/map")
def public_map():
    db = get_db()
    items = cm.public_map(db)
    confirmed, own = {}, set()
    if g.user:
        confirmed = cm.user_confirmations(db, g.user["id"])
        own = {r[0] for r in db.execute("SELECT id FROM incidents WHERE author_id = ?", (g.user["id"],))}
    for item in items:
        item["confirmed"] = sorted(confirmed.get(item["id"], ()))
        item["mine"] = item["id"] in own
    return jsonify({"incidents": items, "statuses": list(sv.ALL_STATUSES)})


@bp.get("/incidents/nearby")
def nearby():
    lat = sv.parse_coordinate(request.args.get("lat"))
    lng = sv.parse_coordinate(request.args.get("lng"))
    if lat is None or lng is None:
        return error("Indique lat e lng.", 400)
    category_id = request.args.get("category_id", type=int)
    return jsonify({"radius_m": cm.NEARBY_RADIUS_M,
                    "incidents": cm.nearby(get_db(), lat, lng, category_id=category_id)})


@bp.post("/incidents/<int:incident_id>/confirm")
def confirm(incident_id):
    require_citizen()
    data = request.get_json(silent=True) or request.form
    try:
        result = cm.confirm(get_db(), g.user, incident_id, data.get("kind", ""))
    except LookupError as exc:
        return error(str(exc), 404)
    except sv.ValidationError as exc:
        return error(" ".join(exc.errors.values()), 400)
    total = cm.user_points(get_db(), g.user["id"])
    return jsonify({**result, "total_points": total, "level": cm.level_for(total)})


@bp.get("/me")
def me():
    if g.user is None:
        abort(401)
    total = cm.user_points(get_db(), g.user["id"])
    return jsonify({"name": g.user["name"], "role": g.user["role"],
                    "points": total, "level": cm.level_for(total)})
