"""Configuração dos testes: correm em SQLite (por defeito) ou em PostgreSQL.

    python -m pytest                                                   # SQLite
    TEST_DATABASE_URL=postgresql://user@localhost:5432/postgres python -m pytest   # PostgreSQL

Em PostgreSQL cada teste usa uma base de dados nova e descartável.
"""
import os
import sys
import uuid

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(TESTS_DIR))
sys.path.insert(0, TESTS_DIR)

import db as database  # noqa: E402

PG_ADMIN_URL = os.environ.get("TEST_DATABASE_URL")

if PG_ADMIN_URL:
    try:
        import psycopg  # noqa: F401  — driver real (no Mac)
    except ImportError:
        import pg_libpq  # alternativa só para testes (libpq via ctypes)
        database.pg_connect = pg_libpq.connect


@pytest.fixture
def database_config(tmp_path):
    """Configuração de base de dados para create_app()."""
    if not PG_ADMIN_URL:
        yield {"DATABASE": str(tmp_path / "test.db")}
        return
    name = "townly_test_" + uuid.uuid4().hex[:10]
    admin = database.Database(PG_ADMIN_URL)
    admin.execute(f'CREATE DATABASE "{name}"')
    url = PG_ADMIN_URL.rsplit("/", 1)[0] + "/" + name
    yield {"DATABASE_URL": url}
    admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    admin.close()
