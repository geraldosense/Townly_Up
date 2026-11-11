"""Townly Up — monitorização do espaço público do Fundão.

Protótipo académico (PAP, 12.º ano TDS). Não é um serviço oficial da
Câmara Municipal do Fundão.

Executar:  flask --app app run --debug --port 8000
"""
import os
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import click
from flask import (Flask, abort, flash, g, jsonify, redirect, render_template,
                   make_response, request, send_from_directory, url_for)
from werkzeug.exceptions import HTTPException

import community as cm
import geo
import mailer
import reference
import services as sv
import sms
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
    @app.template_filter("initials")
    def initials(name):
        """Iniciais para o avatar: 1.ª letra do primeiro e do último nome (ignora «(teste)», «·», etc.)."""
        words = [w for w in str(name or "").replace("·", " ").split() if w[:1].isalpha()]
        if not words:
            return "?"
        return (words[0][0] + (words[-1][0] if len(words) > 1 else "")).upper()

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
            ).fetchone()[0] + sv.community_unread(db, g.user)
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
            "contact_email": os.environ.get("CONTACT_EMAIL", "").strip(),
            "current_year": datetime.now(timezone.utc).year,
        }

    class _AuthFigures:
        """Números reais mostrados nas páginas de acesso (calculados só quando usados)."""
        def __getattr__(self, name):
            db = get_db()
            counts = sv.status_counts(db)
            values = {"active": sum(counts[st] for st in sv.ACTIVE_STATUSES), "resolved": counts[sv.RESOLVIDA],
                      "people": db.execute("SELECT COUNT(*) FROM users WHERE role = 'citizen'").fetchone()[0]}
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
        recent = public[:12]  # ativas + resolvidas nos últimos 30 dias, mais recentes primeiro (só dados públicos)
        return render_template("index.html", stats=stats, recent=recent)

    # ------------------------------------------------------------ Comunidade e informação
    @app.get("/freguesias")
    def parishes_index():
        """As 23 freguesias, com as ocorrências de cada uma e a Junta responsável."""
        db = get_db()
        followed = set(sv.followed_parishes(db, g.user["id"])) if g.user else set()
        return render_template("parishes.html", parishes=sv.parish_overview(db), followed=followed)

    @app.get("/como-funciona")
    def how_it_works():
        return render_template("pages/how.html", radius=cm.NEARBY_RADIUS_M, points=cm.POINTS)

    @app.get("/regras")
    def rules():
        return render_template("pages/rules.html")

    @app.get("/privacidade")
    def privacy():
        return render_template("pages/privacy.html")

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
        personal = db.execute(
            """SELECT n.*, i.ticket FROM notifications n JOIN incidents i ON i.id = n.incident_id
                WHERE n.user_id = ? AND i.deleted_at IS NULL ORDER BY n.created_at DESC, n.id DESC LIMIT 50""",
            (g.user["id"],),
        ).fetchall()
        # Alertas pessoais (estado, atribuição) + feed da comunidade (novos problemas), do mais recente para o mais antigo.
        items = sorted([dict(n) for n in personal] + sv.community_feed(db, g.user),
                       key=lambda n: n["created_at"], reverse=True)[:50]
        followed = set(sv.followed_parishes(db, g.user["id"]))
        parishes = sorted(((c, reference.short_name(n)) for c, n in reference.PARISHES), key=lambda p: p[1])
        scope = db.execute("SELECT alert_scope FROM users WHERE id = ?", (g.user["id"],)).fetchone()["alert_scope"]
        return render_template("notifications.html", items=items, followed=followed, parishes=parishes, scope=scope)

    @app.post("/alertas/alcance")
    @login_required
    def notifications_scope():
        sv.set_alert_scope(get_db(), g.user, request.form.get("scope", ""))
        flash("Preferência de alertas guardada.", "success")
        return redirect(url_for("notifications_page"))

    @app.post("/alertas/lidas")
    @login_required
    def notifications_read():
        sv.mark_alerts_seen(get_db(), g.user)
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
        errors, similar = {}, []
        if request.method == "POST":
            try:
                incident_id, ticket = sv.create_incident(
                    db, app.config["UPLOAD_FOLDER"], g.user, request.form,
                    request.files.getlist("photos"), app.config["MAX_IMAGE_BYTES"],
                )
                # Comprovativo por email, enviado na hora (em segundo plano: a página não fica à espera).
                created = sv.get_incident(db, incident_id)
                mailer.incident_receipt(app, g.user, created, public_url("incident_detail", incident_id=incident_id))
                # SMS para o(s) responsável(eis) da Junta da freguesia onde aconteceu.
                sms.new_incident_alert(app, db, created, reference.sms_parish(created["parish_code"]),
                                       public_url("admin_incident", incident_id=incident_id))
                flash(f"+{cm.POINTS['report']} pontos por ajudar a cidade!", "success")
                return redirect(url_for("incident_submitted", incident_id=incident_id))
            except sv.ValidationError as exc:
                errors, similar = exc.errors, exc.similar
                if exc.suspended:
                    logout_user()
                    flash(exc.errors["photos"], "danger")
                    return redirect(url_for("login"))
                if not similar:
                    flash("Verifique os campos assinalados. Os dados escritos foram mantidos.", "danger")
        categories = db.execute("SELECT * FROM categories WHERE active = TRUE ORDER BY id").fetchall()
        return render_template(
            "citizen/new_incident.html", categories=categories, errors=errors, radius=cm.NEARBY_RADIUS_M,
            form=request.form, center=sv.FUNDAO_CENTER, max_images=sv.MAX_IMAGES, similar=similar,
        ), (400 if errors else 200)

    def public_url(endpoint, **values):
        """Endereço completo para os emails (PUBLIC_URL, se definido; senão o endereço do pedido)."""
        base = os.environ.get("PUBLIC_URL", "").rstrip("/")
        return base + url_for(endpoint, **values) if base else url_for(endpoint, _external=True, **values)

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
        user, domain = g.user["email"].split("@", 1)
        masked = (user[:2] + "•" * max(1, len(user) - 2)) + "@" + domain  # mostra só o início do email
        return render_template("citizen/submitted.html", incident=owned_incident_or_403(incident_id), masked_email=masked)

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
            can_delete=sv.can_delete(g.user, incident),
        )

    @app.post("/incidents/<int:incident_id>/apagar")
    @role_required("citizen")
    def delete_incident(incident_id):
        incident = owned_incident_or_403(incident_id)
        try:
            sv.delete_incident(get_db(), g.user, incident)
        except sv.ValidationError as exc:
            flash(exc.errors["delete"], "warning")
            return redirect(url_for("incident_detail", incident_id=incident_id))
        flash(f"A ocorrência {incident['ticket']} foi apagada.", "info")
        return redirect(url_for("citizen_dashboard"))

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

    def similar_to_incident(db, incident):
        """Para os técnicos: ocorrências parecidas com esta (local, categoria, texto e fotografias)."""
        import similarity
        hashes = [r["phash"] for r in db.execute(
            "SELECT phash FROM incident_images WHERE incident_id = ? AND phash IS NOT NULL", (incident["id"],))]
        return similarity.find_similar(db, incident["latitude"], incident["longitude"], incident["category_id"],
                                       f"{incident['title']} {incident['description']}", hashes,
                                       exclude_id=incident["id"], limit=5)

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
            duplicates=similar_to_incident(db, incident) if incident["status"] in sv.ACTIVE_STATUSES else [],
            history=sv.get_history(db, incident_id), images=sv.get_images(db, incident_id),
            next_statuses=sv.TRANSITIONS[incident["status"]], closed=incident["status"] in sv.CLOSED_STATUSES,
            priorities=sv.PRIORITIES, errors=errors or {}, form=form or {},
            organizations=db.execute(
                "SELECT id, name, kind FROM organizations WHERE active = TRUE ORDER BY kind, name").fetchall(),
        )

    # ------------------------------------------------------------ Supervisão (só a Câmara)
    @app.get("/admin/supervisao")
    @role_required("admin")
    def admin_supervision():
        import supervision
        return render_template("admin/supervision.html", s=supervision.overview(get_db()))

    # ------------------------------------------------------------ Contas das entidades (só a Câmara)
    @app.route("/admin/contas", methods=["GET", "POST"])
    @role_required("admin")
    def admin_accounts():
        db = get_db()
        errors = {}
        if request.method == "POST":
            try:
                sv.create_staff(db, request.form, hash_password)
                flash(f"Conta criada para {request.form.get('email', '').strip().lower()}. "
                      "Entregue a palavra-passe temporária à pessoa: ela pode mudá-la em «A minha conta».", "success")
                return redirect(url_for("admin_accounts"))
            except sv.ValidationError as exc:
                errors = exc.errors
        test_active = db.execute("SELECT COUNT(*) FROM users WHERE email LIKE ? AND active = TRUE",
                                 (f"%@{TEST_DOMAIN}",)).fetchone()[0]
        return render_template("admin/accounts.html", accounts=sv.staff_accounts(db),
                               organizations=sv.organizations_for_select(db), errors=errors,
                               form=request.form if request.method == "POST" else {},
                               test_allowed=test_accounts_allowed(), test_active=test_active,
                               test_plan=test_accounts_plan(), test_password=TEST_PASSWORD)

    def test_accounts_allowed():
        """As contas de teste só se criam no computador (modo de desenvolvimento) ou com TEST_ACCOUNTS=1."""
        return app.debug or app.testing or os.environ.get("TEST_ACCOUNTS") == "1"

    @app.post("/admin/contas/teste")
    @role_required("admin")
    def admin_test_accounts():
        if not test_accounts_allowed():
            abort(403)
        db = get_db()
        if request.form.get("action") == "remover":
            flash(f"{remove_test_accounts(db)} contas de teste desativadas.", "info")
        else:
            create_test_accounts(db)
            flash(f"Contas de teste prontas. Palavra-passe de todas: {TEST_PASSWORD}", "success")
        return redirect(url_for("admin_accounts") + "#teste")

    @app.post("/admin/contas/<int:user_id>/estado")
    @role_required("admin")
    def admin_account_toggle(user_id):
        try:
            sv.set_user_active(get_db(), g.user, user_id, request.form.get("active") == "1")
            flash("Estado da conta atualizado.", "success")
        except sv.ValidationError as exc:
            flash(" ".join(exc.errors.values()), "warning")
        return redirect(url_for("admin_accounts"))

    @app.get("/avatar/<int:user_id>")
    def avatar(user_id):
        """Fotografia de perfil (pública: aparece no ranking e junto ao nome)."""
        row = get_db().execute("SELECT avatar, avatar_type FROM users WHERE id = ? AND active = TRUE", (user_id,)).fetchone()
        if row is None or row["avatar"] is None:
            abort(404)
        response = make_response(bytes(row["avatar"]))
        response.headers["Content-Type"] = row["avatar_type"] or "image/jpeg"
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"  # o endereço muda (?v=) a cada troca
        return response

    @app.post("/conta/fotografia")
    @login_required
    def account_avatar():
        try:
            if request.form.get("action") == "remover":
                sv.remove_avatar(get_db(), g.user)
                flash("Fotografia de perfil removida.", "info")
            else:
                sv.set_avatar(get_db(), g.user, request.files.get("avatar"), app.config["MAX_IMAGE_BYTES"])
                flash("Fotografia de perfil atualizada.", "success")
        except sv.ValidationError as exc:
            flash(exc.errors["avatar"], "danger")
        return redirect(url_for("account"))

    @app.route("/conta", methods=["GET", "POST"])
    @login_required
    def account():
        errors = {}
        db = get_db()
        info = db.execute("SELECT has_password FROM users WHERE id = ?", (g.user["id"],)).fetchone()
        if request.method == "POST":
            try:
                sv.change_password(db, g.user, request.form, verify_password, hash_password,
                                   require_current=bool(info["has_password"]))
                flash("Palavra-passe guardada.", "success")
                return redirect(url_for("account"))
            except sv.ValidationError as exc:
                errors = exc.errors
        return render_template("account.html", errors=errors, profile_form={}, has_password=bool(info["has_password"]))

    @app.post("/conta/dados")
    @login_required
    def account_profile():
        db = get_db()
        try:
            sv.update_profile(db, g.user, request.form, verify_password)
            flash("Dados da conta guardados.", "success")
            return redirect(url_for("account"))
        except sv.ValidationError as exc:
            info = db.execute("SELECT has_password FROM users WHERE id = ?", (g.user["id"],)).fetchone()
            return render_template("account.html", errors=exc.errors, profile_form=request.form,
                                   has_password=bool(info["has_password"])), 400

    # ------------------------------------------------------------ Responsáveis por freguesia (conta principal)
    @app.route("/admin/freguesias", methods=["GET", "POST"])
    @role_required("admin")
    def admin_parishes():
        db = get_db()
        errors, form = {}, {}
        if request.method == "POST":
            form = request.form
            try:
                sv.create_parish_manager(db, form, hash_password)
                flash(f"Responsável criado para {reference.short_name(dict(reference.PARISHES)[form['parish_code']])}. "
                      "Entregue a palavra-passe temporária à pessoa: ela pode mudá-la em «A minha conta».", "success")
                return redirect(url_for("admin_parishes") + f"#f-{form['parish_code']}")
            except sv.ValidationError as exc:
                errors = exc.errors
        rows = sv.parish_managers(db)
        return render_template("admin/parishes.html", rows=rows, errors=errors, form=form,
                               covered=sum(r["covered"] for r in rows), with_sms=sum(r["sms"] for r in rows),
                               sms_backend=sms.backend(), pretty_phone=sms.pretty_phone,
                               preselect=request.args.get("freguesia", "")), (400 if errors else 200)

    @app.post("/admin/freguesias/<int:user_id>/telemovel")
    @role_required("admin")
    def admin_parish_phone(user_id):
        db = get_db()
        row = db.execute("SELECT o.parish_code FROM users u JOIN organizations o ON o.id = u.organization_id "
                         "WHERE u.id = ? AND o.kind = 'junta'", (user_id,)).fetchone()
        if row is None:
            abort(404)
        try:
            sv.set_manager_phone(db, user_id, request.form)
            flash("Telemóvel atualizado.", "success")
        except sv.ValidationError as exc:
            flash(exc.errors["phone"], "danger")
        return redirect(url_for("admin_parishes") + f"#f-{row['parish_code']}")

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

    @app.cli.command("repor-admin")
    @click.option("--email", prompt="Email da conta principal", default="admin@townly.pt", show_default=True)
    @click.option("--name", prompt="Nome mostrado", default="Townly Up", show_default=True)
    @click.password_option(prompt="Nova palavra-passe", confirmation_prompt="Repita a palavra-passe")
    def reset_admin(email, name, password):
        """Esqueceu a palavra-passe? Repõe a conta principal (a 1.ª conta de administrador) com novo email e palavra-passe."""
        email = email.strip().lower()
        problem = sv.password_problem(password)
        if problem:
            raise click.ClickException(problem)
        init_db()
        db = get_db()
        main = db.execute("SELECT id, email FROM users WHERE role = 'admin' ORDER BY id LIMIT 1").fetchone()
        clash = db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if clash and (main is None or clash["id"] != main["id"]):
            raise click.ClickException(f"Já existe outra conta com o email {email}.")
        if main is None:
            sv.create_user(db, name, email, hash_password(password), role="admin",
                           organization_id=sv.organization_by(db, "camara")["id"])
            click.echo(f"Conta principal {email} criada.")
            return
        with db:
            db.execute("UPDATE users SET name = ?, email = ?, password_hash = ?, has_password = TRUE, active = TRUE "
                       "WHERE id = ?", (name.strip() or "Townly Up", email, hash_password(password), main["id"]))
        click.echo(f"Conta principal reposta: {main['email']} → {email}. Já pode entrar com a nova palavra-passe.")

    @app.cli.command("contas-teste")
    @click.option("--password", default="TownlyTeste2026", show_default=True, help="Palavra-passe de todas as contas de teste.")
    @click.option("--remover", is_flag=True, help="Desativa todas as contas @townlyup.test.")
    def test_accounts(password, remover):
        """Cria (ou desativa) as contas de TESTE: supervisão + 23 Juntas + 6 serviços (@townlyup.test)."""
        init_db()
        db = get_db()
        if remover:
            click.echo(f"{remove_test_accounts(db)} contas de teste desativadas.")
            return
        problem = sv.password_problem(password)
        if problem:
            raise click.ClickException(problem)
        created = create_test_accounts(db, password)
        click.echo(f"{created} contas de teste criadas (palavra-passe: {password}).")
        for name, email, *_ in test_accounts_plan():
            click.echo(f"  {email:45s} {name}")

    @app.cli.command("ensure-admin")
    def ensure_admin():
        """Cria a 1.ª conta da Câmara a partir de ADMIN_EMAIL / ADMIN_PASSWORD / ADMIN_NAME (se ainda não existir)."""
        email = os.environ.get("ADMIN_EMAIL", "").strip().lower()
        password = os.environ.get("ADMIN_PASSWORD", "")
        if not email or not password:
            click.echo("ADMIN_EMAIL/ADMIN_PASSWORD não definidos — nenhuma conta criada.")
            return
        problem = sv.password_problem(password)
        if problem:
            raise click.ClickException(f"ADMIN_PASSWORD: {problem}")
        init_db()
        db = get_db()
        if db.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
            click.echo(f"A conta {email} já existe.")
            return
        sv.create_user(db, os.environ.get("ADMIN_NAME", "Townly Up"), email,
                       hash_password(password), role="admin", organization_id=sv.organization_by(db, "camara")["id"])
        click.echo(f"Conta da Câmara {email} criada.")


