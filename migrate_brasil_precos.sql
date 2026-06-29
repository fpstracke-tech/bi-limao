-- Migração: adaptar brasil_precos para fonte Notícias Agrícolas (CEASAs)
-- Executa no Supabase SQL Editor

-- 1. Remove tabela antiga
DROP TABLE IF EXISTS brasil_precos CASCADE;

-- 2. Recria com nova estrutura
CREATE TABLE brasil_precos (
    id              BIGSERIAL PRIMARY KEY,
    data            DATE            NOT NULL,              -- data do fechamento
    semana          SMALLINT,                              -- semana ISO derivada
    ano             SMALLINT,
    regiao          TEXT            NOT NULL,              -- "Ceasa - Campinas/SP" etc.
    tipo            TEXT            NOT NULL,              -- "Extra", "Especial", "15 a 18 DZ"
    preco_kg        NUMERIC(8, 4),                         -- R$/kg
    preco_4_5kg     NUMERIC(8, 2),                         -- R$/cx 4,5kg (calculado)
    extracted_at    TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    UNIQUE (data, regiao, tipo)
);

CREATE INDEX IF NOT EXISTS idx_brasil_precos_data   ON brasil_precos (data);
CREATE INDEX IF NOT EXISTS idx_brasil_precos_regiao ON brasil_precos (regiao);

-- 3. RLS: leitura pública
ALTER TABLE brasil_precos ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "leitura_publica_brasil_precos" ON brasil_precos;
CREATE POLICY "leitura_publica_brasil_precos"
    ON brasil_precos FOR SELECT USING (true);

-- 4. Recriar view v_ultima_atualizacao (foi dropada pelo CASCADE)
CREATE OR REPLACE VIEW v_ultima_atualizacao AS
SELECT 'brasil_precos'          AS tabela, MAX(extracted_at) AS ultima_atualizacao, COUNT(*) AS total_registros FROM brasil_precos
UNION ALL
SELECT 'chile_precos',                       MAX(extracted_at), COUNT(*) FROM chile_precos
UNION ALL
SELECT 'europa_precos',                      MAX(extracted_at), COUNT(*) FROM europa_precos
UNION ALL
SELECT 'containers',                         MAX(extracted_at), COUNT(*) FROM containers
UNION ALL
SELECT 'comexstat_exportacoes',              MAX(extracted_at), COUNT(*) FROM comexstat_exportacoes
UNION ALL
SELECT 'clima_brasil_atual',                 MAX(extracted_at), COUNT(*) FROM clima_brasil_atual
UNION ALL
SELECT 'clima_brasil_forecast',              MAX(extracted_at), COUNT(*) FROM clima_brasil_forecast
UNION ALL
SELECT 'clima_forecast',                     MAX(extracted_at), COUNT(*) FROM clima_forecast;
