"""«Continuar com o Google» para os munícipes (OpenID Connect, fluxo authorization code + PKCE).

Porque é seguro:
  • state   — impede que outro site inicie o login por nós (CSRF);
  • nonce   — liga o ID token a ESTE pedido (impede repetições);
  • PKCE    — o código de autorização só serve a quem o pediu;
  • o ID token vem diretamente do Google por HTTPS (canal servidor-servidor), por isso a OpenID
    Connect permite validá-lo pelo TLS; mesmo assim confirmamos emissor, destinatário, validade,
    nonce e que o email foi verificado pelo Google.
Só munícipes entram com o Google. As contas das entidades usam email e palavra-passe criados pela Câmara.

Configuração (consola Google Cloud → APIs e serviços → Credenciais → ID de cliente OAuth «Aplicação Web»):
  GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
  URI de redirecionamento autorizado:  <endereço do site>/auth/google/callback
"""
import base64
import hashlib
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for

import reference
import services as sv
from db import get_db
from security import hash_password, login_user

bp = Blueprint("oauth", __name__)

DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"
FALLBACK = {
    "issuer": "https://accounts.google.com",
    "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
    "token_endpoint": "https://oauth2.googleapis.com/token",
}
ISSUERS = {"https://accounts.google.com", "accounts.google.com"}
_discovery = {"at": 0, "doc": None}


def enabled():
    return bool(os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET"))


def _ssl():
    from app import _ssl_context
    return _ssl_context()


def _http_json(url, data=None):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode() if data else None,
                                 headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=15, context=_ssl()) as resp:
        return json.load(resp)


def endpoints():
    """Endereços do Google lidos do documento de descoberta (guardado 1 dia)."""
    if _discovery["doc"] is None or time.time() - _discovery["at"] > 86400:
        try:
            _discovery["doc"] = {**FALLBACK, **_http_json(DISCOVERY_URL)}
        except (urllib.error.URLError, TimeoutError, ValueError):
            _discovery["doc"] = FALLBACK
        _discovery["at"] = time.time()
    return _discovery["doc"]


