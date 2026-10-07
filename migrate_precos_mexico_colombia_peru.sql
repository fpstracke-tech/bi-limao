-- =============================================================================
-- BI Limão — preços de mercado interno: México, Colômbia e Peru
-- Rodar MANUALMENTE no SQL Editor do Supabase (projeto lrxqsrfjisauvdcwaydr).
-- O conector MCP não alcança este projeto, então DDL nunca sobe por código.
-- Rodar ANTES de qualquer ETL novo: o upsert quebra se a tabela não existir.
--
-- Três decisões embutidas, todas vindas de erro já cometido neste projeto:
--  1. Nenhuma coluna da chave de conflito aceita NULL. No Postgres NULL nunca
--     conflita com NULL, e foi assim que chile_precos acumulou 48 mil duplicatas.
--  2. A chave contém toda a granularidade que a fonte distingue. Chave curta
--     demais colapsa linha real e desloca a média semanal.
--  3. Preço gravado na moeda e na unidade da fonte; conversão só na exibição.
-- =============================================================================

-- ── MÉXICO (SNIIM) ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS mexico_precos (
    id               BIGSERIAL PRIMARY KEY,
    fecha            DATE          NOT NULL,
    semana           SMALLINT,
    ano              SMALLINT,
    producto         TEXT          NOT NULL DEFAULT 'Limón s/semilla',
    origen           TEXT          NOT NULL DEFAULT '',   -- estado produtor
    mercado          TEXT          NOT NULL DEFAULT '',   -- central de abasto (destino)
    presentacion     TEXT          NOT NULL DEFAULT '',
    precio_min       NUMERIC(10,2),
    precio_max       NUMERIC(10,2),
    precio           NUMERIC(10,2),                        -- Precio Frec, MXN/kg
    cambio           NUMERIC(12,4),                        -- MXN por USD na data
    cambio_estimado  BOOLEAN       NOT NULL DEFAULT false,
    extracted_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    UNIQUE (fecha, mercado, origen, presentacion)
);
CREATE INDEX IF NOT EXISTS idx_mexico_precos_ano_semana ON mexico_precos (ano, semana);

-- ── COLÔMBIA (DANE / SIPSA) ──────────────────────────────────────────────────
-- promediosSipsaCiudad devolve só a média por cidade, por isso não há
-- precio_min/precio_max aqui. Se depois quisermos a banda, ela vem da
-- operação promediosSipsaSemanaMadr e entra como duas colunas novas.
CREATE TABLE IF NOT EXISTS colombia_precos (
    id               BIGSERIAL PRIMARY KEY,
    fecha            DATE          NOT NULL,
    semana           SMALLINT,
    ano              SMALLINT,
    producto         TEXT          NOT NULL DEFAULT 'Limón Tahití',
    mercado          TEXT          NOT NULL DEFAULT '',   -- cidade / central de abasto
    precio           NUMERIC(10,2),                        -- precioPromedio, COP/kg
    cambio           NUMERIC(12,4),                        -- TRM: COP por USD na data
    cambio_estimado  BOOLEAN       NOT NULL DEFAULT false,
    extracted_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    UNIQUE (fecha, mercado, producto)
);
CREATE INDEX IF NOT EXISTS idx_colombia_precos_ano_semana ON colombia_precos (ano, semana);

-- ── PERU (EMMSA / Gran Mercado Mayorista de Lima) ────────────────────────────
CREATE TABLE IF NOT EXISTS peru_precos (
    id               BIGSERIAL PRIMARY KEY,
    fecha            DATE          NOT NULL,
    semana           SMALLINT,
    ano              SMALLINT,
    producto         TEXT          NOT NULL DEFAULT 'LIMON',
    variedad         TEXT          NOT NULL DEFAULT '',   -- LIMON CITRICO CAJON / BOLSA
    mercado          TEXT          NOT NULL DEFAULT 'GMML - Lima',
    precio_min       NUMERIC(10,2),
    precio_max       NUMERIC(10,2),
    precio           NUMERIC(10,2),                        -- Precio Prom, PEN/kg
    volumen_t        NUMERIC(12,2),                        -- toneladas ingressadas
    cambio           NUMERIC(12,4),                        -- PEN por USD na data
    cambio_estimado  BOOLEAN       NOT NULL DEFAULT false,
    extracted_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    UNIQUE (fecha, variedad, mercado)
);
CREATE INDEX IF NOT EXISTS idx_peru_precos_ano_semana ON peru_precos (ano, semana);

-- ── CONFERÊNCIA ──────────────────────────────────────────────────────────────
-- Esperado: 3 linhas, uma por tabela.
SELECT table_name
  FROM information_schema.tables
 WHERE table_schema = 'public'
   AND table_name IN ('mexico_precos', 'colombia_precos', 'peru_precos')
 ORDER BY table_name;
