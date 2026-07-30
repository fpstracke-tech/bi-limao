-- ─────────────────────────────────────────────────────────────
-- Chile: câmbio real por data (dólar observado BCCh) — 28/07/2026
-- Rodar no Supabase → SQL Editor ANTES de executar pipeline_odepa_limon.py
-- ─────────────────────────────────────────────────────────────
--
-- Regra de conversão adotada:
--   • 2023–2025 → cambio NULL → frontend usa 980 fixo (paridade com o PBI)
--   • 2026+     → cambio = dólar observado do Banco Central do Chile na data
--                 da observação (fonte: mindicador.cl/api/dolar/<ano>)
--
-- A taxa fica gravada por registro: a série NÃO se revaloriza retroativamente
-- a cada run do ETL (o dólar observado de uma data passada nunca muda).

ALTER TABLE chile_precos
    ADD COLUMN IF NOT EXISTS cambio NUMERIC(10, 4);

COMMENT ON COLUMN chile_precos.cambio IS
    'CLP por 1 USD — dolar observado BCCh na data da observacao. NULL = usar 980 fixo (dados 2023-2025, paridade PBI).';

-- Índice não é necessário (coluna só é lida junto com o registro).

-- Verificação após rodar o ETL:
--   SELECT ano, COUNT(*) AS n, COUNT(cambio) AS com_cambio,
--          ROUND(AVG(cambio), 1) AS cambio_medio
--   FROM chile_precos GROUP BY ano ORDER BY ano;
--
-- Esperado: 2023/2024/2025 com_cambio = 0 · 2026 com_cambio = n (média ~898)
