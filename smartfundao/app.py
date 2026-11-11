"""Townly Up — monitorização do espaço público do Fundão.

Protótipo académico (PAP, 12.º ano TDS). Não é um serviço oficial da
Câmara Municipal do Fundão.

Executar:  flask --app app run --debug --port 8000
"""
import io
import os
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import click
from flask import (Flask, abort, flash, g, jsonify, redirect, render_template,
                   make_response, request, send_from_directory, url_for)
from werkzeug.datastructures import FileStorage
from werkzeug.exceptions import HTTPException

import community as cm
import geo
import reference
import services as sv
from api import bp as api_bp
from db import close_db, get_db, init_db
from security import (check_csrf, csrf_token, hash_password, is_safe_next,
                      load_logged_in_user, login_required, login_user,
                      logout_user, role_required, verify_password)

LISBON = ZoneInfo("Europe/Lisbon")

# Limites oficiais das freguesias do Fundão (CAOP 2022, Direção-Geral do Território), em GeoJSON WGS84.
PARISHES_URL = (
    "https://infogeo.dgterritorio.gov.pt/arcgis/rest/services/Hosted/DadosCAOP_2026_PCAT/FeatureServer/0/query"
    "?where=concelho+LIKE+%27FUND%25%27+OR+concelho+LIKE+%27Fund%25%27"
    "&outFields=dicofre,freguesia,concelho,area_ha&outSR=4326&geometryPrecision=5"
    "&maxAllowableOffset=0.00005&f=geojson"
)


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-apenas-para-desenvolvimento"),
        # PostgreSQL: DATABASE_URL=postgresql://utilizador@localhost:5432/townlyup
        # Sem DATABASE_URL usa-se SQLite em instance/townlyup.db (alternativa local).
        DATABASE_URL=os.environ.get("DATABASE_URL"),
        DATABASE=os.path.join(app.instance_path, "townlyup.db"),
        UPLOAD_FOLDER=os.path.join(app.root_path, "uploads"),
        MAX_IMAGE_BYTES=5 * 1024 * 1024,        # 5 MB por imagem
        MAX_CONTENT_LENGTH=20 * 1024 * 1024,    # 20 MB por pedido
        CSRF_ENABLED=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE") == "1",
    )
    if test_config:
        app.config.update(test_config)
    if os.environ.get("TRUST_PROXY") == "1":
        # Atrás do proxy HTTPS do alojamento: confiar nos cabeçalhos X-Forwarded-* (https, IP real).
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    os.makedirs(app.instance_path, exist_ok=True)
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    app.teardown_appcontext(close_db)
    app.before_request(load_logged_in_user)
    app.before_request(check_csrf)

    register_template_helpers(app)
    register_routes(app)
    app.register_blueprint(api_bp)
    register_errors(app)
    register_cli(app)

    # Aplica as migrações em falta e os dados de referência ao arrancar (idempotente).
    with app.app_context():
        init_db()
    ensure_parishes_in_background(app)
    return app


