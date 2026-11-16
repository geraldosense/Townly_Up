-- Pedidos de acesso das entidades (Câmaras, Juntas, serviços): feitos em «Criar conta › Câmara / Junta»,
-- tratados pela conta principal em Painel › Contas das entidades.
CREATE TABLE entity_requests (
    id            INTEGER  GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind          TEXT     NOT NULL CHECK (kind IN ('camara', 'junta', 'servico')),
    district      TEXT     NOT NULL CHECK (length(district) <= 60),
    municipality  TEXT     NOT NULL CHECK (length(municipality) <= 60),
    entity_name   TEXT     NOT NULL CHECK (length(entity_name) BETWEEN 3 AND 120),
    contact_name  TEXT     NOT NULL CHECK (length(contact_name) BETWEEN 2 AND 100),
    email         TEXT     NOT NULL CHECK (length(email) <= 120),
    phone         TEXT     CHECK (phone IS NULL OR length(phone) <= 20),
    message       TEXT     CHECK (message IS NULL OR length(message) <= 600),
    status        TEXT     NOT NULL DEFAULT 'pendente' CHECK (status IN ('pendente', 'tratado')),
    created_at    TIMESTAMP      NOT NULL
);
CREATE INDEX entity_requests_status_idx ON entity_requests (status, created_at);
