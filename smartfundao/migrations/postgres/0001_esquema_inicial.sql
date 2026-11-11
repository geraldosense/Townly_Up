-- Townly Up — esquema inicial (PostgreSQL 13+)
-- Datas em UTC (TIMESTAMP sem fuso). Todas as regras importantes estão
-- também na base de dados (CHECK, FOREIGN KEY, UNIQUE, triggers), não só no código.

-- ------------------------------------------------------------------ Território
CREATE TABLE parishes (
    code          CHAR(6)      PRIMARY KEY,               -- DICOFRE (CAOP / DGT)
    name          TEXT         NOT NULL UNIQUE,           -- nome oficial
    short_name    TEXT         NOT NULL,                  -- sem «União das freguesias de»
    landmark      TEXT,                                   -- ponto principal (legenda da imagem)
    image_credit  TEXT,
    CONSTRAINT parishes_code_format CHECK (code ~ '^[0-9]{6}$')
);

-- ------------------------------------------------------------------ Entidades recetoras
-- camara = central municipal; junta = Junta de Freguesia; servico = serviço técnico.
CREATE TABLE organizations (
    id           INTEGER      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name         TEXT         NOT NULL UNIQUE,
    kind         TEXT         NOT NULL CHECK (kind IN ('camara', 'junta', 'servico')),
    parish_code  CHAR(6)      REFERENCES parishes(code),
    service      TEXT,
    active       BOOLEAN      NOT NULL DEFAULT TRUE,
    CONSTRAINT org_junta_tem_freguesia  CHECK (kind <> 'junta'   OR parish_code IS NOT NULL),
    CONSTRAINT org_servico_tem_servico  CHECK (kind <> 'servico' OR service IS NOT NULL)
);
CREATE UNIQUE INDEX organizations_one_junta_per_parish ON organizations (parish_code) WHERE kind = 'junta';
CREATE UNIQUE INDEX organizations_one_per_service ON organizations (service) WHERE kind = 'servico';

-- ------------------------------------------------------------------ Utilizadores
-- citizen = munícipe; staff = técnico de uma entidade; admin = central municipal.
CREATE TABLE users (
    id               INTEGER     GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name             TEXT        NOT NULL CHECK (length(name) BETWEEN 2 AND 100),
    email            TEXT        NOT NULL UNIQUE CHECK (email = lower(email) AND email LIKE '%_@_%._%'),
    password_hash    TEXT        NOT NULL,
    role             TEXT        NOT NULL DEFAULT 'citizen' CHECK (role IN ('citizen', 'staff', 'admin')),
    organization_id  INTEGER     REFERENCES organizations(id),
    parish_code      CHAR(6)     REFERENCES parishes(code),
    active           BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at       TIMESTAMP   NOT NULL,
    last_login_at    TIMESTAMP,
    CONSTRAINT users_staff_have_organization CHECK ((role = 'citizen') = (organization_id IS NULL))
);

-- Freguesias que cada pessoa segue (recebe alertas de novos problemas).
CREATE TABLE user_parishes (
    user_id      INTEGER  NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    parish_code  CHAR(6)  NOT NULL REFERENCES parishes(code),
    PRIMARY KEY (user_id, parish_code)
);

-- ------------------------------------------------------------------ Ocorrências
CREATE TABLE categories (
    id               INTEGER  GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name             TEXT     NOT NULL UNIQUE,
    default_service  TEXT,                      -- serviço técnico que recebe esta categoria
    active           BOOLEAN  NOT NULL DEFAULT TRUE
);

