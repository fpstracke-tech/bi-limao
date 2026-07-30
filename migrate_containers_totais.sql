-- ═══════════════════════════════════════════════════════════════════════
-- containers_totais — linha TOTAL da fonte Aschenberg
-- ═══════════════════════════════════════════════════════════════════════
-- Por que uma tabela separada e não week = NULL em `containers`:
-- a chave de conflito do upsert é (flow, from_zone, to_zone, week, year) e o
-- Postgres não considera conflito quando qualquer coluna da chave é NULL
-- (NULL <> NULL). Cada run semanal re-inseriria a linha TOTAL, exatamente o
-- que inflou `chile_precos` com 48 mil duplicatas.
--
-- Este total é o do ANO INTEIRO, calculado pela própria fonte. Não é a soma
-- das semanas que temos em `containers` — a fonte mostra uma janela móvel de
-- ~22 semanas, então o banco nunca tem o ano completo. É o número que fecha
-- com o relatório do Power BI.
--
-- Rodar no SQL Editor do Supabase. Idempotente.
-- ═══════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS containers_totais (
    id                BIGSERIAL PRIMARY KEY,
    flow              TEXT    NOT NULL,   -- 'Shipped' | 'Arrivals'
    from_zone         TEXT    NOT NULL,
    to_zone           TEXT    NOT NULL,
    year              INT     NOT NULL,
    total_containers  NUMERIC,            -- NULL = fonte exibiu '-'
    extracted_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT containers_totais_uk UNIQUE (flow, from_zone, to_zone, year)
);

COMMENT ON TABLE containers_totais IS
    'Linha TOTAL (ano inteiro) da tabela de containers da fonte. Não somar com containers: janelas diferentes.';
COMMENT ON COLUMN containers_totais.total_containers IS
    'Total do ano informado pela fonte. Pode ter meia unidade (ex: 7477.5).';

CREATE INDEX IF NOT EXISTS containers_totais_flow_year_idx
    ON containers_totais (flow, year);

-- ── RLS: leitura pública (anon), escrita só via service_role ──────────
ALTER TABLE containers_totais ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "leitura_publica_containers_totais" ON containers_totais;
CREATE POLICY "leitura_publica_containers_totais"
    ON containers_totais FOR SELECT USING (true);
