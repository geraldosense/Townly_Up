"""Townly Up — monitorização do espaço público do Fundão.

Protótipo académico (PAP, 12.º ano TDS). Não é um serviço oficial da
Câmara Municipal do Fundão.

Executar:  flask --app app run --debug --port 8000
"""
import io
import json
import os
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import click
from flask import (Flask, abort, flash, g, jsonify, redirect, render_template,
                   make_response, request, send_from_directory, session, url_for)
from werkzeug.exceptions import HTTPException

import community as cm
import geo
import google_auth
import mailer
import reference
import services as sv
import sms
import territory
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
        MAX_AVATAR_BYTES=18 * 1024 * 1024,      # fotografia de perfil: qualquer imagem até 18 MB (fica 320×320)
        MAX_CONTENT_LENGTH=20 * 1024 * 1024,    # 20 MB por pedido
        CSRF_ENABLED=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE") == "1",
        TEMPLATES_AUTO_RELOAD=os.environ.get("TEMPLATES_AUTO_RELOAD") == "1" or None,
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

    # Concelhos ativos = os que têm a conta da Câmara criada (lido da base de dados, uma vez por pedido).
    import territory as _territory
    from flask import has_app_context

    def _active_now():
        if not has_app_context():
            return _territory.ACTIVE
        if "active_municipalities" not in g:
            g.active_municipalities = sv.active_municipalities(get_db())
        return g.active_municipalities
    _territory._active_source = _active_now

    # Aplica as migrações em falta e os dados de referência ao arrancar (idempotente).
    with app.app_context():
        init_db()
    ensure_parishes_in_background(app)
    return app