# ====================================================================== Templates
def register_template_helpers(app):
    @app.template_filter("local")
    def local_time(value, fmt="%d/%m/%Y %H:%M"):
        """Converte a data UTC da base de dados para a hora de Portugal."""
        if not value:
            return ""
        dt = datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return dt.astimezone(LISBON).strftime(fmt)

    app.add_template_filter(sv.status_slug, "slug")

    ASSET_VERSION = str(int(time.time()))  # muda a cada arranque do servidor

    @app.context_processor
    def inject_globals():
        unread, my_level = 0, None
        if g.get("user"):
            db = get_db()
            unread = db.execute(
                "SELECT COUNT(*) FROM notifications WHERE user_id = ? AND is_read = FALSE",
                (g.user["id"],),
            ).fetchone()[0]
            if g.user["role"] == "citizen":
                my_level = cm.level_for(cm.user_points(db, g.user["id"]))
        return {
            "csrf_token": csrf_token,
            "STATUS_DESCRIPTIONS": sv.STATUS_DESCRIPTIONS,
            "ALL_STATUSES": sv.ALL_STATUSES,
            "ROLE_LABELS": sv.ROLE_LABELS,
            "unread_count": unread,
            "my_level": my_level,
            "category_style": cm.category_style,
            "parish_image": parish_image,
            "asset_version": ASSET_VERSION,
            "auth_figures": _AuthFigures(),
        }

    class _AuthFigures:
        """Números reais mostrados nas páginas de acesso (calculados só quando usados)."""
        def __getattr__(self, name):
            counts = sv.status_counts(get_db())
            values = {"active": sum(counts[st] for st in sv.ACTIVE_STATUSES), "resolved": counts[sv.RESOLVIDA]}
            self.__dict__.update(values)
            return values[name]


    def parish_image(code):
        """Imagem de fundo da freguesia: static/img/freguesias/<código>.jpg|.jpeg|.png|.webp (se existir)."""
        folder = os.path.join(app.static_folder, "img", "freguesias")
        for ext in ("jpg", "jpeg", "png", "webp"):
            if os.path.exists(os.path.join(folder, f"{code}.{ext}")):
                return url_for("static", filename=f"img/freguesias/{code}.{ext}")
        return None

    app.parish_image = parish_image

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response


