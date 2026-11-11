ALTER TABLE users ADD COLUMN alerts_seen_at TEXT;
DELETE FROM notifications WHERE kind = 'zone';
CREATE INDEX incidents_feed_idx ON incidents (created_at DESC) WHERE deleted_at IS NULL;
CREATE INDEX incidents_parish_feed_idx ON incidents (parish_code, created_at DESC) WHERE deleted_at IS NULL;
