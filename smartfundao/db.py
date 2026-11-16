"""Acesso à base de dados — PostgreSQL (principal) ou SQLite (alternativa local).

O motor é escolhido por DATABASE_URL:
    postgresql://utilizador@localhost:5432/townlyup   -> PostgreSQL (psycopg 3)
    sqlite:////caminho/para/townlyup.db                -> SQLite

O resto da aplicação escreve SQL com marcadores «?» e usa sempre esta API:
    db = get_db()
    db.execute(sql, params).fetchone() / .fetchall()
    with db:            # transação: ou grava tudo, ou nada
        ...
As linhas devolvidas aceitam linha["coluna"] e linha[0]; datas vêm como texto
'AAAA-MM-DD HH:MM:SS' (UTC) nos dois motores.
"""
import datetime
import decimal
import os
import sqlite3

from flask import current_app, g

TS_FMT = "%Y-%m-%d %H:%M:%S"
MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "migrations")


class IntegrityError(Exception):
    """Violação de uma regra da base de dados (UNIQUE, CHECK, FOREIGN KEY, trigger)."""


class Row(dict):
    """Linha: acesso por nome (row["name"]) e por posição (row[0])."""

    def __init__(self, columns, values):
        values = [_normalize(v) for v in values]
        super().__init__(zip(columns, values))
        self._values = values

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return super().__getitem__(key)


def _normalize(value):
    if isinstance(value, datetime.datetime):
        return value.strftime(TS_FMT)
    if isinstance(value, decimal.Decimal):
        return float(value)
    return value


class Cursor:
    def __init__(self, raw):
        self._raw = raw
        self._cols = [d[0] for d in raw.description] if raw.description else []

    def fetchone(self):
        row = self._raw.fetchone()
        return Row(self._cols, row) if row is not None else None

    def fetchall(self):
        return [Row(self._cols, r) for r in self._raw.fetchall()]

    def __iter__(self):
        return iter(self.fetchall())

    @property
    def rowcount(self):
        return self._raw.rowcount


class Database:
    """Ligação com a mesma interface para PostgreSQL e SQLite."""

    def __init__(self, url):
        self.url = url
        self._depth = 0
        if url.startswith(("postgres://", "postgresql://")):
            self.dialect = "postgres"
            self.conn = _pool_take(url)
        else:
            self.dialect = "sqlite"
            path = url.split("sqlite:///", 1)[-1] if url.startswith("sqlite:") else url
            self.conn = sqlite3.connect(path, isolation_level=None, timeout=10)  # autocommit; transações explícitas
            self.conn.execute("PRAGMA foreign_keys = ON")
            # Muitos acessos ao mesmo tempo: leituras não bloqueiam escritas (WAL) e espera-se em vez de falhar.
            self.conn.execute("PRAGMA busy_timeout = 10000")
            if path != ":memory:":
                self.conn.execute("PRAGMA journal_mode = WAL")
                self.conn.execute("PRAGMA synchronous = NORMAL")

    # ------------------------------------------------------------ SQL
    def _sql(self, sql):
        if self.dialect == "postgres":
            return sql.replace("%", "%%").replace("?", "%s")
        return sql

    def _wrap_error(self, exc):
        state = str(getattr(exc, "sqlstate", "") or "")
        if isinstance(exc, sqlite3.IntegrityError) or state.startswith("23"):
            return IntegrityError(str(exc).strip())
        return None

    def execute(self, sql, params=()):
        cur = self.conn.cursor()
        try:
            cur.execute(self._sql(sql), tuple(params))
        except Exception as exc:
            mapped = self._wrap_error(exc)
            if mapped:
                raise mapped from exc
            raise
        return Cursor(cur)

    def executemany(self, sql, seq):
        for params in seq:
            self.execute(sql, params)

    def executescript(self, script):
        if self.dialect == "sqlite":
            self.conn.executescript(script)
        else:
            self.conn.cursor().execute(script)

    def commit(self):
        """Compatibilidade: fora de «with db:» cada instrução já fica gravada."""

    # ------------------------------------------------------------ transações
    def __enter__(self):
        if self._depth == 0:
            self.conn.cursor().execute("BEGIN")
        self._depth += 1
        return self

    def __exit__(self, exc_type, exc, tb):
        self._depth -= 1
        if self._depth == 0:
            self.conn.cursor().execute("ROLLBACK" if exc_type else "COMMIT")
        return False

    def close(self):
        if self.dialect == "postgres" and self._depth == 0:
            _pool_give(self.url, self.conn)
        else:
            self.conn.close()


