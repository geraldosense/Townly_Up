-- Townly Up — esquema inicial (SQLite 3.35+, alternativa local ao PostgreSQL)
-- Mesma estrutura e regras que migrations/postgres/0001_esquema_inicial.sql.

CREATE TABLE parishes (
    code          TEXT  PRIMARY KEY CHECK (length(code) = 6 AND code NOT GLOB '*[^0-9]*'),
    name          TEXT  NOT NULL UNIQUE,
    short_name    TEXT  NOT NULL,
    landmark      TEXT,
    image_credit  TEXT
);

CREATE TABLE organizations (
    id           INTEGER  PRIMARY KEY AUTOINCREMENT,
    name         TEXT     NOT NULL UNIQUE,
    kind         TEXT     NOT NULL CHECK (kind IN ('camara', 'junta', 'servico')),
    parish_code  TEXT     REFERENCES parishes(code),
    service      TEXT,
    active       BOOLEAN  NOT NULL DEFAULT TRUE,
    CHECK (kind <> 'junta'   OR parish_code IS NOT NULL),
    CHECK (kind <> 'servico' OR service IS NOT NULL)
);
CREATE UNIQUE INDEX organizations_one_junta_per_parish ON organizations (parish_code) WHERE kind = 'junta';
CREATE UNIQUE INDEX organizations_one_per_service ON organizations (service) WHERE kind = 'servico';

CREATE TABLE users (
    id               INTEGER  PRIMARY KEY AUTOINCREMENT,
    name             TEXT     NOT NULL CHECK (length(name) BETWEEN 2 AND 100),
    email            TEXT     NOT NULL UNIQUE CHECK (email = lower(email) AND email LIKE '%_@_%._%'),
    password_hash    TEXT     NOT NULL,
    role             TEXT     NOT NULL DEFAULT 'citizen' CHECK (role IN ('citizen', 'staff', 'admin')),
    organization_id  INTEGER  REFERENCES organizations(id),
    parish_code      TEXT     REFERENCES parishes(code),
    active           BOOLEAN  NOT NULL DEFAULT TRUE,
    created_at       TEXT     NOT NULL,
    last_login_at    TEXT,
    CHECK ((role = 'citizen') = (organization_id IS NULL))
);

CREATE TABLE user_parishes (
    user_id      INTEGER  NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    parish_code  TEXT     NOT NULL REFERENCES parishes(code),
    PRIMARY KEY (user_id, parish_code)
);

CREATE TABLE categories (
    id               INTEGER  PRIMARY KEY AUTOINCREMENT,
    name             TEXT     NOT NULL UNIQUE,
    default_service  TEXT,
    active           BOOLEAN  NOT NULL DEFAULT TRUE
);

CREATE TABLE incidents (
    id               INTEGER  PRIMARY KEY AUTOINCREMENT,
    ticket           TEXT     UNIQUE CHECK (ticket GLOB 'TU-[0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9]*'),
    author_id        INTEGER  NOT NULL REFERENCES users(id),
    category_id      INTEGER  NOT NULL REFERENCES categories(id),
    parish_code      TEXT     REFERENCES parishes(code),
    organization_id  INTEGER  REFERENCES organizations(id),
    title            TEXT     NOT NULL CHECK (length(title) BETWEEN 3 AND 120),
    description      TEXT     NOT NULL CHECK (length(description) BETWEEN 10 AND 2000),
    latitude         REAL     NOT NULL CHECK (latitude  BETWEEN -90 AND 90),
    longitude        REAL     NOT NULL CHECK (longitude BETWEEN -180 AND 180),
    status           TEXT     NOT NULL DEFAULT 'Registada'
                     CHECK (status IN ('Registada', 'Em análise', 'Em tratamento', 'Resolvida', 'Rejeitada', 'Duplicada')),
    priority         TEXT     CHECK (priority IN ('Baixa', 'Média', 'Alta')),
    resolution_note  TEXT,
    assigned_to      INTEGER  REFERENCES users(id),
    created_at       TEXT     NOT NULL,
    updated_at       TEXT     NOT NULL,
    resolved_at      TEXT,
    CHECK (status <> 'Resolvida' OR resolved_at IS NOT NULL)
);

CREATE TABLE incident_images (
    id               INTEGER  PRIMARY KEY AUTOINCREMENT,
    incident_id      INTEGER  NOT NULL REFERENCES incidents(id),
    stored_filename  TEXT     NOT NULL UNIQUE,
    kind             TEXT     NOT NULL DEFAULT 'report' CHECK (kind IN ('report', 'resolution')),
    uploaded_at      TEXT     NOT NULL
);

CREATE TABLE status_history (
    id               INTEGER  PRIMARY KEY AUTOINCREMENT,
    incident_id      INTEGER  NOT NULL REFERENCES incidents(id),
    actor_id         INTEGER  NOT NULL REFERENCES users(id),
    previous_status  TEXT,
    new_status       TEXT     NOT NULL,
    note             TEXT     NOT NULL,
    changed_at       TEXT     NOT NULL
);

CREATE TABLE notifications (
    id           INTEGER  PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER  NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    incident_id  INTEGER  NOT NULL REFERENCES incidents(id),
    kind         TEXT     NOT NULL DEFAULT 'status' CHECK (kind IN ('status', 'zone', 'assigned')),
    message      TEXT     NOT NULL,
    is_read      BOOLEAN  NOT NULL DEFAULT FALSE,
    created_at   TEXT     NOT NULL
);

CREATE TABLE confirmations (
    id           INTEGER  PRIMARY KEY AUTOINCREMENT,
    incident_id  INTEGER  NOT NULL REFERENCES incidents(id),
    user_id      INTEGER  NOT NULL REFERENCES users(id),
    kind         TEXT     NOT NULL CHECK (kind IN ('seen', 'fixed')),
    created_at   TEXT     NOT NULL,
    UNIQUE (incident_id, user_id, kind)
);

CREATE TABLE points_log (
    id           INTEGER  PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER  NOT NULL REFERENCES users(id),
    incident_id  INTEGER  REFERENCES incidents(id),
    reason       TEXT     NOT NULL,
    points       INTEGER  NOT NULL CHECK (points > 0),
    created_at   TEXT     NOT NULL
);

CREATE INDEX incidents_author_idx        ON incidents (author_id);
CREATE INDEX incidents_status_idx        ON incidents (status);
CREATE INDEX incidents_parish_idx        ON incidents (parish_code);
CREATE INDEX incidents_org_idx           ON incidents (organization_id);
CREATE INDEX incidents_created_idx       ON incidents (created_at DESC);
CREATE INDEX incidents_geo_idx           ON incidents (latitude, longitude);
CREATE INDEX history_incident_idx        ON status_history (incident_id, changed_at);
CREATE INDEX images_incident_idx         ON incident_images (incident_id);
CREATE INDEX notifications_user_idx      ON notifications (user_id, is_read);
CREATE INDEX confirmations_incident_idx  ON confirmations (incident_id, kind);
CREATE INDEX points_user_idx             ON points_log (user_id);
CREATE INDEX users_org_idx               ON users (organization_id);

CREATE TRIGGER history_no_update BEFORE UPDATE ON status_history
BEGIN SELECT RAISE(ABORT, 'O historico e imutavel'); END;
CREATE TRIGGER history_no_delete BEFORE DELETE ON status_history
BEGIN SELECT RAISE(ABORT, 'O historico e imutavel'); END;
CREATE TRIGGER incidents_no_delete BEFORE DELETE ON incidents
BEGIN SELECT RAISE(ABORT, 'Ocorrencias nao podem ser apagadas'); END;