# ====================================================================== Rotas
def register_routes(app):
    def home_for(user):
        return url_for("admin_dashboard") if user["role"] in ("admin", "staff") else url_for("citizen_dashboard")

    # ------------------------------------------------------------ Público
    @app.get("/")
    def index():
        db = get_db()
        counts = sv.status_counts(db)
        public = cm.public_map(db)  # só dados seguros (sem nomes, títulos nem fotos)
        stats = {
            "active": sum(counts[st] for st in sv.ACTIVE_STATUSES),
            "resolved": counts[sv.RESOLVIDA],
            "people": db.execute("SELECT COUNT(*) FROM users WHERE role = 'citizen'").fetchone()[0],
        }
        recent = [i for i in public if i["status"] in sv.ACTIVE_STATUSES][:5]
        return render_template("index.html", stats=stats, recent=recent)

    @app.get("/freguesias/<code>")
    def parish_page(code):
        """Página de uma freguesia: imagem do ponto principal, números e ocorrências em curso."""
        db = get_db()
        parish = next((p for p in sv.parish_overview(db) if p["code"] == code), None)
        if parish is None:
            abort(404)
        junta = sv.organization_by(db, "junta", code)
        items = [i for i in cm.public_map(db) if i["parish_code"] == code]
        followed = sv.followed_parishes(db, g.user["id"]) if g.user else []
        return render_template("parish.html", parish=parish, junta=junta, items=items,
                               image=app.parish_image(code), following=code in followed,
                               followed_others=[c for c in followed if c != code])

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if g.user:
            return redirect(home_for(g.user))
        errors = {}
        if request.method == "POST":
            db = get_db()
            try:
                name, email, password, parish = sv.validate_registration(db, request.form)
                # O perfil é SEMPRE 'citizen': o campo role nunca vem do formulário.
                user_id = sv.create_user(db, name, email, hash_password(password), parish=parish)
                login_user({"id": user_id})
                flash(f"Bem-vindo/a, {name}! A sua conta foi criada.", "success")
                return redirect(url_for("citizen_dashboard"))
            except sv.ValidationError as exc:
                errors = exc.errors
        parishes = sorted(((c, reference.short_name(n)) for c, n in reference.PARISHES), key=lambda p: p[1])
        return render_template("register.html", errors=errors, form=request.form, parishes=parishes)

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if g.user:
            return redirect(home_for(g.user))
        error = None
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            db = get_db()
            user = db.execute("SELECT * FROM users WHERE email = ? AND active = TRUE", (email,)).fetchone()
            if user is None or not verify_password(user["password_hash"], password):
                error = "Email ou palavra-passe incorretos."  # mensagem neutra
            else:
                with db:
                    db.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (sv.utcnow(), user["id"]))
                login_user(user)
                flash(f"Sessão iniciada. Olá, {user['name']}!", "success")
                target = request.args.get("next")
                return redirect(target if is_safe_next(target) else home_for(user))
        return render_template("login.html", error=error, form=request.form)

    @app.post("/logout")
    @login_required
    def logout():
        logout_user()
        flash("Sessão terminada.", "info")
        return redirect(url_for("index"))

    # ------------------------------------------------------------ Munícipe
    @app.get("/citizen")
    @role_required("citizen")
    def citizen_dashboard():
        db = get_db()
        view = request.args.get("view", "ativas")
        if view not in ("ativas", "arquivo"):
            view = "ativas"
        incidents = sv.search_incidents(db, view=view, author_id=g.user["id"])
        counts = sv.status_counts(db, author_id=g.user["id"])
        return render_template(
            "citizen/dashboard.html", incidents=incidents, view=view,
            active_total=sum(counts[s] for s in sv.ACTIVE_STATUSES),
            closed_total=sum(counts[s] for s in sv.CLOSED_STATUSES),
        )

    # ------------------------------------------------------------ Alertas (todos os perfis)
    @app.get("/alertas")
    @login_required
    def notifications_page():
        db = get_db()
        items = db.execute(
            """SELECT n.*, i.ticket FROM notifications n JOIN incidents i ON i.id = n.incident_id
                WHERE n.user_id = ? ORDER BY n.created_at DESC, n.id DESC LIMIT 50""",
            (g.user["id"],),
        ).fetchall()
        followed = set(sv.followed_parishes(db, g.user["id"]))
        parishes = sorted(((c, reference.short_name(n)) for c, n in reference.PARISHES), key=lambda p: p[1])
        return render_template("notifications.html", items=items, followed=followed, parishes=parishes)

    @app.post("/alertas/lidas")
    @login_required
    def notifications_read():
        db = get_db()
        with db:
            db.execute("UPDATE notifications SET is_read = TRUE WHERE user_id = ?", (g.user["id"],))
        return redirect(url_for("notifications_page"))

    @app.post("/alertas/freguesias")
    @login_required
    def follow_parishes():
        """Escolher as freguesias de que recebe alertas de novos problemas."""
        codes = sv.set_followed_parishes(get_db(), g.user["id"], request.form.getlist("parish"))
        flash(f"Passa a receber alertas de {len(codes)} freguesia{'s' if len(codes) != 1 else ''}.", "success")
        target = request.form.get("next")
        return redirect(target if is_safe_next(target) else url_for("notifications_page"))

    @app.route("/incidents/new", methods=["GET", "POST"])
    @role_required("citizen")
    def new_incident():
        db = get_db()
        errors = {}
        if request.method == "POST":
            try:
                incident_id, ticket = sv.create_incident(
                    db, app.config["UPLOAD_FOLDER"], g.user, request.form,
                    request.files.getlist("photos"), app.config["MAX_IMAGE_BYTES"],
                )
                flash(f"+{cm.POINTS['report']} pontos por ajudar a cidade!", "success")
                return redirect(url_for("incident_submitted", incident_id=incident_id))
            except sv.ValidationError as exc:
                errors = exc.errors
                flash("Verifique os campos assinalados. Os dados escritos foram mantidos.", "danger")
        categories = db.execute("SELECT * FROM categories WHERE active = TRUE ORDER BY id").fetchall()
        return render_template(
            "citizen/new_incident.html", categories=categories, errors=errors, radius=cm.NEARBY_RADIUS_M,
            form=request.form, center=sv.FUNDAO_CENTER, max_images=sv.MAX_IMAGES,
        ), (400 if errors else 200)

    def owned_incident_or_403(incident_id):
        """Devolve a ocorrência se o utilizador for o titular ou a entidade que a trata."""
        incident = sv.get_incident(get_db(), incident_id)
        if incident is None:
            abort(404)
        if not sv.can_view(g.user, incident):
            abort(403)  # TA06: dados de outros cidadãos não são acessíveis
        return incident

    @app.get("/incidents/<int:incident_id>/enviada")
    @role_required("citizen")
    def incident_submitted(incident_id):
        return render_template("citizen/submitted.html", incident=owned_incident_or_403(incident_id))

    @app.get("/incidents/<int:incident_id>")
    @login_required
    def incident_detail(incident_id):
        incident = owned_incident_or_403(incident_id)
        if sv.can_manage(g.user, incident):
            return redirect(url_for("admin_incident", incident_id=incident_id))
        db = get_db()
        with db:
            db.execute(
                "UPDATE notifications SET is_read = TRUE WHERE user_id = ? AND incident_id = ?",
                (g.user["id"], incident_id),
            )
        return render_template(
            "citizen/detail.html", incident=incident, counts=cm.confirmation_counts(db, incident_id),
            history=sv.get_history(db, incident_id), images=sv.get_images(db, incident_id),
        )

    @app.get("/media/<int:image_id>")
    @login_required
    def media(image_id):
        """As fotografias não são públicas: só o titular e a central as veem."""
        image = get_db().execute("SELECT * FROM incident_images WHERE id = ?", (image_id,)).fetchone()
        if image is None:
            abort(404)
        owned_incident_or_403(image["incident_id"])
        if image["data"] is not None:  # guardada na base de dados
            response = make_response(bytes(image["data"]))
            response.headers["Content-Type"] = image["content_type"] or "application/octet-stream"
        else:  # fotografias antigas, guardadas em uploads/
            response = send_from_directory(app.config["UPLOAD_FOLDER"], image["stored_filename"])
        response.headers["Cache-Control"] = "private, max-age=3600"
        return response

    # ------------------------------------------------------------ Comunidade (estilo Waze)
    @app.get("/mapa")
    def live_map():
        """Mapa vivo: público, mas só com dados seguros (ver community.public_incident)."""
        db = get_db()
        categories = db.execute("SELECT id, name FROM categories WHERE active = TRUE ORDER BY id").fetchall()
        return render_template("map.html", categories=categories, center=sv.FUNDAO_CENTER)

    @app.post("/incidents/<int:incident_id>/confirm")
    @role_required("citizen")
    def confirm_incident(incident_id):
        """Alternativa sem JavaScript ao botão «Eu também vi» do mapa."""
        try:
            result = cm.confirm(get_db(), g.user, incident_id, request.form.get("kind", ""))
            flash(f"Obrigado! +{result['points']} pontos.", "success")
        except LookupError:
            abort(404)
        except sv.ValidationError as exc:
            flash(" ".join(exc.errors.values()), "warning")
        target = request.form.get("next")
        return redirect(target if is_safe_next(target) else url_for("live_map"))

    @app.get("/ranking")
    def ranking():
        db = get_db()
        me = None
        if g.user and g.user["role"] == "citizen":
            me = {"level": cm.level_for(cm.user_points(db, g.user["id"])),
                  "recent": cm.recent_points(db, g.user["id"])}
        return render_template(
            "ranking.html", people=cm.ranking_people(db), parishes=cm.ranking_parishes(db),
            levels=cm.LEVELS, points=cm.POINTS, reasons=cm.POINT_REASONS, me=me,
        )

    @app.get("/sw.js")
    def service_worker():
        """O service worker tem de ser servido na raiz para controlar todo o site (PWA)."""
        response = send_from_directory(app.static_folder, "sw.js", mimetype="application/javascript")
        response.headers["Cache-Control"] = "no-cache"
        response.headers["Service-Worker-Allowed"] = "/"
        return response

    @app.get("/manifest.webmanifest")
    def manifest():
        return send_from_directory(app.static_folder, "manifest.webmanifest",
                                   mimetype="application/manifest+json")

    @app.get("/offline")
    def offline():
        return render_template("offline.html")

    # ------------------------------------------------------------ Administração
    @app.get("/admin")
    @role_required("admin", "staff")
    def admin_dashboard():
        db = get_db()
        filters = {
            "view": request.args.get("view", "ativas"),
            "status": request.args.get("status", ""),
            "category_id": request.args.get("category_id", ""),
            "parish": request.args.get("parish", ""),
            "q": request.args.get("q", "").strip()[:100],
            "sort": request.args.get("sort", "recentes"),
        }
        incidents = sv.search_incidents(db, user=g.user, **filters)
        points = [
            {"id": i["id"], "ticket": i["ticket"], "title": i["title"], "status": i["status"],
             "slug": sv.status_slug(i["status"]), "lat": i["latitude"], "lng": i["longitude"],
             "url": url_for("admin_incident", incident_id=i["id"]),
             "icon": cm.category_style(i["category"])["icon"], "color": cm.category_style(i["category"])["color"],
             "seen": i["seen_count"]}
            for i in incidents
        ]
        return render_template(
            "admin/dashboard.html", incidents=incidents, filters=filters, points=points,
            counts=sv.status_counts(db, user=g.user), category_counts=sv.category_counts(db, user=g.user),
            categories=db.execute("SELECT * FROM categories ORDER BY id").fetchall(),
            parishes=db.execute("SELECT code, short_name FROM parishes ORDER BY short_name").fetchall(),
        )

    @app.get("/admin/incidents/<int:incident_id>")
    @role_required("admin", "staff")
    def admin_incident(incident_id, errors=None, form=None):
        db = get_db()
        incident = sv.get_incident(db, incident_id)
        if incident is None:
            abort(404)
        if not sv.can_manage(g.user, incident):
            abort(403)  # cada entidade só trata o que é seu
        return render_template(
            "admin/detail.html", incident=incident, counts=cm.confirmation_counts(db, incident_id),
            duplicates=cm.nearby(db, incident["latitude"], incident["longitude"], exclude_id=incident_id)
            if incident["status"] in sv.ACTIVE_STATUSES else [],
            history=sv.get_history(db, incident_id), images=sv.get_images(db, incident_id),
            next_statuses=sv.TRANSITIONS[incident["status"]], closed=incident["status"] in sv.CLOSED_STATUSES,
            priorities=sv.PRIORITIES, errors=errors or {}, form=form or {},
            organizations=db.execute(
                "SELECT id, name, kind FROM organizations WHERE active = TRUE ORDER BY kind, name").fetchall(),
        )

    @app.post("/admin/incidents/<int:incident_id>/status")
    @role_required("admin", "staff")
    def admin_update_status(incident_id):
        db = get_db()
        incident = sv.get_incident(db, incident_id)
        if incident is None:
            abort(404)
        if not sv.can_manage(g.user, incident):
            abort(403)
        try:
            new_status = sv.change_status(
                db, app.config["UPLOAD_FOLDER"], g.user, incident, request.form,
                request.files.getlist("resolution_photo"), app.config["MAX_IMAGE_BYTES"],
            )
        except sv.ValidationError as exc:
            flash("Não foi possível atualizar: " + " ".join(exc.errors.values()), "danger")
            return admin_incident(incident_id, errors=exc.errors, form=request.form), 400
        flash(f"{incident['ticket']} atualizado: {new_status}.", "success")
        return redirect(url_for("admin_incident", incident_id=incident_id))