def _b64url(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _jwt_claims(token):
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


def _redirect_uri():
    return url_for("oauth.google_callback", _external=True)


# ------------------------------------------------------------------ 1) ir ao Google
@bp.get("/auth/google")
def google_login():
    if not enabled():
        flash("O início de sessão com o Google ainda não está configurado.", "warning")
        return redirect(url_for("login"))
    verifier = _b64url(secrets.token_bytes(32))
    session["google"] = {"state": secrets.token_urlsafe(24), "nonce": secrets.token_urlsafe(24),
                         "verifier": verifier, "next": request.args.get("next", "")}
    params = {
        "client_id": os.environ["GOOGLE_CLIENT_ID"],
        "response_type": "code",
        "scope": "openid email profile",
        "redirect_uri": _redirect_uri(),
        "state": session["google"]["state"],
        "nonce": session["google"]["nonce"],
        "code_challenge": _b64url(hashlib.sha256(verifier.encode()).digest()),
        "code_challenge_method": "S256",
        "prompt": "select_account",
    }
    return redirect(endpoints()["authorization_endpoint"] + "?" + urllib.parse.urlencode(params))


# ------------------------------------------------------------------ 2) voltar do Google
@bp.get("/auth/google/callback")
def google_callback():
    pending = session.pop("google", None)
    if not enabled() or pending is None:
        abort(400)
    if request.args.get("error"):
        flash("Início de sessão com o Google cancelado.", "info")
        return redirect(url_for("login"))
    if not secrets.compare_digest(request.args.get("state", ""), pending["state"]):
        abort(400)
    try:
        tokens = _http_json(endpoints()["token_endpoint"], {
            "code": request.args.get("code", ""),
            "client_id": os.environ["GOOGLE_CLIENT_ID"],
            "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
            "redirect_uri": _redirect_uri(),
            "grant_type": "authorization_code",
            "code_verifier": pending["verifier"],
        })
        claims = _jwt_claims(tokens["id_token"])
    except (urllib.error.URLError, TimeoutError, KeyError, ValueError, IndexError) as exc:
        current_app.logger.warning("Google: %s", exc)
        flash("Não foi possível entrar com o Google. Tente novamente.", "danger")
        return redirect(url_for("login"))

    if (claims.get("iss") not in ISSUERS or claims.get("aud") != os.environ["GOOGLE_CLIENT_ID"]
            or claims.get("exp", 0) < time.time() or claims.get("nonce") != pending["nonce"]):
        abort(400)
    if not claims.get("email_verified"):
        flash("O email desta conta Google não está verificado.", "danger")
        return redirect(url_for("login"))

    profile = {"sub": str(claims["sub"]), "email": claims["email"].strip().lower(),
               "name": (claims.get("name") or claims["email"].split("@")[0]).strip()[:100],
               "next": pending.get("next", "")}
    return _finish_google(profile)


def _finish_google(profile):
    db = get_db()
    user = db.execute(
        """SELECT u.* FROM user_identities i JOIN users u ON u.id = i.user_id
            WHERE i.provider = 'google' AND i.subject = ?""", (profile["sub"],)).fetchone()
    if user is None:
        user = db.execute("SELECT * FROM users WHERE email = ?", (profile["email"],)).fetchone()
        if user is not None:
            if user["role"] != "citizen":
                flash("Esta é uma conta de entidade: entre com o email e a palavra-passe.", "warning")
                return redirect(url_for("login"))
            # Conta de munícipe com o mesmo email (verificado pelo Google): liga as duas.
            with db:
                db.execute("""INSERT INTO user_identities (user_id, provider, subject, email, created_at)
                              VALUES (?, 'google', ?, ?, ?)""", (user["id"], profile["sub"], profile["email"], sv.utcnow()))
    if user is None:
        # Conta nova: falta escolher a freguesia e aceitar as regras.
        session["google_profile"] = profile
        return redirect(url_for("oauth.google_complete"))
    if not user["active"]:
        flash("Esta conta está suspensa.", "danger")
        return redirect(url_for("login"))
    return _sign_in(user, profile.get("next"))


def _sign_in(user, target=None):
    from security import is_safe_next
    db = get_db()
    with db:
        db.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (sv.utcnow(), user["id"]))
    login_user(user)
    flash(f"Sessão iniciada com o Google. Olá, {user['name'].split()[0]}!", "success")
    return redirect(target if is_safe_next(target) else url_for("citizen_dashboard"))


# ------------------------------------------------------------------ 3) 1.ª vez: concluir o registo
@bp.route("/auth/google/concluir", methods=["GET", "POST"])
def google_complete():
    profile = session.get("google_profile")
    if not profile:
        return redirect(url_for("login"))
    errors = {}
    if request.method == "POST":
        parish = request.form.get("parish", "").strip() or None
        if parish is not None and parish not in sv.PARISH_CODES:
            errors["parish"] = "Escolha uma freguesia da lista."
        if not request.form.get("terms"):
            errors["terms"] = "Tem de aceitar as regras de utilização."
        if not errors:
            db = get_db()
            if db.execute("SELECT 1 FROM users WHERE email = ?", (profile["email"],)).fetchone():
                session.pop("google_profile", None)
                return redirect(url_for("oauth.google_login"))
            # Sem palavra-passe: a conta entra pelo Google (pode definir uma depois em «A minha conta»).
            user_id = sv.create_user(db, profile["name"], profile["email"], hash_password(secrets.token_urlsafe(32)),
                                     parish=parish)
            with db:
                db.execute("UPDATE users SET has_password = FALSE WHERE id = ?", (user_id,))
                db.execute("""INSERT INTO user_identities (user_id, provider, subject, email, created_at)
                              VALUES (?, 'google', ?, ?, ?)""", (user_id, profile["sub"], profile["email"], sv.utcnow()))
            session.pop("google_profile", None)
            return _sign_in(db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone(), profile.get("next"))
    parishes = sorted(((c, reference.short_name(n)) for c, n in reference.PARISHES), key=lambda p: p[1])
    return render_template("google_complete.html", profile=profile, parishes=parishes, errors=errors,
                           form=request.form)
