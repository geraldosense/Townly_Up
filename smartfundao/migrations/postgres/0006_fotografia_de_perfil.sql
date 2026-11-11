-- Fotografia de perfil: guardada na base de dados (quadrada, 320×320, sem metadados).
ALTER TABLE users ADD COLUMN avatar BYTEA CHECK (avatar IS NULL OR octet_length(avatar) <= 524288);
ALTER TABLE users ADD COLUMN avatar_type TEXT CHECK (avatar_type IS NULL OR avatar_type IN ('image/jpeg', 'image/png', 'image/webp'));
ALTER TABLE users ADD COLUMN avatar_updated_at TIMESTAMP;
