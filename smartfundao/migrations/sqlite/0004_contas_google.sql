CREATE TABLE user_identities (
    id          INTEGER    PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER    NOT NULL REFERENCES users(id),
    provider    TEXT       NOT NULL CHECK (provider IN ('google')),
    subject     TEXT       NOT NULL CHECK (length(subject) BETWEEN 1 AND 255),
    email       TEXT       NOT NULL,
    created_at  TEXT       NOT NULL,
    UNIQUE (provider, subject),
    UNIQUE (user_id, provider)
);

ALTER TABLE users ADD COLUMN has_password BOOLEAN NOT NULL DEFAULT TRUE;
