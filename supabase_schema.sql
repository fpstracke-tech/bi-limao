-- ============================================================
-- BI Limão — Schema Supabase (PostgreSQL)
-- TFruits / Tradeconnex
-- ============================================================
-- Tabelas espelham as fontes do Power BI original:
--   Base_Brasil       → brasil_precos
--   Chile_consolidado → chile_precos
--   Base_EuropaTotal  → europa_precos
--   Aschenberg        → containers
--   Comexstat_rev     → comexstat_exportacoes
--   Weather_Brasil    → clima_brasil_atual + clima_brasil_forecast
--   Weather_Forecast  → clima_forecast
-- ============================================================

-- Habilitar RLS (Row Level Security) — ajustar policies conforme necessário
-- Por padrão as tabelas ficam acessíveis via service_role key (GitHub Actions)
-- e leitura pública via anon key (dashboard Vercel)

-- ─────────────────────────────────────────────────────────────
-- 1. PREÇOS BRASIL (HF Brasil)
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS brasil_precos (
    id              BIGSERIAL PRIMARY KEY,
    semana          SMALLINT        NOT NULL,          -- semana ISO (1–53)
    ano             SMALLINT        NOT NULL,
    data_semana     DATE,                              -- data da segunda da semana
    regiao          TEXT,                              -- ex: "SP", "MG"
    preco_kg        NUMERIC(8, 4),                     -- €/kg original
    preco_4_5kg     NUMERIC(8, 2),                     -- preço caixa 4,5kg
    extracted_at    TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    UNIQUE (semana, ano, regiao)
);

CREATE INDEX IF NOT EXISTS idx_brasil_precos_ano_semana ON brasil_precos (ano, semana);

-- ─────────────────────────────────────────────────────────────
-- 2. PREÇOS CHILE (ODEPA)
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS chile_precos (
    id              BIGSERIAL PRIMARY KEY,
    fecha           DATE            NOT NULL,
    semana          SMALLINT,                          -- semana ISO derivada da data
    ano             SMALLINT,
    producto        TEXT            NOT NULL DEFAULT 'LIMÓN',
    mercado         TEXT,
    presentacion    TEXT,
    precio          NUMERIC(10, 2),                    -- CLP/kg
    unidad          TEXT,
    extracted_at    TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    UNIQUE (fecha, mercado, presentacion)
);

CREATE INDEX IF NOT EXISTS idx_chile_precos_ano_semana ON chile_precos (ano, semana);

-- ─────────────────────────────────────────────────────────────
-- 3. PREÇOS EUROPA (FranceAgriMer)
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS europa_precos (
    id              BIGSERIAL PRIMARY KEY,
    semana          SMALLINT        NOT NULL,
    semana_fmt      CHAR(2),                           -- "01", "27", etc.
    ano             SMALLINT        NOT NULL,
    ano_semana      VARCHAR(8),                        -- "2026-27"
    stade           TEXT,                              -- "Grossistes", "Import"
    mercado         TEXT,                              -- nome do MIN/mercado
    produto         TEXT,                              -- "LIME Brésil bateau", etc.
    unidade         TEXT,                              -- "euro HT le kg"
    preco           NUMERIC(8, 4),                     -- €/kg original
    preco_4_5kg     NUMERIC(8, 2),                     -- Preco * 4.5 * 0.7
    modal           TEXT CHECK (modal IN ('Marítimo', 'Aéreo')),
    extracted_at    TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    UNIQUE (semana, ano, mercado, produto)
);

CREATE INDEX IF NOT EXISTS idx_europa_precos_ano_semana ON europa_precos (ano, semana);

-- ─────────────────────────────────────────────────────────────
-- 4. CONTAINERS ASCHENBERG
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS containers (
    id              BIGSERIAL PRIMARY KEY,
    flow            TEXT            NOT NULL CHECK (flow IN ('Shipped', 'Arrivals')),
    from_zone       TEXT            NOT NULL,          -- "Brasil - All"
    to_zone         TEXT            NOT NULL,          -- "Europe - All (Med. + N. Europe + UK)"
    week            SMALLINT        NOT NULL,          -- semana ISO
    year            SMALLINT        NOT NULL,
    containers      NUMERIC(10, 1),                    -- qtde containers 40'
    extracted_at    TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    UNIQUE (flow, from_zone, to_zone, week, year)
);

CREATE INDEX IF NOT EXISTS idx_containers_year_week ON containers (year, week);
CREATE INDEX IF NOT EXISTS idx_containers_flow      ON containers (flow);

-- ─────────────────────────────────────────────────────────────
-- 5. EXPORTAÇÕES COMEXSTAT (MDIC)
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS comexstat_exportacoes (
    id              BIGSERIAL PRIMARY KEY,
    ano             SMALLINT        NOT NULL,
    mes             SMALLINT        NOT NULL CHECK (mes BETWEEN 1 AND 12),
    pais            TEXT            NOT NULL,
    ncm             VARCHAR(8)      NOT NULL DEFAULT '08055000',  -- limão tahiti
    kg_liquido      NUMERIC(15, 2),
    valor_usd       NUMERIC(15, 2),
    extracted_at    TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    UNIQUE (ano, mes, pais, ncm)
);

CREATE INDEX IF NOT EXISTS idx_comexstat_ano_mes ON comexstat_exportacoes (ano, mes);
CREATE INDEX IF NOT EXISTS idx_comexstat_pais    ON comexstat_exportacoes (pais);

