-- Freguesia do utilizador em qualquer concelho do país (código DICOFRE da CAOP), usada para abrir o mapa nela.
ALTER TABLE users ADD COLUMN home_parish TEXT CHECK (home_parish IS NULL OR length(home_parish) BETWEEN 4 AND 12);
UPDATE users SET home_parish = parish_code WHERE parish_code IS NOT NULL;

-- Desempenho com dezenas de milhares de contas e ocorrências: índices para as consultas mais frequentes.
CREATE INDEX incidents_municipality_idx ON incidents (municipality, status);
CREATE INDEX incidents_author_created_idx ON incidents (author_id, created_at DESC);
CREATE INDEX confirmations_user_idx ON confirmations (user_id);
CREATE INDEX notifications_user_created_idx ON notifications (user_id, created_at DESC);
CREATE INDEX users_municipality_idx ON users (municipality);
CREATE INDEX users_role_idx ON users (role);
CREATE INDEX user_parishes_parish_idx ON user_parishes (parish_code);
CREATE INDEX images_incident_kind_idx ON incident_images (incident_id, kind);
