-- Uma Câmara Municipal por concelho (os 308): a conta principal (plataforma Townly Up) cria a conta da Câmara de
-- cada concelho e, a partir daí, as ocorrências desse concelho vão para essa Câmara. As entidades que já existiam
-- (Câmara, Juntas e serviços) são todas do concelho do Fundão.
ALTER TABLE organizations ADD COLUMN municipality TEXT;
UPDATE organizations SET municipality = 'Fundão' WHERE municipality IS NULL;
CREATE UNIQUE INDEX organizations_one_camara_per_municipality ON organizations (municipality) WHERE kind = 'camara';