# ---------------------------------------------------------------- Reserva de ligações (PostgreSQL)
# Abrir uma ligação ao PostgreSQL custa dezenas de milissegundos. Com milhares de utilizadores, cada pedido
# reutiliza uma ligação já aberta (até POOL_SIZE em espera), em vez de abrir e fechar uma nova.
import threading as _threading

POOL_SIZE = int(os.environ.get("DB_POOL_SIZE", "10"))
_pools, _pool_lock = {}, _threading.Lock()


def _pool_take(url):
    with _pool_lock:
        free = _pools.get(url) or []
        while free:
            conn = free.pop()
            if not getattr(conn, "closed", False):
                return conn
    return pg_connect(url)


def _pool_give(url, conn):
    info = getattr(conn, "info", None)          # psycopg 3; outros controladores não ficam em reserva
    idle = info is not None and not getattr(conn, "closed", False) and getattr(info, "transaction_status", 1) == 0
    if idle and POOL_SIZE > 0:
        with _pool_lock:
            free = _pools.setdefault(url, [])
            if len(free) < POOL_SIZE:
                free.append(conn)
                return
    try:
        conn.close()
    except Exception:
        pass


def pg_connect(url):
    """Liga ao PostgreSQL com psycopg 3 (em modo autocommit; as transações são explícitas)."""
    import psycopg
    return psycopg.connect(url, autocommit=True)


# ---------------------------------------------------------------- Flask
def database_url(app):
    return app.config.get("DATABASE_URL") or "sqlite:///" + app.config["DATABASE"]


def get_db():
    if "db" not in g:
        g.db = Database(database_url(current_app))
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


# ---------------------------------------------------------------- Migrações
def _has_table(db, name):
    if db.dialect == "sqlite":
        sql = "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?"
    else:
        sql = "SELECT 1 FROM information_schema.tables WHERE table_schema = current_schema() AND table_name = ?"
    return db.execute(sql, (name,)).fetchone() is not None


def migrate(db):
    """Aplica, por ordem, as migrações de migrations/<motor>/ que ainda não foram aplicadas."""
    ts_type = "TIMESTAMP" if db.dialect == "postgres" else "TEXT"
    db.execute(f"CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at {ts_type} NOT NULL)")
    done = {r["version"] for r in db.execute("SELECT version FROM schema_migrations").fetchall()}
    if not done and _has_table(db, "incidents"):
        raise RuntimeError("A base de dados tem um esquema antigo (sem migrações). "
                           "Mude o ficheiro instance/townlyup.db de nome (ou apague-o) e volte a arrancar.")
    folder = os.path.join(MIGRATIONS_DIR, db.dialect)
    applied = []
    for name in sorted(f for f in os.listdir(folder) if f.endswith(".sql")):
        version = name.split("_", 1)[0]
        if version in done:
            continue
        with open(os.path.join(folder, name), encoding="utf-8") as fh:
            script = fh.read()
        now = datetime.datetime.now(datetime.timezone.utc).strftime(TS_FMT)
        if db.dialect == "sqlite":
            # executescript não aceita parâmetros: a transação vai dentro do próprio script.
            db.executescript(f"BEGIN;\n{script}\nINSERT INTO schema_migrations VALUES ('{version}', '{now}');\nCOMMIT;")
        else:
            with db:
                db.executescript(script)
                db.execute("INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)", (version, now))
        applied.append(name)
    return applied


def init_db():
    """Cria/atualiza o esquema e os dados de referência (freguesias, entidades, categorias)."""
    import reference  # dados de referência (evita importação circular)

    db = get_db()
    migrate(db)
    reference.seed(db)
    return db