# ====================================================================== Erros
def register_errors(app):
    messages = {
        400: ("Pedido inválido", "Não foi possível processar o pedido."),
        403: ("Sem permissão", "Não tem permissão para aceder a esta página."),
        404: ("Página não encontrada", "O endereço não existe ou foi removido."),
        413: ("Ficheiro demasiado grande", "O envio excede o tamanho máximo permitido (20 MB)."),
        500: ("Erro interno", "Ocorreu um erro inesperado. Tente novamente mais tarde."),
    }

    @app.errorhandler(HTTPException)
    def http_error(exc):
        if request.path.startswith("/api/"):
            api_messages = {401: "Inicie sessão.", 403: "Sem permissão.", 404: "Não encontrado."}
            return jsonify({"error": api_messages.get(exc.code, exc.name)}), exc.code
        title, text = messages.get(exc.code, (exc.name, exc.description))
        if exc.code == 400 and exc.description and "formulário" in exc.description:
            text = exc.description
        return render_template("errors/error.html", code=exc.code, title=title, text=text), exc.code

    @app.errorhandler(Exception)
    def unexpected_error(exc):
        # RNF05: nunca mostrar stack traces ao cidadão.
        app.logger.exception("Erro não tratado")
        if request.path.startswith("/api/"):
            return jsonify({"error": "Erro interno."}), 500
        title, text = messages[500]
        return render_template("errors/error.html", code=500, title=title, text=text), 500


