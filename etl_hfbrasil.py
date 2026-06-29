"""
ETL HF Brasil — Preços semanais de Lima Ácida Tahiti
=====================================================
Baixa o Excel do HF Brasil/Cepea, filtra Lima Ácida Tahiti e faz upsert no Supabase.

Colunas do Excel: Produto, Regiao, Dia, Mes, Ano, Moeda, Unidade, Preco
Unidade original: R$/caixa 40,8 kg
Conversao: preco_4_5kg = Preco / 40.8 * 4.5

Uso:
    python etl_hfbrasil.py
"""

import io
import csv
import time
from datetime import datetime, timezone, date

import requests
import pandas as pd

# ── CONFIG ─────────────────────────────────────────────────────────────────────
URL = (
    "https://www.hfbrasil.org.br/br/estatistica/preco/exportar.aspx"
    "?produto=9"
    "&regiao[]=111&regiao[]=110&regiao[]=109&regiao[]=112&regiao[]=113"
    "&periodicidade=diario&ano_inicial=2023&ano_final=2026"
)
OUTPUT_CSV     = "brasil_precos.csv"
PRODUTO_FILTRO = "Lima Ácida Tahiti"   # substring para filtrar


# Headers realistas para evitar bloqueio por bot-detection
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer":         "https://www.hfbrasil.org.br/br/estatistica/preco/preco-semanal.aspx",
    "Connection":      "keep-alive",
    "DNT":             "1",
}


# ── FETCH + PARSE ──────────────────────────────────────────────────────────────
def fetch_hfbrasil() -> list[dict]:
    print("[1] Baixando Excel HF Brasil...")

    # Primeiro acessa a página principal para obter cookies de sessão
    session = requests.Session()
    session.get("https://www.hfbrasil.org.br/br/estatistica/preco/preco-semanal.aspx",
                headers=HEADERS, timeout=30)
    time.sleep(2)

    # Agora faz o download com cookies da sessão
    r = session.get(URL, headers=HEADERS, timeout=60)
    if r.status_code == 403:
        print("    403 na primeira tentativa — aguardando 8s e retentando...")
        time.sleep(8)
        r = session.get(URL, headers=HEADERS, timeout=60)
    if r.status_code != 200:
        raise requests.HTTPError(f"HTTP {r.status_code} após retry")

    df = pd.read_excel(io.BytesIO(r.content), header=None)
    # Linha 0 = cabeçalho
    df.columns = df.iloc[0]
    df = df.iloc[1:].reset_index(drop=True)
    print(f"    {len(df)} linhas | colunas: {list(df.columns)}")

    # Filtrar Lima Ácida Tahiti
    df_limao = df[df["Produto"].astype(str).str.contains(PRODUTO_FILTRO, case=False, na=False)].copy()
    print(f"    {len(df_limao)} registros de Lima Ácida Tahiti")

    extracted_at = datetime.now(timezone.utc).isoformat()
    records = []

    for _, row in df_limao.iterrows():
        try:
            dia = int(row["Dia"])
            mes = int(row["Mês"])
            ano = int(row["Ano"])
            dt  = date(ano, mes, dia)
            iso = dt.isocalendar()

            preco_raw = row["Preço"]
            preco_cx  = float(str(preco_raw).replace(",", "."))  # R$/cx 40,8kg
            preco_kg  = round(preco_cx / 40.8, 4)
            preco_4_5 = round(preco_cx / 40.8 * 4.5, 2)

            regiao = str(row["Região"]).replace(" (região)", "").strip()

            records.append({
                "semana":       int(iso.week),
                "ano":          int(iso.year),
                "data_semana":  dt.strftime("%Y-%m-%d"),
                "regiao":       regiao,
                "preco_kg":     preco_kg,
                "preco_4_5kg":  preco_4_5,
                "extracted_at": extracted_at,
            })
        except Exception:
            continue

    print(f"    {len(records)} registros parseados")

    # Deduplificar por (semana, ano, regiao) — mantém o registro mais recente de cada semana
    seen: dict[tuple, dict] = {}
    for rec in records:
        key = (rec["semana"], rec["ano"], rec["regiao"])
        # sobrescreve — o loop já está em ordem crescente de data, então o último vence
        seen[key] = rec
    records = list(seen.values())
    print(f"    {len(records)} registros únicos (semana/ano/região)")

    return records


# ── MAIN ───────────────────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("HF BRASIL ETL — Lima Ácida Tahiti")
    print("=" * 60)

    records = fetch_hfbrasil()

    if not records:
        print("Nenhum registro obtido.")
        return

    # Salvar CSV
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=records[0].keys())
        writer.writeheader()
        writer.writerows(records)
    print(f"    Salvo: {OUTPUT_CSV}")

    # Preview
    print("\nPreview (3 primeiros):")
    for r in records[:3]:
        print(f"  sem {r['semana']}/{r['ano']} | {r['regiao']:25s} | R$ {r['preco_kg']:.4f}/kg | cx4.5: R$ {r['preco_4_5kg']}")

    # Upsert Supabase
    try:
        from supabase_upsert import upsert
        result = upsert("brasil_precos", records, on_conflict="semana,ano,regiao")
        print(f"    Supabase: {result['inserted']} registros inseridos")
        if result["errors"]:
            print(f"    Erros: {result['errors']}")
    except Exception as e:
        print(f"    Supabase skipped: {e}")

    print("\n" + "=" * 60)
    print("CONCLUIDO")


if __name__ == "__main__":
    main()
