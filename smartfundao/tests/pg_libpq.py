"""Ligação mínima ao PostgreSQL através da libpq (ctypes) — SÓ PARA TESTES.

Imita a pequena parte do psycopg 3 que db.py usa (cursor().execute, fetchone,
fetchall, description, rowcount, .sqlstate nos erros). A aplicação real usa o
psycopg; isto serve para correr os testes onde o psycopg não está instalado.
"""
import ctypes
import ctypes.util
import datetime
import decimal
import re

_lib = ctypes.CDLL(ctypes.util.find_library("pq") or "libpq.so.5")
_lib.PQconnectdb.restype = ctypes.c_void_p
_lib.PQconnectdb.argtypes = [ctypes.c_char_p]
_lib.PQstatus.argtypes = [ctypes.c_void_p]
_lib.PQerrorMessage.restype = ctypes.c_char_p
_lib.PQerrorMessage.argtypes = [ctypes.c_void_p]
_lib.PQfinish.argtypes = [ctypes.c_void_p]
_lib.PQexec.restype = ctypes.c_void_p
_lib.PQexec.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
_lib.PQexecParams.restype = ctypes.c_void_p
_lib.PQexecParams.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p,
                              ctypes.POINTER(ctypes.c_char_p), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int]
_lib.PQresultStatus.argtypes = [ctypes.c_void_p]
_lib.PQresultErrorField.restype = ctypes.c_char_p
_lib.PQresultErrorField.argtypes = [ctypes.c_void_p, ctypes.c_int]
_lib.PQresultErrorMessage.restype = ctypes.c_char_p
_lib.PQresultErrorMessage.argtypes = [ctypes.c_void_p]
for name in ("PQntuples", "PQnfields"):
    getattr(_lib, name).argtypes = [ctypes.c_void_p]
_lib.PQfname.restype = ctypes.c_char_p
_lib.PQfname.argtypes = [ctypes.c_void_p, ctypes.c_int]
_lib.PQftype.restype = ctypes.c_uint
_lib.PQftype.argtypes = [ctypes.c_void_p, ctypes.c_int]
_lib.PQgetvalue.restype = ctypes.c_char_p
_lib.PQgetvalue.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
_lib.PQgetisnull.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
_lib.PQcmdTuples.restype = ctypes.c_char_p
_lib.PQcmdTuples.argtypes = [ctypes.c_void_p]
_lib.PQclear.argtypes = [ctypes.c_void_p]

PGRES_COMMAND_OK, PGRES_TUPLES_OK = 1, 2
PG_DIAG_SQLSTATE = ord("C")


class Error(Exception):
    sqlstate = None


def _convert(oid, text):
    if oid == 16:
        return text == "t"
    if oid in (20, 21, 23):
        return int(text)
    if oid in (700, 701):
        return float(text)
    if oid == 1700:
        return decimal.Decimal(text)
    if oid == 17:  # bytea (formato hexadecimal \\x...)
        return bytes.fromhex(text[2:])
    if oid == 1114:
        return datetime.datetime.fromisoformat(text)
    return text


def _param(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return b"t" if value else b"f"
    if isinstance(value, (bytes, bytearray, memoryview)):
        return b"\\x" + bytes(value).hex().encode()
    return str(value).encode()


def _placeholders(sql):
    counter = iter(range(1, 10_000))
    sql = re.sub(r"%s", lambda m: f"${next(counter)}", sql)
    return sql.replace("%%", "%")


class Cursor:
    def __init__(self, conn):
        self.conn = conn
        self.description = None
        self.rowcount = -1
        self._rows = []

    def execute(self, sql, params=None):
        if params:
            values = [_param(v) for v in params]
            arr = (ctypes.c_char_p * len(values))(*values)
            res = _lib.PQexecParams(self.conn.pg, _placeholders(sql).encode(), len(values), None, arr, None, None, 0)
        elif params is not None:  # tuplo vazio: ainda assim converter %% -> %
            res = _lib.PQexecParams(self.conn.pg, _placeholders(sql).encode(), 0, None, None, None, None, 0)
        else:
            res = _lib.PQexec(self.conn.pg, sql.encode())
        try:
            status = _lib.PQresultStatus(res)
            if status not in (PGRES_COMMAND_OK, PGRES_TUPLES_OK):
                err = Error((_lib.PQresultErrorMessage(res) or b"").decode().strip())
                err.sqlstate = (_lib.PQresultErrorField(res, PG_DIAG_SQLSTATE) or b"").decode()
                raise err
            nf = _lib.PQnfields(res)
            if status == PGRES_TUPLES_OK:
                self.description = [(_lib.PQfname(res, i).decode(),) for i in range(nf)]
                types = [_lib.PQftype(res, i) for i in range(nf)]
                self._rows = [
                    tuple(None if _lib.PQgetisnull(res, r, c) else _convert(types[c], _lib.PQgetvalue(res, r, c).decode())
                          for c in range(nf))
                    for r in range(_lib.PQntuples(res))
                ]
            else:
                self.description, self._rows = None, []
            n = (_lib.PQcmdTuples(res) or b"").decode()
            self.rowcount = int(n) if n else -1
        finally:
            _lib.PQclear(res)

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None

    def fetchall(self):
        rows, self._rows = self._rows, []
        return rows


class Connection:
    def __init__(self, url):
        self.pg = _lib.PQconnectdb(url.encode())
        if _lib.PQstatus(self.pg) != 0:
            raise Error(_lib.PQerrorMessage(self.pg).decode())

    def cursor(self):
        return Cursor(self)

    def close(self):
        if self.pg:
            _lib.PQfinish(self.pg)
            self.pg = None


def connect(url):
    return Connection(url)