-- ─────────────────────────────────────────────────────────────
-- 6. CLIMA BRASIL — CONDIÇÕES ATUAIS (HG Brasil)
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS clima_brasil_atual (
    id              BIGSERIAL PRIMARY KEY,
    cidade          TEXT            NOT NULL,
    woeid           INTEGER         NOT NULL,
    data_ref        DATE            NOT NULL,
    hora_ref        TIME,
    temp_c          SMALLINT,
    humidade_pct    SMALLINT,
    chuva_mm        NUMERIC(6, 2)   DEFAULT 0,
    descricao       TEXT,
    vento_kmh       TEXT,
    nascer_sol      TEXT,
    por_sol         TEXT,
    extracted_at    TIMESTAMPTZ     NOT NULL DEFAULT NOW()
    -- sem UNIQUE: cada execução sobrescreve o snapshot atual
);

CREATE INDEX IF NOT EXISTS idx_clima_br_atual_cidade ON clima_brasil_atual (cidade);
CREATE INDEX IF NOT EXISTS idx_clima_br_atual_data   ON clima_brasil_atual (data_ref);

-- ─────────────────────────────────────────────────────────────
-- 7. CLIMA BRASIL — PREVISÃO 15 DIAS (HG Brasil)
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS clima_brasil_forecast (
    id              BIGSERIAL PRIMARY KEY,
    cidade          TEXT            NOT NULL,
    woeid           INTEGER         NOT NULL,
    data_previsao   DATE            NOT NULL,
    dia_semana      VARCHAR(3),                        -- "Seg", "Ter", etc.
    temp_max        SMALLINT,
    temp_min        SMALLINT,
    humidade_pct    SMALLINT,
    chuva_mm        NUMERIC(6, 2)   DEFAULT 0,
    prob_chuva_pct  SMALLINT        DEFAULT 0,
    nebulosidade_pct NUMERIC(5, 1)  DEFAULT 0,
    descricao       TEXT,
    condicao        TEXT,
    extracted_at    TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    UNIQUE (cidade, data_previsao, extracted_at)
);

CREATE INDEX IF NOT EXISTS idx_clima_br_fc_cidade ON clima_brasil_forecast (cidade);
CREATE INDEX IF NOT EXISTS idx_clima_br_fc_data   ON clima_brasil_forecast (data_previsao);

-- ─────────────────────────────────────────────────────────────
-- 8. CLIMA GLOBAL — FORECAST 5 DIAS × 3H (OpenWeatherMap)
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS clima_forecast (
    id              BIGSERIAL PRIMARY KEY,
    cidade          TEXT            NOT NULL,
    pais            CHAR(2)         NOT NULL,
    data_hora       TIMESTAMPTZ     NOT NULL,
    temp_c          SMALLINT,
    temp_min        SMALLINT,
    temp_max        SMALLINT,
    humidade_pct    SMALLINT,
    descricao       TEXT,
    rain_3h         NUMERIC(6, 2)   DEFAULT 0,
    chuva_mm        NUMERIC(6, 2)   DEFAULT 0,
    extracted_at    TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    UNIQUE (cidade, pais, data_hora)
);

CREATE INDEX IF NOT EXISTS idx_clima_fc_cidade    ON clima_forecast (cidade);
CREATE INDEX IF NOT EXISTS idx_clima_fc_data_hora ON clima_forecast (data_hora);

-- ─────────────────────────────────────────────────────────────
-- VIEWS ÚTEIS PARA O DASHBOARD
-- ─────────────────────────────────────────────────────────────

-- Última extração de cada fonte (para monitorar atualização)
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

-- Preço médio semanal Europa (marítimo apenas) — para o gráfico principal
CREATE OR REPLACE VIEW v_europa_preco_medio_semanal AS
SELECT
    ano,
    semana,
    ano_semana,
    ROUND(AVG(preco_4_5kg), 2)  AS preco_medio_4_5kg,
    ROUND(AVG(preco), 4)        AS preco_medio_kg,
    COUNT(DISTINCT mercado)     AS n_mercados
FROM europa_precos
WHERE modal = 'Marítimo'
GROUP BY ano, semana, ano_semana
ORDER BY ano, semana;

-- Containers totais por semana (Shipped + Arrivals separados)
CREATE OR REPLACE VIEW v_containers_semanal AS
SELECT
    flow,
    year,
    week,
    SUM(containers) AS total_containers
FROM containers
WHERE week != 0   -- excluir linha TOTAL se houver
GROUP BY flow, year, week
ORDER BY flow, year, week;

-- ─────────────────────────────────────────────────────────────
-- RLS: leitura pública (anon), escrita só via service_role
-- ─────────────────────────────────────────────────────────────
DO $$
DECLARE
    tbl TEXT;
BEGIN
    FOREACH tbl IN ARRAY ARRAY[
        'brasil_precos', 'chile_precos', 'europa_precos',
        'containers', 'comexstat_exportacoes',
        'clima_brasil_atual', 'clima_brasil_forecast', 'clima_forecast'
    ] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', tbl);
        -- DROP antes de recriar para evitar erro em re-execuções
        EXECUTE format('DROP POLICY IF EXISTS "leitura_publica_%s" ON %I', tbl, tbl);
        EXECUTE format('
            CREATE POLICY "leitura_publica_%s"
            ON %I FOR SELECT USING (true)', tbl, tbl);
    END LOOP;
END $$;
