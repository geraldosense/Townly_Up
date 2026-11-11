ALTER TABLE incident_images ADD COLUMN phash TEXT CHECK (phash IS NULL OR (length(phash) = 16 AND phash NOT GLOB '*[^0-9a-f]*'));

CREATE TABLE moderation_events (
    id           INTEGER    PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER    NOT NULL REFERENCES users(id),
    reason       TEXT       NOT NULL CHECK (reason IN ('sexual', 'violencia', 'odio', 'sem_relacao')),
    description  TEXT,
    created_at   TEXT       NOT NULL
);
CREATE INDEX moderation_user_idx ON moderation_events (user_id, created_at);

ALTER TABLE incidents ADD COLUMN similar_to INTEGER REFERENCES incidents(id);