CREATE TABLE incidents (
    id               INTEGER       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,   -- nunca reutilizado
    ticket           TEXT          UNIQUE CHECK (ticket ~ '^TU-[0-9]{4}-[0-9]{4,}$'),
    author_id        INTEGER       NOT NULL REFERENCES users(id),
    category_id      INTEGER       NOT NULL REFERENCES categories(id),
    parish_code      CHAR(6)       REFERENCES parishes(code),
    organization_id  INTEGER       REFERENCES organizations(id),              -- entidade responsável
    title            TEXT          NOT NULL CHECK (length(title) BETWEEN 3 AND 120),
    description      TEXT          NOT NULL CHECK (length(description) BETWEEN 10 AND 2000),
    latitude         NUMERIC(9,6)  NOT NULL CHECK (latitude  BETWEEN -90 AND 90),
    longitude        NUMERIC(9,6)  NOT NULL CHECK (longitude BETWEEN -180 AND 180),
    status           TEXT          NOT NULL DEFAULT 'Registada'
                     CHECK (status IN ('Registada', 'Em análise', 'Em tratamento', 'Resolvida', 'Rejeitada', 'Duplicada')),
    priority         TEXT          CHECK (priority IN ('Baixa', 'Média', 'Alta')),
    resolution_note  TEXT,
    assigned_to      INTEGER       REFERENCES users(id),                      -- último técnico a tratar
    created_at       TIMESTAMP     NOT NULL,
    updated_at       TIMESTAMP     NOT NULL,
    resolved_at      TIMESTAMP,
    CONSTRAINT incidents_resolved_has_date CHECK (status <> 'Resolvida' OR resolved_at IS NOT NULL)
);

CREATE TABLE incident_images (
    id               INTEGER    GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id      INTEGER    NOT NULL REFERENCES incidents(id),
    stored_filename  TEXT       NOT NULL UNIQUE,
    kind             TEXT       NOT NULL DEFAULT 'report' CHECK (kind IN ('report', 'resolution')),
    uploaded_at      TIMESTAMP  NOT NULL
);

CREATE TABLE status_history (
    id               INTEGER    GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id      INTEGER    NOT NULL REFERENCES incidents(id),
    actor_id         INTEGER    NOT NULL REFERENCES users(id),
    previous_status  TEXT,
    new_status       TEXT       NOT NULL,
    note             TEXT       NOT NULL,
    changed_at       TIMESTAMP  NOT NULL
);

-- ------------------------------------------------------------------ Comunidade
CREATE TABLE notifications (
    id           INTEGER    GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id      INTEGER    NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    incident_id  INTEGER    NOT NULL REFERENCES incidents(id),
    kind         TEXT       NOT NULL DEFAULT 'status' CHECK (kind IN ('status', 'zone', 'assigned')),
    message      TEXT       NOT NULL,
    is_read      BOOLEAN    NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMP  NOT NULL
);

CREATE TABLE confirmations (
    id           INTEGER    GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id  INTEGER    NOT NULL REFERENCES incidents(id),
    user_id      INTEGER    NOT NULL REFERENCES users(id),
    kind         TEXT       NOT NULL CHECK (kind IN ('seen', 'fixed')),
    created_at   TIMESTAMP  NOT NULL,
    UNIQUE (incident_id, user_id, kind)
);

CREATE TABLE points_log (
    id           INTEGER    GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id      INTEGER    NOT NULL REFERENCES users(id),
    incident_id  INTEGER    REFERENCES incidents(id),
    reason       TEXT       NOT NULL,
    points       INTEGER    NOT NULL CHECK (points > 0),
    created_at   TIMESTAMP  NOT NULL
);

-- ------------------------------------------------------------------ Índices
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

-- ------------------------------------------------------------------ Regras na própria base de dados
-- RF10: o histórico é imutável.
CREATE FUNCTION townly_history_is_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'O historico e imutavel' USING ERRCODE = 'integrity_constraint_violation';
END $$;
CREATE TRIGGER history_no_update BEFORE UPDATE OR DELETE ON status_history
    FOR EACH ROW EXECUTE FUNCTION townly_history_is_immutable();

-- RF11: ocorrências arquivam-se, nunca se apagam.
CREATE FUNCTION townly_incidents_not_deletable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Ocorrencias nao podem ser apagadas' USING ERRCODE = 'integrity_constraint_violation';
END $$;
CREATE TRIGGER incidents_no_delete BEFORE DELETE ON incidents
    FOR EACH ROW EXECUTE FUNCTION townly_incidents_not_deletable();
