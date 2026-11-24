-- «Continuar com o Google»: liga uma conta Google (identificador permanente «sub») a uma conta de munícipe.
CREATE TABLE IF NOT EXISTS user_identities (
    id          INTEGER    GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id     INTEGER    NOT NULL REFERENCES users(id),
    provider    TEXT       NOT NULL CHECK (provider IN ('google')),
    subject     TEXT       NOT NULL CHECK (length(subject) BETWEEN 1 AND 255),
    email       TEXT       NOT NULL,
    created_at  TIMESTAMP  NOT NULL,
    UNIQUE (provider, subject),
    UNIQUE (user_id, provider)
);
