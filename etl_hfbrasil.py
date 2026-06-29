"""
ETL Brasil — Preços Limão Tahiti (Notícias Agrícolas / CEASAs)
==============================================================
Scraping da página de cotações do Notícias Agrícolas, que agrega:
  - Ceasa Campinas/SP  (Extra, Especial, Primeira)
  - Ceasa BH/MG        (Extra, Especial)
  - Ceagesp/SP         (15-18 DZ, 21-27 DA, Acima 31 DZ)

URL: https://www.noticiasagricolas.com.br/cotacoes/frutas/limao-tahiti-ceasas

Sem autenticação, sem bloqueio de IP — HTML puro.

Uso:
    python etl_hfbrasil.py

Saída:
    brasil_precos.csv  — preços diários por ceasa/tipo
    Upsert → brasil_precos (Supabase)
"""

import re
import csv
from datetime import datetime, date, timezone

import requests
from bs4 import BeautifulSoup

# ── CONFIG ─────────────────────────────────────────────────────────────────────
URL = "https://www.noticiasagricolas.com.br/cotacoes/frutas/limao-tahiti-ceasas"
OUTPUT_CSV = "brasil_precos.csv"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9",
    "Referer": "https://www.noticiasagricolas.com.br/cotacoes/frutas",
}


# ── PARSE ──────────────────────────────────────────────────────────────────────
def parse_date(s: str) -> str | None:
    """Converte '26/06/2026' → '2026-06-26'."""
    try:
        d, m, y = s.strip().split("/")
        return f"{y}-{m.zfill(2)}-{d.zfill(2)}"
    except Exception:
        return None


def parse_price(s: str) -> float | None:
    """Converte '2,75' → 2.75. Retorna None se não numérico."""
    try:
        return float(s.replace(".", "").replace(",", "."))
    except Exception:
        return None


def scrape() -> list[dict]:
    print("[1] Baixando cotações Notícias Agrícolas...")
    r = requests.get(URL, headers=HEADERS, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    extracted_at = datetime.now(timezone.utc).isoformat()
    records = []

    # Cada bloco de fechamento tem um <h2> ou texto "Fechamento: DD/MM/YYYY"
    # seguido de uma <table>
    text = soup.get_text(separator="\n")

    # Também parseia direto das tabelas HTML para maior robustez
    # Encontra padrões "Fechamento: DD/MM/YYYY"
    date_pattern = re.compile(r"Fechamento:\s*(\d{2}/\d{2}/\d{4})")

    # Pega todas as tabelas de cotação
    tables = soup.find_all("table")

    # Busca datas no texto completo
    all_dates = date_pattern.findall(text)
    unique_dates = list(dict.fromkeys(all_dates))  # preserva ordem, remove dupl.

    print(f"    Datas encontradas: {unique_dates[:5]}...")

    for i, table in enumerate(tables):
        # Data correspondente (cada tabela corresponde a um fechamento)
        raw_date = unique_dates[i] if i < len(unique_dates) else None
        if not raw_date:
            continue
        dt_iso = parse_date(raw_date)
        if not dt_iso:
            continue

        dt = date.fromisoformat(dt_iso)
        iso = dt.isocalendar()

        current_ceasa = None
        rows = table.find_all("tr")
        for row in rows:
            cols = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
            if not cols:
                continue

            # Linha de cabeçalho de CEASA (ex: "Ceasa - Campinas/SP")
            if len(cols) == 1 or (len(cols) >= 1 and "ceasa" in cols[0].lower()):
                current_ceasa = cols[0]
                continue
            if len(cols) == 3 and cols[1] == "Preço (R$/Kg)":
                continue  # cabeçalho da tabela
            if len(cols) >= 2 and current_ceasa:
                tipo  = cols[0]
                preco = parse_price(cols[1]) if len(cols) > 1 else None
                if preco is None or tipo in ("Tipo", "***"):
                    continue

                records.append({
                    "data":         dt_iso,
                    "semana":       int(iso.week),
                    "ano":          int(iso.year),
                    "regiao":       current_ceasa,
                    "tipo":         tipo,
                    "preco_kg":     round(preco, 4),
                    "preco_4_5kg":  round(preco * 4.5, 2),
                    "extracted_at": extracted_at,
                })

    print(f"    {len(records)} registros parseados")
    return records


# ── MAIN ───────────────────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("BRASIL ETL — Notícias Agrícolas / CEASAs")
    print("=" * 60)

    records = scrape()

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
        print(f"  {r['data']} | {r['regiao']:30s} | {r['tipo']:15s} | R$ {r['preco_kg']:.2f}/kg")

    # Upsert Supabase
    # UNIQUE: data, regiao, tipo
    try:
        from supabase_upsert import upsert
        result = upsert("brasil_precos", records, on_conflict="data,regiao,tipo")
        print(f"    Supabase: {result['inserted']} registros inseridos")
        if result["errors"]:
            print(f"    Erros: {result['errors'][:2]}")
    except Exception as e:
        print(f"    Supabase skipped: {e}")

    print("\n" + "=" * 60)
    print("CONCLUIDO")


if __name__ == "__main__":
    main()
