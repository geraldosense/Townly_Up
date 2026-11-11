-- Duplicados: «impressão digital» de cada fotografia (dHash de 64 bits, em hexadecimal).
ALTER TABLE incident_images ADD COLUMN phash TEXT CHECK (phash IS NULL OR phash ~ '^[0-9a-f]{16}$');

-- Moderação: fotografias recusadas automaticamente (para detetar abusos e suspender contas).
CREATE TABLE moderation_events (
    id           INTEGER    GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id      INTEGER    NOT NULL REFERENCES users(id),
    reason       TEXT       NOT NULL CHECK (reason IN ('sexual', 'violencia', 'odio', 'sem_relacao')),
    description  TEXT,
    created_at   TIMESTAMP  NOT NULL
);
CREATE INDEX moderation_user_idx ON moderation_events (user_id, created_at);

-- Duplicados confirmados pelo munícipe como «é outro problema» (fica registado para os técnicos).
ALTER TABLE incidents ADD COLUMN similar_to INTEGER REFERENCES incidents(id);
