-- Alertas da comunidade calculados na hora (feed), em vez de uma linha por pessoa e por ocorrência.
-- Com 10 000 munícipes, cada ocorrência criava 10 000 linhas (≈1,5 MB): o plano gratuito enchia em poucas centenas.
ALTER TABLE users ADD COLUMN alerts_seen_at TIMESTAMP;
DELETE FROM notifications WHERE kind = 'zone';
CREATE INDEX incidents_feed_idx ON incidents (created_at DESC) WHERE deleted_at IS NULL;
CREATE INDEX incidents_parish_feed_idx ON incidents (parish_code, created_at DESC) WHERE deleted_at IS NULL;
