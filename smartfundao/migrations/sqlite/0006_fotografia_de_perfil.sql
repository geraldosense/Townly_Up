ALTER TABLE users ADD COLUMN avatar BLOB CHECK (avatar IS NULL OR length(avatar) <= 524288);
ALTER TABLE users ADD COLUMN avatar_type TEXT CHECK (avatar_type IS NULL OR avatar_type IN ('image/jpeg', 'image/png', 'image/webp'));
ALTER TABLE users ADD COLUMN avatar_updated_at TEXT;