# ====================================================================== Comandos
def register_cli(app):
    @app.cli.command("init-db")
    def init_db_command():
        """Cria as tabelas e as categorias."""
        init_db()
        click.echo("Base de dados pronta.")

    @app.cli.command("freguesias")
    def download_parishes():
        """Descarrega os limites oficiais das 23 freguesias (CAOP, DGT) para static/data/."""
        click.echo("A descarregar os limites das freguesias (CAOP / DGT)...")
        try:
            n = fetch_parishes(parishes_path(app))
        except RuntimeError as exc:
            raise click.ClickException(str(exc))
        click.echo(f"OK — {n} freguesias guardadas em static/data/freguesias_fundao.geojson")

    @app.cli.command("create-admin")
    @click.option("--name", prompt="Nome")
    @click.option("--email", prompt="Email")
    @click.password_option(prompt="Palavra-passe")
    def create_admin(name, email, password):
        """Cria um administrador (nunca pelo registo público)."""
        init_db()
        db = get_db()
        if db.execute("SELECT 1 FROM users WHERE email = ?", (email.lower(),)).fetchone():
            raise click.ClickException("Já existe um utilizador com esse email.")
        sv.create_user(db, name, email, hash_password(password), role="admin",
                       organization_id=sv.organization_by(db, "camara")["id"])
        click.echo(f"Administrador {email} criado.")

    @app.cli.command("seed-demo")
    def seed_demo():
        """Cria contas e ocorrências FICTÍCIAS para a demonstração."""
        init_db()
        db = get_db()
        if db.execute("SELECT 1 FROM users WHERE email = 'ana@demo.pt'").fetchone():
            raise click.ClickException("Os dados de demonstração já existem.")
        password = "Fundao2026"
        sede, alpedrinha, souto, alcaria, castelo_novo = "050434", "050406", "050426", "050402", "050413"

        def user(name, email, role="citizen", parish=None, org=None):
            uid = sv.create_user(db, name, email, hash_password(password), role=role, parish=parish,
                                 organization_id=org["id"] if org else None)
            return db.execute(
                """SELECT u.*, o.kind AS org_kind, o.parish_code AS org_parish FROM users u
                     LEFT JOIN organizations o ON o.id = u.organization_id WHERE u.id = ?""", (uid,)).fetchone()

        people = {
            "ana": user("Ana Martins", "ana@demo.pt", parish=sede),
            "carlos": user("Carlos Silva", "carlos@demo.pt", parish=sede),
            "beatriz": user("Beatriz Gonçalves", "beatriz@demo.pt", parish=alpedrinha),
            "diogo": user("Diogo Ferreira", "diogo@demo.pt", parish=souto),
            "marta": user("Marta Lopes", "marta@demo.pt", parish=alcaria),
        }
        camara = sv.organization_by(db, "camara")
        rui = user("Rui Costa", "rui@demo.pt", role="admin", org=camara)
        staff = {
            "joao": user("João Pires", "obras@demo.pt", role="staff", org=sv.organization_by(db, "servico", "Obras Municipais")),
            "luis": user("Luís Antunes", "iluminacao@demo.pt", role="staff", org=sv.organization_by(db, "servico", "Iluminação Pública")),
            "sofia": user("Sofia Matos", "junta.alpedrinha@demo.pt", role="staff", org=sv.organization_by(db, "junta", alpedrinha)),
        }
        cats = {r["name"]: r["id"] for r in db.execute("SELECT * FROM categories").fetchall()}

        used = {}

        def where(parish, fallback):
            """Ponto dentro da freguesia (com os limites CAOP) ou coordenadas aproximadas.
            Várias ocorrências na mesma freguesia ficam afastadas umas das outras (~300 m)."""
            base = geo.interior_point(parish)
            if base is None:
                return fallback
            n = used.get(parish, 0)
            used[parish] = n + 1
            for k in range(n, n + 8):
                dlat, dlng = (0.003 * ((k + 1) // 2) * (1 if k % 2 else -1), 0.0025 * (k // 2))
                lat, lng = round(base[0] + dlat, 6), round(base[1] + dlng, 6)
                if k == 0 or geo.parish_at(lat, lng) == parish:
                    return lat, lng
            return base

        # (autor, categoria, título, descrição, freguesia, coordenadas sem CAOP, cor da foto, transições, «Eu também vi»)
        demo = [
            ("ana", "Vias e pavimentos", "Buraco na estrada", "Buraco grande na estrada junto ao cruzamento com a Rua da Cale.",
             sede, (40.1392, -7.5021), (90, 90, 95),
             [("joao", sv.EM_ANALISE, "Ocorrência validada.", "Média"),
              ("joao", sv.EM_TRATAMENTO, "Equipa deslocada ao local para reparação.", "Média"),
              ("joao", sv.RESOLVIDA, "Buraco reparado e pavimento regularizado.", "Média")], ["carlos", "beatriz"]),
            ("ana", "Iluminação pública", "Candeeiro apagado", "O candeeiro em frente ao n.º 12 está apagado há uma semana.",
             sede, (40.1421, -7.4987), (40, 45, 70),
             [("luis", sv.EM_ANALISE, "Pedido encaminhado para verificação.", "Baixa")], ["carlos"]),
            ("carlos", "Limpeza urbana", "Lixo acumulado junto ao ecoponto", "Sacos de lixo acumulados fora do ecoponto há vários dias.",
             sede, (40.1450, -7.5003), (120, 110, 80),
             [("rui", sv.EM_ANALISE, "A confirmar com a Limpeza Urbana.", "Alta"),
              ("rui", sv.EM_TRATAMENTO, "Recolha agendada para amanhã de manhã.", "Alta")], ["ana", "diogo", "marta"]),
            ("beatriz", "Mobiliário urbano", "Banco de jardim partido", "Banco do largo com as tábuas partidas, perigoso para crianças.",
             alpedrinha, (40.0985, -7.4672), (120, 90, 70),
             [("sofia", sv.EM_ANALISE, "A Junta vai avaliar a substituição.", "Média")], ["ana"]),
            ("beatriz", "Espaços verdes", "Árvore caída no caminho", "Ramo de grande dimensão caído sobre o caminho pedonal.",
             alpedrinha, (40.0990, -7.4655), (60, 110, 60), [], ["diogo", "marta"]),
            ("diogo", "Água e saneamento", "Fuga de água no passeio", "Água a correr do passeio há dois dias, junto à paragem de autocarro.",
             souto, (40.1338, -7.5544), (60, 90, 140), [], ["carlos", "beatriz", "ana"]),
            ("marta", "Iluminação pública", "Rua sem iluminação", "Três candeeiros seguidos apagados na rua junto à escola.",
             alcaria, (40.1590, -7.4870), (40, 40, 60), [], []),
            ("carlos", "Sinalização e trânsito", "Sinal de STOP tombado", "O sinal de STOP do cruzamento está tombado e quase não se vê.",
             castelo_novo, (40.0780, -7.4960), (150, 60, 60), [], ["ana", "diogo"]),
        ]
        actors = {**staff, "rui": rui}
        for author, cat, title, desc, parish, fallback, color, steps, seen_by in demo:
            lat, lng = where(parish, fallback)
            photo = FileStorage(io.BytesIO(_demo_image(color)), filename="foto.jpg")
            iid, ticket = sv.create_incident(
                db, app.config["UPLOAD_FOLDER"], people[author],
                {"category_id": cats[cat], "title": title, "description": desc,
                 "latitude": lat, "longitude": lng, "privacy": "1"},
                [photo], app.config["MAX_IMAGE_BYTES"],
            )
            for who in seen_by:  # a comunidade confirma antes de a entidade tratar
                cm.confirm(db, people[who], iid, "seen")
            for actor, status, note, prio in steps:
                sv.change_status(db, app.config["UPLOAD_FOLDER"], actors[actor], sv.get_incident(db, iid),
                                 {"new_status": status, "note": note, "priority": prio},
                                 [], app.config["MAX_IMAGE_BYTES"])
            click.echo(f"  {ticket} — {title}")
        click.echo(f"Contas de demonstração (palavra-passe: {password}):")
        click.echo("  Munícipes:     ana@demo.pt · carlos@demo.pt · beatriz@demo.pt · diogo@demo.pt · marta@demo.pt")
        click.echo("  Técnicos:      obras@demo.pt · iluminacao@demo.pt · junta.alpedrinha@demo.pt")
        click.echo("  Central:       rui@demo.pt")


def parishes_path(app):
    return os.path.join(app.static_folder, "data", "freguesias_fundao.geojson")


def _ssl_context():
    """Contexto HTTPS com certificados fiáveis.

    O Python instalado a partir de python.org no Mac não usa os certificados do sistema
    (erro CERTIFICATE_VERIFY_FAILED). Usamos o pacote certifi, ou a cópia que vem com o pip.
    """
    import ssl
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        try:
            from pip._vendor import certifi as pip_certifi
            return ssl.create_default_context(cafile=pip_certifi.where())
        except Exception:
            return ssl.create_default_context()


def fetch_parishes(target):
    """Descarrega o GeoJSON das freguesias e grava-o de forma atómica. Devolve o nº de freguesias."""
    import json
    import ssl
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(PARISHES_URL, timeout=60, context=_ssl_context()) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (ssl.SSLError, urllib.error.URLError) as exc:
        reason = getattr(exc, "reason", exc)
        hint = (" No Mac: Aplicações > Python 3.x > «Install Certificates.command»."
                if "CERTIFICATE" in str(reason).upper() else "")
        raise RuntimeError(f"Não foi possível descarregar ({reason}).{hint} Alternativa — abra no navegador e guarde "
                           f"como static/data/freguesias_fundao.geojson:\n{PARISHES_URL}") from None
    except Exception as exc:
        raise RuntimeError(f"Não foi possível descarregar ({exc}).") from None
    features = data.get("features", [])
    if len(features) != 23:
        raise RuntimeError(f"Resposta inesperada: {len(features)} freguesias (esperadas 23).")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    tmp = target + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, target)  # nunca deixa um ficheiro meio escrito
    return len(features)


def ensure_parishes_in_background(app):
    """Se faltarem os limites das freguesias, tenta descarregá-los sem atrasar o arranque."""
    import threading

    target = parishes_path(app)
    if app.config.get("TESTING") or os.path.exists(target):
        return

    def worker():
        try:
            n = fetch_parishes(target)
            app.logger.info("Limites das freguesias descarregados (%s freguesias).", n)
        except RuntimeError as exc:
            app.logger.warning("Freguesias: %s", exc)

    threading.Thread(target=worker, daemon=True).start()


def _demo_image(color):
    """Gera uma imagem fictícia simples (sem pessoas nem matrículas)."""
    import random
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (800, 600), color)
    draw = ImageDraw.Draw(img)
    rnd = random.Random(sum(color))
    for _ in range(2500):
        x, y = rnd.randrange(800), rnd.randrange(600)
        shade = rnd.randrange(-25, 25)
        draw.point((x, y), fill=tuple(max(0, min(255, c + shade)) for c in color))
    draw.ellipse((250, 200, 550, 420), fill=tuple(max(0, c - 45) for c in color))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=85)
    return out.getvalue()


if __name__ == "__main__":
    create_app().run(debug=True)
