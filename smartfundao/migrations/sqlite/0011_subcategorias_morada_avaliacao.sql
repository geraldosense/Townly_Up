-- Subcategorias (tipo → subtipo), morada da rua e avaliação da resolução pelo munícipe.
CREATE TABLE subcategories (
    id           INTEGER  PRIMARY KEY AUTOINCREMENT,
    category_id  INTEGER  NOT NULL REFERENCES categories(id),
    name         TEXT     NOT NULL,
    position     INTEGER  NOT NULL DEFAULT 0,
    active       BOOLEAN  NOT NULL DEFAULT TRUE,
    UNIQUE (category_id, name)
);
ALTER TABLE incidents ADD COLUMN subcategory_id INTEGER REFERENCES subcategories(id);
ALTER TABLE incidents ADD COLUMN address TEXT CHECK (address IS NULL OR length(address) <= 200);
CREATE TABLE resolution_ratings (
    id           INTEGER  PRIMARY KEY AUTOINCREMENT,
    incident_id  INTEGER  NOT NULL REFERENCES incidents(id),
    user_id      INTEGER  NOT NULL REFERENCES users(id),
    stars        INTEGER  NOT NULL CHECK (stars BETWEEN 1 AND 5),
    comment      TEXT     CHECK (comment IS NULL OR length(comment) <= 500),
    reopened     BOOLEAN  NOT NULL DEFAULT FALSE,
    created_at   TEXT     NOT NULL
);
CREATE INDEX ratings_incident_idx ON resolution_ratings (incident_id);
