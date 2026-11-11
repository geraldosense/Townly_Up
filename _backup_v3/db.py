"""Ligação à base de dados SQLite (uma ligação por pedido HTTP)."""
import sqlite3

from flask import current_app, g

CATEGORIAS_INICIAIS = [
    "Vias e pavimentos",
    "Iluminação pública",
    "Espaços verdes",
    "Limpeza urbana",
    "Mobiliário urbano",
    "Sinalização e trânsito",
    "Água e saneamento",
    "Outro",
]


def get_db():
    """Devolve a ligação do pedido atual (cria-a se ainda não existir)."""
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row  # permite aceder às colunas por nome
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    """Cria as tabelas (se não existirem) e as categorias iniciais."""
    db = get_db()
    with current_app.open_resource("schema.sql") as f:
        db.executescript(f.read().decode("utf-8"))
    # Migração simples: bases de dados antigas não tinham a coluna 'parish'.
    columns = {row[1] for row in db.execute("PRAGMA table_info(users)")}
    if "parish" not in columns:
        db.execute("ALTER TABLE users ADD COLUMN parish TEXT")
    with db:
        db.executemany(
            "INSERT OR IGNORE INTO categories (name) VALUES (?)",
            [(c,) for c in CATEGORIAS_INICIAIS],
        )
