-- As fotografias passam a ficar guardadas na própria base de dados.
-- Assim não se perdem quando o servidor reinicia (alojamentos sem disco permanente).
ALTER TABLE incident_images ADD COLUMN content_type TEXT;
ALTER TABLE incident_images ADD COLUMN data BYTEA;
ALTER TABLE incident_images ADD CONSTRAINT images_size_ck CHECK (data IS NULL OR octet_length(data) <= 6291456);
ALTER TABLE incident_images ADD CONSTRAINT images_type_ck
    CHECK (content_type IS NULL OR content_type IN ('image/jpeg', 'image/png', 'image/webp'));
