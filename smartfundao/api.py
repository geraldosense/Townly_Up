"""API JSON v1 — usada pelo mapa vivo e base das futuras apps móveis.

Respostas sempre em JSON. Nesta versão a autenticação é a sessão do site
(cookie) + token CSRF no cabeçalho X-CSRF-Token. Para as apps da Play Store /
App Store, o passo seguinte será acrescentar autenticação por token.

  GET  /api/v1/categories                 categorias com ícone e cor
  GET  /api/v1/map                        ocorrências do mapa público
  GET  /api/v1/incidents/nearby?lat&lng   ocorrências ativas a menos de 50 m
  POST /api/v1/incidents/<id>/confirm     {"kind": "seen" | "fixed"}
  GET  /api/v1/me                         pontos e nível do utilizador
  GET  /api/v1/parishes                   freguesias com contagens e imagem
"""
from flask import Blueprint, abort, current_app, g, jsonify, request

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
    rows = get_db().execute("SELECT id, name FROM categories WHERE active = TRUE ORDER BY id").fetchall()
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


@bp.post("/incidents/similar")
def similar():
    """Antes de enviar: procura ocorrências parecidas (local, categoria, texto e fotografias).
    Recebe o mesmo formulário (multipart) do «Comunicar ocorrência»; não grava nada."""
    import similarity
    require_citizen()
    lat = sv.parse_coordinate(request.form.get("latitude"))
    lng = sv.parse_coordinate(request.form.get("longitude"))
    category_id = request.form.get("category_id", type=int)
    if lat is None or lng is None or category_id is None:
        return jsonify({"incidents": []})
    images = []
    for f in [f for f in request.files.getlist("photos") if f and f.filename][: sv.MAX_IMAGES]:
        try:
            images.append(sv.process_image(f, current_app.config["MAX_IMAGE_BYTES"]))
        except ValueError:
            pass  # a validação a sério é feita no envio
    text = f"{request.form.get('title', '')} {request.form.get('description', '')}"
    found = similarity.find_similar(get_db(), lat, lng, category_id, text, similarity.hashes_for(images))
    keys = ("id", "ticket", "category", "status", "distance_m", "seen", "score", "reasons", "strong")
    return jsonify({"incidents": [{k: f[k] for k in keys} for f in found]})


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


@bp.get("/parishes")
def parishes():
    rows = sv.parish_overview(get_db())
    return jsonify([{"code": r["code"], "name": r["name"], "short_name": r["short_name"],
                     "active": r["active"], "resolved": r["resolved"], "landmark": r["landmark"],
                     "image": current_app.parish_image(r["code"])} for r in rows])
