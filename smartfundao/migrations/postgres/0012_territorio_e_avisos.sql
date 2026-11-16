-- Portugal inteiro: distrito e concelho de cada conta e de cada ocorrência.
-- Segurança: avisos formais (1.º ato) e banimento automático (2.º ato).
ALTER TABLE users ADD COLUMN district TEXT CHECK (district IS NULL OR length(district) <= 60);
ALTER TABLE users ADD COLUMN municipality TEXT CHECK (municipality IS NULL OR length(municipality) <= 60);
ALTER TABLE users ADD COLUMN banned_at TIMESTAMP;
ALTER TABLE users ADD COLUMN ban_reason TEXT CHECK (ban_reason IS NULL OR length(ban_reason) <= 300);
ALTER TABLE incidents ADD COLUMN municipality TEXT NOT NULL DEFAULT 'Fundão';
-- Até agora a plataforma era só do concelho do Fundão.
UPDATE users SET district = 'Castelo Branco', municipality = 'Fundão';

CREATE TABLE user_warnings (
    id               INTEGER  GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id          INTEGER  NOT NULL REFERENCES users(id),
    kind             TEXT     NOT NULL CHECK (kind IN ('linguagem', 'imagem')),
    context          TEXT     NOT NULL CHECK (length(context) <= 60),
    detail           TEXT     CHECK (detail IS NULL OR length(detail) <= 300),
    led_to_ban       BOOLEAN  NOT NULL DEFAULT FALSE,
    seen_at          TIMESTAMP,
    created_at       TIMESTAMP     NOT NULL
);
CREATE INDEX user_warnings_user_idx ON user_warnings (user_id, created_at);
