-- O autor pode apagar a sua ocorrência enquanto ainda não está a ser tratada.
-- Por exigência do caderno de encargos (RF10/RF11) o registo e o histórico não desaparecem da base de dados:
-- a ocorrência fica marcada como apagada, deixa de aparecer em todo o lado e as fotografias são eliminadas.
ALTER TABLE incidents ADD COLUMN deleted_at TIMESTAMP;
CREATE INDEX incidents_live_idx ON incidents (status) WHERE deleted_at IS NULL;

-- Alertas de novos problemas: de todo o concelho (por omissão) ou só das freguesias que segue.
ALTER TABLE users ADD COLUMN alert_scope TEXT NOT NULL DEFAULT 'all' CHECK (alert_scope IN ('all', 'followed'));
