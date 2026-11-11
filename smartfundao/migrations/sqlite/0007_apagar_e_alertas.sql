ALTER TABLE incidents ADD COLUMN deleted_at TEXT;
CREATE INDEX incidents_live_idx ON incidents (status) WHERE deleted_at IS NULL;
ALTER TABLE users ADD COLUMN alert_scope TEXT NOT NULL DEFAULT 'all' CHECK (alert_scope IN ('all', 'followed'));
