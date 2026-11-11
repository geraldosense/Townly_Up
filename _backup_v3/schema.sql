-- SmartFundão — esquema da base de dados (SQLite)
-- Datas guardadas em UTC no formato 'AAAA-MM-DD HH:MM:SS'.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL,
    email         TEXT    NOT NULL UNIQUE,
    password_hash TEXT    NOT NULL,
    role          TEXT    NOT NULL DEFAULT 'citizen' CHECK (role IN ('citizen', 'admin')),
    parish        TEXT,
    created_at    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS categories (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    name   TEXT    NOT NULL UNIQUE,
    active INTEGER NOT NULL DEFAULT 1
);

-- AUTOINCREMENT garante que um id (e portanto um ticket) nunca é reutilizado.
CREATE TABLE IF NOT EXISTS incidents (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket          TEXT    UNIQUE,
    author_id       INTEGER NOT NULL REFERENCES users(id),
    category_id     INTEGER NOT NULL REFERENCES categories(id),
    title           TEXT    NOT NULL,
    description     TEXT    NOT NULL,
    latitude        REAL    NOT NULL,
    longitude       REAL    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'Registada',
    priority        TEXT,
    service         TEXT,
    resolution_note TEXT,
    assigned_to     INTEGER REFERENCES users(id),
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL,
    resolved_at     TEXT
);

CREATE TABLE IF NOT EXISTS incident_images (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id     INTEGER NOT NULL REFERENCES incidents(id),
    stored_filename TEXT    NOT NULL UNIQUE,
    kind            TEXT    NOT NULL DEFAULT 'report' CHECK (kind IN ('report', 'resolution')),
    uploaded_at     TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS status_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id     INTEGER NOT NULL REFERENCES incidents(id),
    actor_id        INTEGER NOT NULL REFERENCES users(id),
    previous_status TEXT,
    new_status      TEXT    NOT NULL,
    note            TEXT    NOT NULL,
    changed_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    incident_id INTEGER NOT NULL REFERENCES incidents(id),
    message     TEXT    NOT NULL,
    is_read     INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL
);

-- Comunidade (estilo Waze): «Eu também vi» / «Já foi resolvido?»
CREATE TABLE IF NOT EXISTS confirmations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id INTEGER NOT NULL REFERENCES incidents(id),
    user_id     INTEGER NOT NULL REFERENCES users(id),
    kind        TEXT    NOT NULL CHECK (kind IN ('seen', 'fixed')),
    created_at  TEXT    NOT NULL,
    UNIQUE (incident_id, user_id, kind)          -- cada pessoa confirma uma vez
);

-- Pontos: registo de todas as atribuições (o total é a soma).
CREATE TABLE IF NOT EXISTS points_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    incident_id INTEGER REFERENCES incidents(id),
    reason      TEXT    NOT NULL,
    points      INTEGER NOT NULL,
    created_at  TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_confirmations_incident ON confirmations(incident_id, kind);
CREATE INDEX IF NOT EXISTS idx_points_user ON points_log(user_id);
CREATE INDEX IF NOT EXISTS idx_incidents_author ON incidents(author_id);
CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents(status);
CREATE INDEX IF NOT EXISTS idx_history_incident ON status_history(incident_id);
CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id, is_read);

-- RF10: o histórico é imutável — a própria base de dados recusa alterações.
CREATE TRIGGER IF NOT EXISTS history_no_update
BEFORE UPDATE ON status_history
BEGIN
    SELECT RAISE(ABORT, 'O historico e imutavel');
END;

CREATE TRIGGER IF NOT EXISTS history_no_delete
BEFORE DELETE ON status_history
BEGIN
    SELECT RAISE(ABORT, 'O historico e imutavel');
END;

-- RF11: ocorrências arquivam-se, nunca se apagam.
CREATE TRIGGER IF NOT EXISTS incidents_no_delete
BEFORE DELETE ON incidents
BEGIN
    SELECT RAISE(ABORT, 'Ocorrencias nao podem ser apagadas');
END;