TEST_DOMAIN = "townlyup.test"  # domínio reservado para testes: nunca é um email real


def test_accounts_plan():
    """Contas de teste: supervisão (Câmara), uma por Junta e uma por serviço técnico."""
    import re
    import unicodedata

    def slug(text):
        text = re.split(r",| e ", reference.short_name(text))[0]
        text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
        return re.sub(r"[^a-z0-9]+", "-", text).strip("-")

    plan = [("Equipa de Supervisão", f"supervisao@{TEST_DOMAIN}", "admin", "camara", None)]
    plan += [(f"Teste · Junta de {reference.short_name(n)}", f"junta.{slug(n)}@{TEST_DOMAIN}", "staff", "junta", c)
             for c, n in reference.PARISHES]
    plan += [(f"Teste · {s}", f"{slug(s)}@{TEST_DOMAIN}", "staff", "servico", s) for s in reference.SERVICES]
    return plan


TEST_PASSWORD = "TownlyTeste2026"


def create_test_accounts(db, password=TEST_PASSWORD):
    """Cria (ou reativa) as contas de teste. Devolve quantas foram criadas de novo."""
    created = 0
    for name, email, role, kind, value in test_accounts_plan():
        if db.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
            with db:
                db.execute("UPDATE users SET active = TRUE, password_hash = ? WHERE email = ?", (hash_password(password), email))
            continue
        org = sv.organization_by(db, kind, value)
        sv.create_user(db, name, email, hash_password(password), role=role, organization_id=org["id"])
        created += 1
    return created


def remove_test_accounts(db):
    with db:
        return db.execute("UPDATE users SET active = FALSE WHERE email LIKE ?", (f"%@{TEST_DOMAIN}",)).rowcount


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


if __name__ == "__main__":
    create_app().run(debug=True)
