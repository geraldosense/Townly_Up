-- Contas ligadas a fornecedores externos (por agora: Google). Um munícipe pode entrar com o Google
-- e/ou com email e palavra-passe. O «subject» é o identificador permanente da conta Google.
CREATE TABLE user_identities (
    id          INTEGER    GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id     INTEGER    NOT NULL REFERENCES users(id),
    provider    TEXT       NOT NULL CHECK (provider IN ('google')),
    subject     TEXT       NOT NULL CHECK (length(subject) BETWEEN 1 AND 255),
    email       TEXT       NOT NULL,
    created_at  TIMESTAMP  NOT NULL,
    UNIQUE (provider, subject),
    UNIQUE (user_id, provider)
);

-- Contas criadas com o Google ainda não têm palavra-passe própria.
ALTER TABLE users ADD COLUMN has_password BOOLEAN NOT NULL DEFAULT TRUE;
