-- =============================================================================
-- BI Limão — tabela ceagesp_precos (série ALTERNATIVA ao HF Brasil/Cepea)
-- Rodar UMA vez no SQL Editor do Supabase (projeto do BI Limão).
--
-- Contexto (17/11/2026): HF Brasil bloqueou o acesso automatizado (Cloudflare)
-- e a licença do Cepea é CC BY-NC. A cotação pública da CEAGESP entra como
-- referência de atacado em SP. Tabela própria — NÃO gravar em brasil_precos:
-- a view de status usa MAX(extracted_at) e a CEAGESP mascararia o atraso do HF.
-- =============================================================================

CREATE TABLE IF NOT EXISTS ceagesp_precos (
    id                 BIGSERIAL PRIMARY KEY,
    data               DATE        NOT NULL,
    semana             INT         NOT NULL,
    ano                INT         NOT NULL,
    produto            TEXT        NOT NULL DEFAULT 'LIMÃO TAITI',
    classificacao      TEXT        NOT NULL,          -- 15 A 18 DZ | 21 A 27 DZ | ACIMA DE 31 DZ
    unidade            TEXT,                          -- KG
    preco_min          NUMERIC(10,4),                 -- R$/kg
    preco_comum        NUMERIC(10,4) NOT NULL,        -- R$/kg (preço mais frequente)
    preco_max          NUMERIC(10,4),                 -- R$/kg
    preco_comum_4_5kg  NUMERIC(10,4),                 -- preco_comum × 4,5 (mesma unidade da aba Brasil)
    extracted_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ceagesp_precos_data_classif_uk UNIQUE (data, classificacao)
);

CREATE INDEX IF NOT EXISTS ceagesp_precos_ano_semana_idx ON ceagesp_precos (ano, semana);

-- Leitura pública (dashboard usa a anon key); escrita só service_role.
ALTER TABLE ceagesp_precos ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "leitura publica ceagesp_precos" ON ceagesp_precos;
CREATE POLICY "leitura publica ceagesp_precos"
    ON ceagesp_precos FOR SELECT TO anon, authenticated USING (true);
GRANT SELECT ON ceagesp_precos TO anon, authenticated;

-- Status: definição vigente de v_ultima_atualizacao + ceagesp_precos
CREATE OR REPLACE VIEW v_ultima_atualizacao AS
SELECT 'brasil_precos'          AS tabela, MAX(extracted_at) AS ultima_atualizacao, COUNT(*) AS total_registros FROM brasil_precos
UNION ALL
SELECT 'ceagesp_precos',                   MAX(extracted_at), COUNT(*) FROM ceagesp_precos
UNION ALL
SELECT 'chile_precos',                     MAX(extracted_at), COUNT(*) FROM chile_precos
UNION ALL
SELECT 'europa_precos',                    MAX(extracted_at), COUNT(*) FROM europa_precos
UNION ALL
SELECT 'mexico_precos',                    MAX(extracted_at), COUNT(*) FROM mexico_precos
UNION ALL
SELECT 'colombia_precos',                  MAX(extracted_at), COUNT(*) FROM colombia_precos
UNION ALL
SELECT 'peru_precos',                      MAX(extracted_at), COUNT(*) FROM peru_precos
UNION ALL
SELECT 'containers',                       MAX(extracted_at), COUNT(*) FROM containers
UNION ALL
SELECT 'comexstat_exportacoes',            MAX(extracted_at), COUNT(*) FROM comexstat_exportacoes
UNION ALL
SELECT 'clima_brasil_atual',               MAX(extracted_at), COUNT(*) FROM clima_brasil_atual
UNION ALL
SELECT 'clima_brasil_forecast',            MAX(extracted_at), COUNT(*) FROM clima_brasil_forecast
UNION ALL
SELECT 'clima_forecast',                   MAX(extracted_at), COUNT(*) FROM clima_forecast;

-- Conferência: 12 linhas, ceagesp_precos com 0 registros até o 1º ETL.
SELECT * FROM v_ultima_atualizacao ORDER BY tabela;
