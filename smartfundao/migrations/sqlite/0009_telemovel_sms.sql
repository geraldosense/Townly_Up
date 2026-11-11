-- Telemóvel dos responsáveis (Juntas): recebem uma SMS por cada nova ocorrência na sua freguesia.
ALTER TABLE users ADD COLUMN phone TEXT;
ALTER TABLE users ADD COLUMN sms_alerts BOOLEAN NOT NULL DEFAULT TRUE;
