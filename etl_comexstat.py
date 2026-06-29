"""
ETL Comexstat — Exportações brasileiras de limão Tahiti (NCM 08055000)
======================================================================
Consulta a API do MDIC e faz upsert no Supabase.

Uso:
    pip install requests --break-system-packages
    python etl_comexstat.py

Saída:
    comexstat_exportacoes.csv
"""

import csv
import json
from datetime import datetime, timezone

import requests

# ── CONFIG ─────────────────────────────────────────────────────────────────────
API_URL    = "https://api-comexstat.mdic.gov.br/general"
OUTPUT_CSV = "comexstat_exportacoes.csv"
NCM_LIMAO  = "08055000"  # Limão Tahiti

# Anos a consultar (ajustar conforme necessário)
ANOS = [
    {"from": "2022-01", "to": "2022-12"},
    {"from": "2023-01", "to": "2023-12"},
    {"from": "2024-01", "to": "2024-12"},
    {"from": "2025-01", "to": "2025-12"},
    {"from": "2026-01", "to": "2026-12"},
]


# ── FETCH ──────────────────────────────────────────────────────────────────────
def fetch_comexstat(period_from: str, period_to: str) -> list[dict]:
    """Consulta exportações de limão para um período."""
    payload = {
        "flow":        "export",
        "monthDetail": True,
        "period":      {"from": period_from, "to": period_to},
        "filters":     [{"filter": "ncm", "values": [NCM_LIMAO]}],
        "details":     ["country"],
        "metrics":     ["metricFOB", "metricKG"],
    }
    r = requests.post(API_URL, json=payload, timeout=30)
    r.raise_for_status()
    data = r.json()

    # Estrutura da resposta: {"data": {"list": [...]}}
    items = data.get("data", {}).get("list", [])
    return items


# ── TRANSFORM ──────────────────────────────────────────────────────────────────
def transform(items: list[dict], extracted_at: str) -> list[dict]:
    records = []
    for item in items:
        try:
            ano = int(item.get("year", 0))
            mes = int(item.get("monthNumber", 0))
            if not ano or not mes:
                continue

            records.append({
                "ano":          ano,
                "mes":          mes,
                "pais":         str(item.get("country", "")).strip(),
                "ncm":          NCM_LIMAO,
                "kg_liquido":   float(item.get("metricKG", 0) or 0),
                "valor_usd":    float(item.get("metricFOB", 0) or 0),
                "extracted_at": extracted_at,
            })
        except (ValueError, TypeError):
            continue
    return records


# ── MAIN ───────────────────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("COMEXSTAT ETL — Exportações Limão Tahiti (NCM 08055000)")
    print("=" * 60)

    extracted_at = datetime.now(timezone.utc).isoformat()
    all_records  = []

    for period in ANOS:
        try:
            items = fetch_comexstat(period["from"], period["to"])
            recs  = transform(items, extracted_at)
            print(f"  ✅ {period['from']} → {period['to']}: {len(recs)} registros")
            all_records.extend(recs)
        except requests.HTTPError as e:
            print(f"  ❌ {period['from']} → {period['to']}: HTTP {e.response.status_code}")
        except Exception as e:
            print(f"  ❌ {period['from']} → {period['to']}: {e}")

    print(f"\n[Total] {len(all_records)} registros")

    if not all_records:
        print("❌ Nenhum registro obtido.")
        return

    # Salvar CSV
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=all_records[0].keys())
        writer.writeheader()
        writer.writerows(all_records)
    print(f"    Salvo: {OUTPUT_CSV}")

    # Preview
    print("\nPreview (3 primeiros):")
    for r in all_records[:3]:
        print(f"  {r['ano']}-{r['mes']:02d} | {r['pais']:30s} | {r['kg_liquido']:,.0f} kg | USD {r['valor_usd']:,.0f}")

    # Upsert Supabase
    try:
        from supabase_upsert import upsert
        result = upsert("comexstat_exportacoes", all_records, on_conflict="ano,mes,pais,ncm")
        print(f"    Supabase: {result['inserted']} registros inseridos")
        if result["errors"]:
            print(f"    ⚠️  Erros: {result['errors']}")
    except Exception as e:
        print(f"    ⚠️  Supabase skipped: {e}")

    print("\n" + "=" * 60)
    print("CONCLUÍDO")


if __name__ == "__main__":
    main()
