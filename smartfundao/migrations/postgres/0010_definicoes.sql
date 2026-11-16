-- Definições: comprovativo por email (pode ser desligado) e contas eliminadas pelo próprio (RGPD: dados anonimizados).
ALTER TABLE users ADD COLUMN email_receipts BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE users ADD COLUMN deleted_at TIMESTAMP;