def report_center(user):
    """Centro do mapa ao comunicar: o do concelho da conta (Fundão por omissão)."""
    m = (user["municipality"] if user is not None else None) or "Fundão"
    if m == "Fundão":
        return sv.FUNDAO_CENTER
    try:
        from flask import current_app
        b = territory.geometry(m, current_app.config.get("GEO_CACHE_DIR") or current_app.instance_path)["bbox"]
        return ((b[1] + b[3]) / 2, (b[0] + b[2]) / 2)
    except Exception:
        return sv.FUNDAO_CENTER


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
    PARISH_CHOICES = sorted(((c, reference.short_name(n)) for c, n in reference.PARISHES), key=lambda p: p[1])

    @app.context_processor
    def inject_globals():
        unread, my_level, pending_warning, pending_requests = 0, None, None, 0
        if g.get("user"):
            db = get_db()
            if g.user["role"] == "admin":          # pedidos de acesso por tratar (aparecem no menu e no início)
                pending_requests = db.execute("SELECT COUNT(*) FROM entity_requests WHERE status = 'pendente'").fetchone()[0]
            unread = db.execute(
                "SELECT COUNT(*) FROM notifications WHERE user_id = ? AND is_read = FALSE",
                (g.user["id"],),
            ).fetchone()[0] + sv.community_unread(db, g.user)
            if g.user["role"] == "citizen":
                my_level = cm.level_for(cm.user_points(db, g.user["id"]))
                pending_warning = sv.unseen_warning(db, g.user["id"])
        return {
            "csrf_token": csrf_token,
            "STATUS_DESCRIPTIONS": sv.STATUS_DESCRIPTIONS,
            "ALL_STATUSES": sv.ALL_STATUSES,
            "ROLE_LABELS": sv.ROLE_LABELS,
            "unread_count": unread,
            "pending_requests": pending_requests,
            "my_level": my_level,
            "category_style": cm.category_style,
            "parish_image": parish_image,
            "asset_version": ASSET_VERSION,
            "auth_figures": _AuthFigures(),
            "contact_email": os.environ.get("CONTACT_EMAIL", "").strip(),
            "app_version": "1.0",
            # Ligações das lojas (quando a app for publicada); sem elas aparece «Brevemente».
            "app_store_url": os.environ.get("APP_STORE_URL", "").strip(),
            "play_store_url": os.environ.get("PLAY_STORE_URL", "").strip(),
            "current_year": datetime.now(timezone.utc).year,
            "parish_choices": PARISH_CHOICES,
            "pending_warning": pending_warning,
            "warning_labels": sv.WARNING_KIND_LABELS,
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
        # Respostas grandes (mapa, páginas) comprimidas: 5–10× menos dados no telemóvel.
        if (response.status_code == 200 and not response.direct_passthrough and "Content-Encoding" not in response.headers
                and "gzip" in request.headers.get("Accept-Encoding", "")
                and (response.mimetype or "").startswith(("application/json", "text/html", "text/css", "application/javascript", "text/javascript"))):
            data = response.get_data()
            if len(data) > 1400:
                import gzip
                response.set_data(gzip.compress(data, 5))
                response.headers["Content-Encoding"] = "gzip"
                response.headers["Vary"] = "Accept-Encoding"
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
        stats = {
            "active": sum(counts[st] for st in sv.ACTIVE_STATUSES),
            "resolved": counts[sv.RESOLVIDA],
            "people": db.execute("SELECT COUNT(*) FROM users WHERE role = 'citizen'").fetchone()[0],
        }
        return render_template("index.html", stats=stats)

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
        items = cm.public_map(db, parish=code, limit=500)
        followed = sv.followed_parishes(db, g.user["id"]) if g.user else []
        return render_template("parish.html", parish=parish, junta=junta, items=items,
                               image=app.parish_image(code), following=code in followed,
                               followed_others=[c for c in followed if c != code])

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if g.user:
            return redirect(home_for(g.user))
        errors = {}
        access = "entidade" if (request.form.get("access") or request.args.get("acesso")) == "entidade" else "cidadao"
        if request.method == "POST" and access == "entidade":
            # Entidades não criam conta sozinhas: fazem um pedido de acesso, que a conta principal verifica.
            try:
                entity = sv.create_entity_request(get_db(), request.form)
                flash(f"Pedido de acesso enviado para {entity}. A Câmara vai verificar e enviar-lhe os dados de entrada "
                      "para o email indicado.", "success")
                return redirect(url_for("login", acesso="entidade"))
            except sv.ValidationError as exc:
                errors = exc.errors
        elif request.method == "POST":
            db = get_db()
            try:
                name, email, password, parish, district, municipality = sv.validate_registration(db, request.form, app.config.get("GEO_CACHE_DIR") or app.instance_path)
                # O perfil é SEMPRE 'citizen': o campo role nunca vem do formulário.
                user_id = sv.create_user(db, name, email, hash_password(password), parish=parish,
                                         district=district, municipality=municipality)
                login_user({"id": user_id})
                if territory.is_active(municipality):
                    flash(f"Bem-vindo/a, {name}! A sua conta foi criada.", "success")
                else:
                    flash(f"Bem-vindo/a, {name}! O Townly Up ainda não está disponível no concelho de {municipality}: "
                          "pode explorar o mapa e, quando a sua Câmara aderir, passa a poder comunicar.", "info")
                return redirect(url_for("citizen_dashboard"))
            except sv.ValidationError as exc:
                errors = exc.errors
        parishes = sorted(((c, reference.short_name(n)) for c, n in reference.PARISHES), key=lambda p: p[1])
        return render_template("register.html", errors=errors, form=request.form, parishes=parishes,
                               territory=territory.options(), access=access, entity_kinds=sv.ENTITY_KINDS)

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if g.user:
            return redirect(home_for(g.user))
        error = None
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            access = request.form.get("access", "")       # «cidadao» ou «entidade» (separadores do ecrã de entrada)
            db = get_db()
            user = db.execute("SELECT * FROM users WHERE email = ? AND active = TRUE", (email,)).fetchone()
            banned = None if user else db.execute(
                "SELECT password_hash FROM users WHERE email = ? AND banned_at IS NOT NULL AND active = FALSE", (email,)).fetchone()
            if banned is not None and verify_password(banned["password_hash"], password):
                error = ("Esta conta foi banida por desrespeitar duas vezes as regras da comunidade "
                         "(linguagem ofensiva ou imagens impróprias). Se acha que foi um engano, contacte a Câmara Municipal.")
            elif user is None or not verify_password(user["password_hash"], password):
                error = "Email ou palavra-passe incorretos."  # mensagem neutra
            elif access == "entidade" and user["role"] == "citizen":
                error = "Esta é uma conta de cidadão. Use o separador «Cidadão» para entrar."
            elif access == "cidadao" and user["role"] != "citizen":
                error = "Esta é uma conta de entidade (Câmara, Junta ou serviço). Use o separador «Câmara»."
            else:
                with db:
                    db.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (sv.utcnow(), user["id"]))
                login_user(user)
                flash(f"Sessão iniciada. Olá, {user['name']}!", "success")
                target = request.args.get("next")
                return redirect(target if is_safe_next(target) else home_for(user))
        access = request.form.get("access") or request.args.get("acesso") or "cidadao"
        return render_template("login.html", error=error, form=request.form,
                               access="entidade" if access == "entidade" else "cidadao")

    # ------------------------------------------------------------ Continuar com o Google (munícipes)
    def google_redirect_uri():
        """O endereço de retorno tem de ser EXATAMENTE um dos registados na Google Cloud Console."""
        fixed = os.environ.get("GOOGLE_REDIRECT_URI", "").strip()
        if fixed:
            return fixed
        base = os.environ.get("PUBLIC_URL", "").strip().rstrip("/")
        return (base + url_for("google_callback")) if base else url_for("google_callback", _external=True)

    def google_identity(db):
        """Conta Google ligada a esta conta (para mostrar no perfil), ou None."""
        return db.execute("SELECT email FROM user_identities WHERE user_id = ? AND provider = 'google'",
                          (g.user["id"],)).fetchone()

    @app.get("/entrar/google")
    def google_login():
        if g.user:
            return redirect(home_for(g.user))
        if not google_auth.configured():
            flash("O início de sessão com o Google ainda não está configurado neste servidor. Entre com email e palavra-passe.", "info")
            return redirect(url_for("login"))
        # A Google só aceita «localhost» (e não 127.0.0.1) como endereço local: muda para lá antes de começar,
        # para o regresso encontrar a mesma sessão.
        if request.host.split(":")[0] == "127.0.0.1" and not os.environ.get("GOOGLE_REDIRECT_URI"):
            return redirect(request.url.replace("://127.0.0.1", "://localhost", 1))
        state, nonce = google_auth.new_state()
        target = request.args.get("next")
        session["google_oauth"] = {"state": state, "nonce": nonce, "next": target if is_safe_next(target) else None}
        return redirect(google_auth.authorize_url(google_redirect_uri(), state, nonce))

    @app.get("/entrar/google/retorno")
    def google_callback():
        pending = session.pop("google_oauth", None) or {}
        if request.args.get("error"):                       # a pessoa cancelou no ecrã do Google
            flash("Início de sessão com o Google cancelado.", "info")
            return redirect(url_for("login"))
        if not pending or request.args.get("state") != pending.get("state") or not request.args.get("code"):
            flash("O pedido ao Google expirou. Tente outra vez.", "warning")
            return redirect(url_for("login"))
        db = get_db()
        try:
            claims = google_auth.exchange_code(request.args["code"], google_redirect_uri(), _ssl_context())
            info = google_auth.check_claims(claims, pending.get("nonce"))
            user, created = google_auth.find_or_create_user(db, info, sv.utcnow(), sv.create_user)
        except google_auth.GoogleError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("login"))
        with db:
            db.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (sv.utcnow(), user["id"]))
        login_user(user)
        if created:   # conta nova: falta confirmar o concelho e a freguesia
            flash(f"Conta criada com o Google. Bem-vindo, {user['name'].split()[0]}! Confirme o seu concelho.", "success")
            return redirect(url_for("settings") + "#concelho")
        flash(f"Sessão iniciada. Olá, {user['name']}!", "success")
        return redirect(pending.get("next") or home_for(user))

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
        # Abrir os alertas = vê-los: ficam todos como lidos e o número do sino desaparece logo.
        # (A lista acima já foi lida, por isso os novos continuam destacados nesta visita.)
        sv.mark_alerts_seen(db, g.user)
        load_logged_in_user()
        return render_template("notifications.html", items=items, followed=followed, parishes=parishes, scope=scope)

    @app.post("/alertas/alcance")
    @login_required
    def notifications_scope():
        sv.set_alert_scope(get_db(), g.user, request.form.get("scope", ""))
        flash("Preferência de alertas guardada.", "success")
        return redirect(url_for("settings") + "#notificacoes")

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
        return redirect(target if is_safe_next(target) else url_for("settings") + "#notificacoes")

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
                if exc.banned:
                    logout_user()
                    flash(next(iter(exc.errors.values())), "danger")
                    return redirect(url_for("login"))
                if exc.warned:
                    flash(next(iter(exc.errors.values())), "warning")
                if not similar:
                    flash("Verifique os campos assinalados. Os dados escritos foram mantidos.", "danger")
        categories = db.execute("SELECT * FROM categories WHERE active = TRUE ORDER BY id").fetchall()
        subcategories = sv.subcategories_by_category(db)
        return render_template(
            "citizen/new_incident.html", categories=categories, subcategories=subcategories, errors=errors, radius=cm.NEARBY_RADIUS_M,
            form=request.form, center=report_center(g.user), max_images=sv.MAX_IMAGES, similar=similar,
            can_report=sv.can_report(g.user),
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
        incident = sv.get_incident(get_db(), incident_id)
        if incident is None:
            abort(404)
        if not sv.can_view(g.user, incident):
            # Ocorrência de outra pessoa (ex.: aberta a partir de um alerta): abre-a no mapa, com a ficha pública
            # (estado, tipo, fotografia, «Eu também vi»). Os dados privados do autor continuam protegidos (TA06).
            if cm.is_public(get_db(), incident_id):
                return redirect(url_for("live_map", ocorrencia=incident_id))
            flash(f"A ocorrência {incident['ticket']} já foi fechada ({incident['status'].lower()}) e saiu do mapa.", "info")
            return redirect(url_for("notifications_page"))
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
            can_rate=sv.can_rate(g.user, incident), rating=sv.latest_rating(db, incident),
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

    _thumbs = {}

    @app.get("/media/publica/<int:incident_id>.jpg")
    @app.get("/media/publica/<int:incident_id>/<kind>.jpg")
    def public_photo(incident_id, kind="antes"):
        """Miniaturas públicas (480 px, sem metadados):
           • «antes»  — 1.ª fotografia do problema de ocorrências que estão no mapa público ou já resolvidas;
           • «depois» — 1.ª fotografia da resolução, só de ocorrências resolvidas («Antes e depois»).
        As restantes fotografias continuam privadas (ver media())."""
        if kind not in ("antes", "depois"):
            abort(404)
        db = get_db()
        row = db.execute("SELECT status, deleted_at FROM incidents WHERE id = ?", (incident_id,)).fetchone()
        resolved = row is not None and row["deleted_at"] is None and row["status"] == sv.RESOLVIDA
        if not (resolved or (kind == "antes" and cm.is_public(db, incident_id))):
            abort(404)
        image = db.execute("""SELECT id, data, stored_filename FROM incident_images WHERE incident_id = ? AND kind = ?
                               ORDER BY id LIMIT 1""", (incident_id, "report" if kind == "antes" else "resolution")).fetchone()
        if image is None:
            abort(404)
        if image["id"] not in _thumbs:
            raw = bytes(image["data"]) if image["data"] is not None else open(
                os.path.join(app.config["UPLOAD_FOLDER"], image["stored_filename"]), "rb").read()
            from PIL import Image
            with Image.open(io.BytesIO(raw)) as im:
                im = im.convert("RGB")
                im.thumbnail((720, 720) if kind == "depois" or request.args.get("g") else (480, 480))
                out = io.BytesIO()
                im.save(out, "JPEG", quality=80, optimize=True)
            if len(_thumbs) > 200:
                _thumbs.clear()
            _thumbs[image["id"]] = out.getvalue()
        response = make_response(_thumbs[image["id"]])
        response.headers["Content-Type"] = "image/jpeg"
        response.headers["Cache-Control"] = "public, max-age=600"
        return response

    @app.get("/resolvidas")
    def before_after():
        """«Antes e depois»: problemas resolvidos com a fotografia do problema e a da resolução."""
        db = get_db()
        items = sv.before_after(db)
        return render_template("pages/before_after.html", items=items, satisfaction=sv.satisfaction(db))

    @app.post("/incidents/<int:incident_id>/avaliar")
    @role_required("citizen")
    def rate_incident(incident_id):
        db = get_db()
        incident = sv.get_incident(db, incident_id)
        if incident is None:
            abort(404)
        try:
            reopened = sv.rate_resolution(db, g.user, incident, request.form)
        except PermissionError:
            abort(403)
        except sv.ValidationError as exc:
            flash(" ".join(exc.errors.values()), "danger")
            if exc.banned:
                logout_user()
                return redirect(url_for("login"))
            return redirect(url_for("incident_detail", incident_id=incident_id) + "#avaliar")
        flash("A ocorrência foi reaberta e a entidade foi avisada." if reopened else "Obrigado pela avaliação!", "success")
        return redirect(url_for("incident_detail", incident_id=incident_id))

    # ------------------------------------------------------------ Comunidade (estilo Waze)
    @app.get("/mapa")
    def live_map():
        """Mapa vivo: público, mas só com dados seguros (ver community.public_incident)."""
        db = get_db()
        categories = db.execute("SELECT id, name FROM categories WHERE active = TRUE ORDER BY id").fetchall()
        # O mapa abre (e fica limitado) ao concelho da conta. Concelhos que ainda não aderiram veem o aviso.
        # O mapa abre no concelho da conta (qualquer um dos 308) e, se a pessoa indicou a freguesia, já nela.
        mine = g.user["municipality"] if g.user and g.user["municipality"] else None
        name = mine if mine in territory.MUNICIPALITY_DISTRICT else territory.DEFAULT
        area = {"name": name, "district": territory.MUNICIPALITY_DISTRICT.get(name), "active": territory.is_active(name),
                "center": territory.ACTIVE.get(name, {}).get("center")}
        my_parish = (g.user["home_parish"] or g.user["parish_code"]) if g.user else None
        # Sem sessão iniciada o mapa mostra Portugal continental inteiro (com as ocorrências dos concelhos aderentes).
        return render_template("map.html", categories=categories, area=area, my_municipality=mine, my_parish=my_parish,
                               national=g.user is None,
                               can_report=(g.user is None or g.user["role"] != "citizen" or sv.can_report(g.user)))

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

    @app.get("/saude")
    def health():
        """Verificação do alojamento (Render): o servidor responde e a base de dados também."""
        get_db().execute("SELECT 1").fetchone()
        return jsonify(status="ok")

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
        # Paginação: 50 de cada vez (com milhares de ocorrências a página continua leve).
        per_page = 50
        total = sv.search_incidents(db, user=g.user, count=True, **filters)
        pages = max(1, -(-total // per_page))
        page = min(max(1, request.args.get("pagina", 1, type=int)), pages)
        incidents = sv.search_incidents(db, user=g.user, limit=per_page, offset=(page - 1) * per_page, **filters)
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
            page=page, pages=pages, total=total,
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
            ratings=db.execute("SELECT stars, comment, reopened, created_at FROM resolution_ratings WHERE incident_id = ? "
                               "ORDER BY id DESC", (incident_id,)).fetchall(),
            organizations=db.execute(
                "SELECT id, name, kind FROM organizations WHERE active = TRUE ORDER BY kind, name").fetchall(),
        )

    # ------------------------------------------------------------ Supervisão (só a Câmara)
    @app.get("/admin/supervisao")
    @role_required("admin")
    def admin_supervision():
        import supervision
        db = get_db()
        return render_template("admin/supervision.html", s=supervision.overview(db), safety=sv.safety_overview(db))

    @app.post("/admin/contas/<int:user_id>/levantar-banimento")
    @role_required("admin")
    def lift_ban(user_id):
        if sv.lift_ban(get_db(), user_id):
            flash("Banimento levantado. A conta voltou a estar ativa e os avisos anteriores foram anulados.", "success")
        return redirect(url_for("admin_supervision") + "#seguranca")

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
        # «Criar conta» a partir de um pedido de acesso: preenche o formulário da Câmara com os dados do pedido.
        prefill = {}
        if request.args.get("pedido", "").isdigit():
            r = db.execute("SELECT * FROM entity_requests WHERE id = ?", (int(request.args["pedido"]),)).fetchone()
            if r:
                prefill = {"district": r["district"], "municipality": r["municipality"], "name": r["contact_name"],
                           "email": r["email"], "phone": r["phone"] or ""}
        return render_template("admin/accounts.html", accounts=sv.staff_accounts(db), requests=sv.entity_requests(db),
                               organizations=sv.organizations_for_select(db), errors=errors,
                               form=request.form if request.method == "POST" else {},
                               camaras=sv.camaras(db), camara_form=session.pop("camara_form", None) or prefill,
                               camara_errors=session.pop("camara_errors", {}), territory=territory.options(),
                               test_allowed=test_accounts_allowed(), test_active=test_active,
                               test_plan=test_accounts_plan(), test_password=TEST_PASSWORD)

    @app.post("/admin/camaras")
    @role_required("admin")
    def admin_create_camara():
        """A plataforma cria a conta da Câmara Municipal de um concelho (Distrito → Concelho); o concelho fica ativo."""
        try:
            _uid, municipality = sv.create_camara_account(get_db(), request.form, hash_password)
            flash(f"Conta da Câmara de {municipality} criada para {request.form.get('email', '').strip().lower()}. "
                  f"O concelho de {municipality} está ativo: as ocorrências de lá vão para esta Câmara. "
                  "Entregue a palavra-passe temporária à pessoa (pode mudá-la em «A minha conta»).", "success")
        except sv.ValidationError as exc:
            session["camara_errors"] = exc.errors
            session["camara_form"] = {k: request.form.get(k, "") for k in ("district", "municipality", "name", "email", "phone")}
            flash("Verifique os campos assinalados.", "danger")
        return redirect(url_for("admin_accounts") + "#camara")

    @app.post("/admin/pedidos/<int:request_id>/tratado")
    @role_required("admin")
    def admin_close_request(request_id):
        sv.close_entity_request(get_db(), request_id)
        flash("Pedido marcado como tratado.", "info")
        return redirect(url_for("admin_accounts") + "#pedidos")

    @app.post("/admin/contas/converter")
    @role_required("admin")
    def admin_convert_account():
        """Transforma uma conta de cidadão já existente em conta de entidade (Câmara, Junta ou serviço)."""
        try:
            name = sv.convert_to_staff(get_db(), request.form)
            flash(f"A conta de {name} passou a ser uma conta de entidade. Deixa de poder comunicar como cidadão "
                  "e passa a tratar as ocorrências no painel.", "success")
        except sv.ValidationError as exc:
            flash(" ".join(exc.errors.values()), "danger")
        return redirect(url_for("admin_accounts") + "#converter")

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
                sv.set_avatar(get_db(), g.user, request.files.get("avatar"), app.config["MAX_AVATAR_BYTES"])
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
        return render_template("account.html", errors=errors, profile_form={}, has_password=bool(info["has_password"]), google=google_identity(db),
                               profile=profile_data(db, g.user))

    def profile_data(db, user):
        """Perfil ao estilo das redes sociais: números, nível e as ocorrências da pessoa em grelha."""
        if user["role"] != "citizen":
            org = user["organization_id"]
            counts = db.execute(
                """SELECT COALESCE(SUM(CASE WHEN status IN ('Registada', 'Em análise', 'Em tratamento') THEN 1 ELSE 0 END), 0),
                          COALESCE(SUM(CASE WHEN status = 'Resolvida' THEN 1 ELSE 0 END), 0)
                     FROM incidents WHERE deleted_at IS NULL AND organization_id = ?""", (org,)).fetchone() if org else (0, 0)
            return {"stats": [(counts[0], "em curso"), (counts[1], "resolvidas")], "level": None, "posts": []}
        rows = db.execute(
            """SELECT i.id, i.ticket, i.status, c.name AS category,
                      (SELECT MIN(m.id) FROM incident_images m WHERE m.incident_id = i.id AND m.kind = 'report') AS photo
                 FROM incidents i JOIN categories c ON c.id = i.category_id
                WHERE i.author_id = ? AND i.deleted_at IS NULL ORDER BY i.id DESC LIMIT 30""", (user["id"],)).fetchall()
        total = db.execute("SELECT COUNT(*) FROM incidents WHERE author_id = ? AND deleted_at IS NULL", (user["id"],)).fetchone()[0]
        solved = db.execute("SELECT COUNT(*) FROM incidents WHERE author_id = ? AND deleted_at IS NULL AND status = 'Resolvida'",
                            (user["id"],)).fetchone()[0]
        points = cm.user_points(db, user["id"])
        return {"stats": [(total, "ocorrência" if total == 1 else "ocorrências"), (solved, "resolvida" if solved == 1 else "resolvidas"),
                          (points, "pontos")],
                "level": cm.level_for(points),
                "posts": [dict(r, slug=sv.status_slug(r["status"]), **cm.category_style(r["category"])) for r in rows]}

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
            return render_template("account.html", errors=exc.errors, profile_form=request.form, google=google_identity(db),
                                   has_password=bool(info["has_password"]), profile=profile_data(db, g.user)), 400

    # ------------------------------------------------------------ Definições e ajuda
    @app.get("/definicoes")
    def settings(errors=None):
        """Aparência e acessibilidade (para todos, guardadas no próprio aparelho); notificações e privacidade (com conta)."""
        ctx = {"errors": errors or {}}
        if g.user:
            db = get_db()
            ctx["followed"] = set(sv.followed_parishes(db, g.user["id"]))
            ctx["parishes"] = sorted(((c, reference.short_name(n)) for c, n in reference.PARISHES), key=lambda p: p[1])
            ctx["scope"] = g.user["alert_scope"]
            ctx["pretty_phone"] = sms.pretty_phone
            ctx["territory"] = territory.options()
        return render_template("settings.html", **ctx)

    @app.post("/definicoes/notificacoes")
    @login_required
    def settings_notifications():
        try:
            sv.set_notification_prefs(get_db(), g.user, request.form)
            flash("Definições de notificações guardadas.", "success")
        except sv.ValidationError as exc:
            load_logged_in_user()
            return settings(errors=exc.errors), 400
        return redirect(url_for("settings") + "#notificacoes")

    @app.get("/definicoes/dados")
    @login_required
    def settings_export():
        data = sv.export_user_data(get_db(), g.user)
        body = json.dumps(data, ensure_ascii=False, indent=2)
        return app.response_class(body, mimetype="application/json", headers={
            "Content-Disposition": f'attachment; filename="townlyup-os-meus-dados-{datetime.now():%Y%m%d}.json"'})

    @app.post("/definicoes/apagar-conta")
    @login_required
    def settings_delete_account():
        try:
            sv.delete_own_account(get_db(), g.user, request.form, verify_password)
        except sv.ValidationError as exc:
            return settings(errors=exc.errors), 400
        logout_user()
        flash("A sua conta foi apagada. Obrigado por ter ajudado o concelho.", "info")
        return redirect(url_for("index"))

    @app.get("/ajuda")
    def help_page():
        return render_template("pages/help.html")

    # ------------------------------------------------------------ Painel «Configurações» do menu (perfil rápido)
    @app.post("/conta/painel")
    @login_required
    def account_panel():
        """Guarda o painel do menu: nome, email (pede a palavra-passe se mudar), freguesia e notificações."""
        db = get_db()
        back = request.form.get("next")
        back = back if is_safe_next(back) else url_for("index")
        try:
            sv.update_profile(db, g.user, request.form, verify_password)
        except sv.ValidationError as exc:
            flash("Não foi possível guardar: " + " ".join(exc.errors.values()), "danger")
            return redirect(back)
        with db:
            if g.user["role"] == "citizen":
                code = request.form.get("parish_code", "")
                code = code if code in sv.PARISH_CODES else None
                db.execute("UPDATE users SET parish_code = ?, home_parish = COALESCE(?, home_parish), email_receipts = ? WHERE id = ?",
                           (code, code, request.form.get("notify") == "1", g.user["id"]))
            elif g.user["role"] == "staff":
                db.execute("UPDATE users SET sms_alerts = ? WHERE id = ?", (request.form.get("notify") == "1", g.user["id"]))
        flash("Alterações guardadas.", "success")
        return redirect(back)

    @app.post("/conta/avisos/visto")
    @login_required
    def warnings_seen():
        sv.mark_warnings_seen(get_db(), g.user["id"])
        back = request.form.get("next")
        return redirect(back if is_safe_next(back) else url_for("account_activity"))

    @app.post("/conta/concelho")
    @login_required
    def change_territory():
        db = get_db()
        back = request.form.get("next")
        back = back if is_safe_next(back) else url_for("settings")
        try:
            municipality = sv.set_territory(db, g.user, request.form, app.config.get("GEO_CACHE_DIR") or app.instance_path)
        except sv.ValidationError as exc:
            flash(" ".join(exc.errors.values()), "danger")
            return redirect(back)
        if territory.is_active(municipality):
            flash(f"Concelho atualizado: {municipality}.", "success")
        else:
            flash(f"Concelho atualizado: {municipality}. O Townly Up ainda não está disponível neste concelho.", "info")
        return redirect(back)

    @app.get("/conta/atividade")
    @login_required
    def account_activity():
        """Atividade da conta: acessos, ocorrências, confirmações e pontos (mais recentes primeiro)."""
        db = get_db()
        uid = g.user["id"]
        user = db.execute("SELECT created_at, last_login_at FROM users WHERE id = ?", (uid,)).fetchone()
        events = []
        for r in db.execute("SELECT id, ticket, title, created_at FROM incidents WHERE author_id = ? AND deleted_at IS NULL "
                            "ORDER BY id DESC LIMIT 30", (uid,)):
            events.append({"at": r["created_at"], "kind": "report", "text": f"Comunicou {r['ticket']} — {r['title']}",
                           "url": url_for("incident_detail", incident_id=r["id"])})
        for r in db.execute("""SELECT f.kind, f.created_at, i.ticket FROM confirmations f JOIN incidents i ON i.id = f.incident_id
                               WHERE f.user_id = ? ORDER BY f.id DESC LIMIT 30""", (uid,)):
            events.append({"at": r["created_at"], "kind": "confirm",
                           "text": ("Confirmou «Eu também vi» em " if r["kind"] == "seen" else "Indicou que está resolvida: ") + r["ticket"]})
        for r in db.execute("""SELECT h.new_status, h.changed_at, i.ticket, i.id FROM status_history h JOIN incidents i ON i.id = h.incident_id
                               WHERE h.actor_id = ? AND h.previous_status IS NOT NULL ORDER BY h.id DESC LIMIT 30""", (uid,)):
            events.append({"at": r["changed_at"], "kind": "status", "text": f"Mudou {r['ticket']} para «{r['new_status']}»",
                           "url": url_for("admin_incident", incident_id=r["id"]) if g.user["role"] != "citizen" else None})
        events.sort(key=lambda e: str(e["at"]), reverse=True)
        points = cm.recent_points(db, uid, limit=15) if g.user["role"] == "citizen" else []
        return render_template("account_activity.html", user=user, events=events[:40], points=points,
                               warnings=sv.warnings_for(db, uid))

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

    @app.cli.command("apagar-tudo")
    @click.option("--sim", is_flag=True, help="Não pedir confirmação (só para testes).")
    def wipe_everything(sim):
        """APAGA todas as contas (incluindo a principal) e tudo o que criaram: ocorrências, fotografias, alertas, pontos.
        Ficam só as freguesias, entidades e categorias. Não se pode desfazer."""
        init_db()
        db = get_db()
        n_users = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        n_inc = db.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
        click.echo(f"Vai apagar {n_users} conta(s) e {n_inc} ocorrência(s), com fotografias, alertas e pontos.")
        if not sim and click.prompt("Escreva APAGAR para confirmar", default="", show_default=False).strip() != "APAGAR":
            click.echo("Cancelado — nada foi apagado.")
            raise SystemExit(1)
        wipe_all(db)
        for folder in ("outbox", "sms_outbox"):
            path = os.path.join(app.instance_path, folder)
            if os.path.isdir(path):
                for name in os.listdir(path):
                    os.remove(os.path.join(path, name))
        click.echo(f"Apagado: {n_users} conta(s) e {n_inc} ocorrência(s). Os próximos tickets recomeçam em TU-AAAA-0001.")

    @app.cli.command("apagar-contas-camara")
    @click.option("--sim", is_flag=True, help="Não pedir confirmação (só para testes).")
    def wipe_council_accounts(sim):
        """APAGA todas as contas da Câmara e das entidades (incluindo a conta principal). As contas dos munícipes e as
        ocorrências ficam. Depois crie a nova conta principal com: flask --app app repor-admin"""
        init_db()
        db = get_db()
        rows = db.execute("SELECT email, role FROM users WHERE role IN ('admin', 'staff') AND deleted_at IS NULL ORDER BY id").fetchall()
        if not rows:
            click.echo("Não há contas da Câmara para apagar.")
            return
        for r in rows:
            click.echo(f"  {r['email']}  ({'conta principal' if r['role'] == 'admin' else 'entidade'})")
        if not sim and click.prompt(f"Vai apagar {len(rows)} conta(s). Escreva APAGAR para confirmar", default="", show_default=False).strip() != "APAGAR":
            click.echo("Cancelado — nada foi apagado.")
            raise SystemExit(1)
        gone = sv.delete_council_accounts(db)
        click.echo(f"Apagadas {len(gone)} conta(s). Para criar a nova conta principal: duplo clique em repor_conta_principal.command")

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


USER_DATA_TABLES = ("notifications", "points_log", "confirmations", "status_history", "incident_images", "resolution_ratings",
                    "moderation_events", "user_warnings", "user_parishes", "entity_requests", "incidents", "users")


def wipe_all(db):
    """Apaga todos os dados criados por pessoas (pela ordem das chaves estrangeiras) e reinicia a numeração."""
    with db:
        if db.dialect == "postgres":
            db.execute("TRUNCATE " + ", ".join(USER_DATA_TABLES) + " RESTART IDENTITY CASCADE")
        else:
            # As regras «histórico imutável» e «ocorrências não se apagam» são triggers: suspendem-se só aqui.
            triggers = db.execute("SELECT name, sql FROM sqlite_master WHERE type = 'trigger' "
                                  "AND tbl_name IN ('status_history', 'incidents')").fetchall()
            for t in triggers:
                db.execute(f'DROP TRIGGER "{t["name"]}"')
            db.execute("UPDATE incidents SET similar_to = NULL")
            for table in USER_DATA_TABLES:
                db.execute(f"DELETE FROM {table}")
            for t in triggers:
                db.execute(t["sql"])
            db.execute("DELETE FROM sqlite_sequence WHERE name IN (" + ",".join("?" * len(USER_DATA_TABLES)) + ")",
                       USER_DATA_TABLES)


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
