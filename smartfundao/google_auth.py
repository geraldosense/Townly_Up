"""«Continuar com o Google» (OpenID Connect, fluxo «authorization code»), sem bibliotecas extra.

Como funciona:
  1. /entrar/google      → guarda um «state» e um «nonce» aleatórios na sessão e envia a pessoa para o Google.
  2. O Google pede a conta e devolve-a a /entrar/google/retorno com um «code».
  3. O servidor troca o «code» pelo «id_token» diretamente com o Google (HTTPS, com o segredo da aplicação).
     Como o token vem diretamente do Google por TLS, a especificação OpenID permite usar o conteúdo sem validar
     a assinatura; validamos na mesma: emissor, destinatário (o nosso client_id), validade, nonce e email verificado.
  4. Liga-se à conta existente com o mesmo email (só munícipes) ou cria-se uma conta de munícipe nova.

Configuração (variáveis de ambiente, nunca no código): GOOGLE_CLIENT_ID e GOOGLE_CLIENT_SECRET.
"""
import base64
import json
import os
import secrets
import time
import urllib.parse
import urllib.request

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
ISSUERS = ("https://accounts.google.com", "accounts.google.com")


class GoogleError(Exception):
    """Erro com uma mensagem pronta a mostrar ao munícipe."""


def client_id():
    return os.environ.get("GOOGLE_CLIENT_ID", "").strip()


def client_secret():
    return os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()


def configured():
    return bool(client_id() and client_secret())


def new_state():
    return secrets.token_urlsafe(24), secrets.token_urlsafe(24)


def authorize_url(redirect_uri, state, nonce):
    params = {"client_id": client_id(), "redirect_uri": redirect_uri, "response_type": "code",
              "scope": "openid email profile", "state": state, "nonce": nonce, "prompt": "select_account"}
    return AUTH_URL + "?" + urllib.parse.urlencode(params)


def exchange_code(code, redirect_uri, ssl_context=None):
    """Troca o «code» pelo id_token, diretamente com o Google. Devolve as «claims» do id_token (dict)."""
    body = urllib.parse.urlencode({"code": code, "client_id": client_id(), "client_secret": client_secret(),
                                   "redirect_uri": redirect_uri, "grant_type": "authorization_code"}).encode()
    req = urllib.request.Request(TOKEN_URL, data=body, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=15, context=ssl_context) as resp:
            data = json.loads(resp.read().decode())
    except Exception:
        raise GoogleError("Não foi possível falar com o Google. Tente outra vez daqui a pouco.")
    token = data.get("id_token")
    if not token:
        raise GoogleError("O Google não devolveu a sua conta. Tente outra vez.")
    return decode_id_token(token)


def decode_id_token(token):
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload.encode()).decode())
    except Exception:
        raise GoogleError("Resposta do Google inválida. Tente outra vez.")


def check_claims(claims, nonce, now=None):
    """Confirma que o token é para esta aplicação, é recente, corresponde a este pedido e o email está verificado."""
    now = now or time.time()
    if claims.get("iss") not in ISSUERS or claims.get("aud") != client_id():
        raise GoogleError("Resposta do Google inválida. Tente outra vez.")
    if float(claims.get("exp", 0)) < now - 60:
        raise GoogleError("O pedido ao Google expirou. Tente outra vez.")
    if not nonce or claims.get("nonce") != nonce:
        raise GoogleError("O pedido ao Google expirou. Tente outra vez.")
    if not claims.get("sub"):
        raise GoogleError("Resposta do Google inválida. Tente outra vez.")
    email = (claims.get("email") or "").strip().lower()
    if not email or claims.get("email_verified") not in (True, "true"):
        raise GoogleError("A sua conta Google não tem um email confirmado. Confirme-o no Google ou crie conta com email.")
    return {"sub": str(claims["sub"]), "email": email,
            "name": " ".join((claims.get("name") or email.split("@")[0]).split())[:100]}


def find_or_create_user(db, info, now, create_user):
    """Devolve (utilizador, criado?). Liga a conta Google à conta de munícipe com o mesmo email ou cria uma nova.
    As contas da Câmara/entidades não entram com o Google (usam email e palavra-passe)."""
    row = db.execute("""SELECT u.* FROM user_identities i JOIN users u ON u.id = i.user_id
                         WHERE i.provider = 'google' AND i.subject = ?""", (info["sub"],)).fetchone()
    created = False
    if row is None:
        row = db.execute("SELECT * FROM users WHERE email = ?", (info["email"],)).fetchone()
        if row is not None and row["role"] != "citizen":
            raise GoogleError("Esta é uma conta de entidade (Câmara, Junta ou serviço). Entre no separador «Câmara» com a palavra-passe.")
        if row is None:
            user_id = create_user(db, info["name"], info["email"], "!", role="citizen")
            with db:
                db.execute("UPDATE users SET has_password = FALSE WHERE id = ?", (user_id,))
            row = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            created = True
        with db:
            db.execute("""INSERT INTO user_identities (user_id, provider, subject, email, created_at)
                          VALUES (?, 'google', ?, ?, ?) ON CONFLICT DO NOTHING""",
                       (row["id"], info["sub"], info["email"], now))
    if row["role"] != "citizen":
        raise GoogleError("Esta é uma conta de entidade (Câmara, Junta ou serviço). Entre no separador «Câmara» com a palavra-passe.")
    if row["banned_at"] if "banned_at" in row.keys() else False:
        raise GoogleError("Esta conta foi banida por desrespeitar duas vezes as regras da comunidade. "
                          "Se acha que foi um engano, contacte a Câmara Municipal.")
    if not row["active"]:
        raise GoogleError("Esta conta está desativada.")
    return row, created
