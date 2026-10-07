-- =============================================================================
-- BI Limão — leitura pública nas tabelas novas de preços
-- Rodar no SQL Editor do Supabase, depois de migrate_precos_mexico_colombia_peru.sql
--
-- Por que: o dashboard lê o Supabase com a anon key. Sem política de SELECT
-- para anon, a REST responde 200 com lista VAZIA em vez de erro — a aba carrega
-- "sem dados" e nada no console indica o motivo. Conferido em 07/10/2026:
-- colombia_precos com 22.579 linhas gravadas pela service_role e 0 linha
-- visível pela anon key. Mesmo padrão já aplicado em chile_precos e nas demais.
-- A escrita continua exclusiva da service_role, que ignora RLS.
-- =============================================================================

ALTER TABLE mexico_precos   ENABLE ROW LEVEL SECURITY;
ALTER TABLE colombia_precos ENABLE ROW LEVEL SECURITY;
ALTER TABLE peru_precos     ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "leitura publica mexico_precos"   ON mexico_precos;
DROP POLICY IF EXISTS "leitura publica colombia_precos" ON colombia_precos;
DROP POLICY IF EXISTS "leitura publica peru_precos"     ON peru_precos;

CREATE POLICY "leitura publica mexico_precos"
    ON mexico_precos   FOR SELECT TO anon, authenticated USING (true);

CREATE POLICY "leitura publica colombia_precos"
    ON colombia_precos FOR SELECT TO anon, authenticated USING (true);

CREATE POLICY "leitura publica peru_precos"
    ON peru_precos     FOR SELECT TO anon, authenticated USING (true);

-- Garantia de grant (o papel anon precisa do SELECT além da política)
GRANT SELECT ON mexico_precos, colombia_precos, peru_precos TO anon, authenticated;

-- ── CONFERÊNCIA ──────────────────────────────────────────────────────────────
-- Esperado: 3 linhas, uma política de SELECT por tabela.
SELECT tablename, policyname, cmd, roles
  FROM pg_policies
 WHERE schemaname = 'public'
   AND tablename IN ('mexico_precos', 'colombia_precos', 'peru_precos')
 ORDER BY tablename;

-- =============================================================================
-- Status: incluir as três tabelas novas em v_ultima_atualizacao
-- A página Status do dashboard lê essa view. Sem isso os cards novos nunca
-- aparecem, mesmo com o ETL rodando e os limiares configurados no index.html.
-- Definição abaixo = a original do supabase_schema.sql (linha 187) + 3 linhas.
-- =============================================================================

CREATE OR REPLACE VIEW v_ultima_atualizacao AS
SELECT 'brasil_precos'          AS tabela, MAX(extracted_at) AS ultima_atualizacao, COUNT(*) AS total_registros FROM brasil_precos
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

-- Esperado: 11 linhas, com colombia_precos trazendo 22.579 registros.
SELECT * FROM v_ultima_atualizacao ORDER BY tabela;
