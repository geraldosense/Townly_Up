-- As fotografias passam a ficar guardadas na própria base de dados.
ALTER TABLE incident_images ADD COLUMN content_type TEXT
    CHECK (content_type IS NULL OR content_type IN ('image/jpeg', 'image/png', 'image/webp'));
ALTER TABLE incident_images ADD COLUMN data BLOB CHECK (data IS NULL OR length(data) <= 6291456);
