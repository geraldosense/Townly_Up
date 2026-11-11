"""Autenticação, perfis e proteção CSRF.

- Passwords: hashing com Werkzeug (PBKDF2/scrypt com salt) — nunca texto simples (RNF02).
- Sessão: cookie assinado pelo Flask com SECRET_KEY.
- CSRF: cada formulário POST leva um token secreto guardado na sessão.
"""
import functools
import secrets

from flask import abort, current_app, flash, g, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from db import get_db

hash_password = generate_password_hash
verify_password = check_password_hash


# ---------------------------------------------------------------- CSRF
def csrf_token():
    """Token usado nos formulários: {{ csrf_token() }}."""
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)
    return session["_csrf"]


def check_csrf():
    """Executado antes de cada pedido POST. Recusa pedidos sem token válido."""
    if request.method != "POST" or not current_app.config.get("CSRF_ENABLED", True):
        return
    # Formulários enviam o token num campo; o JavaScript (fetch) envia-o no cabeçalho.
    sent = request.form.get("csrf_token", "") or request.headers.get("X-CSRF-Token", "")
    expected = session.get("_csrf", "")
    if not sent or not expected or not secrets.compare_digest(sent, expected):
        abort(400, description="O formulário expirou. Atualize a página e tente novamente.")


# ---------------------------------------------------------------- Sessão
def load_logged_in_user():
    """Coloca o utilizador autenticado em g.user (ou None)."""
    user_id = session.get("user_id")
    g.user = None
    if user_id is not None:
        g.user = get_db().execute(
            """SELECT u.id, u.name, u.email, u.role, u.parish_code, u.organization_id,
                      o.name AS org_name, o.kind AS org_kind, o.parish_code AS org_parish
                 FROM users u LEFT JOIN organizations o ON o.id = u.organization_id
                WHERE u.id = ? AND u.active = TRUE""",
            (user_id,),
        ).fetchone()


def login_user(user):
    session.clear()  # evita reaproveitar uma sessão anterior
    session["user_id"] = user["id"]


def logout_user():
    session.clear()


# ---------------------------------------------------------------- Decoradores
def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            flash("Inicie sessão para continuar.", "warning")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def role_required(*roles):
    """RF02/RNF03: restringe a rota a um ou mais perfis ('citizen', 'staff', 'admin')."""

    def decorator(view):
        @functools.wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if g.user["role"] not in roles:
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator


def is_safe_next(target):
    """Evita redirecionamentos para sites externos depois do login."""
    return bool(target) and target.startswith("/") and not target.startswith("//")
