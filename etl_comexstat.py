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
import os
import sys
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


# ── MÊS ALVO (retry até a fonte publicar) ─────────────────────────────────────
def mes_alvo() -> tuple[int, int]:
    """Mês anterior ao corrente: é ele que o MDIC publica por volta do dia 10."""
    hoje = datetime.now(timezone.utc)
    if hoje.month == 1:
        return hoje.year - 1, 12
    return hoje.year, hoje.month - 1


def mes_no_banco(ano: int, mes: int) -> bool:
    """Consulta o Supabase: o mês alvo já tem registros?"""
    from supabase_upsert import SUPABASE_URL, SUPABASE_KEY
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/comexstat_exportacoes",
        params={"ano": f"eq.{ano}", "mes": f"eq.{mes}", "select": "ano", "limit": 1},
        headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
        timeout=15,
    )
    r.raise_for_status()
    return len(r.json()) > 0


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

    forcar = bool(os.environ.get("COMEXSTAT_FORCAR"))
    ano_alvo, mes_num = mes_alvo()

    # Janela de retry (dias 10-20): se o mês alvo já entrou no banco numa
    # run anterior, não há nada novo a coletar — encerra sem gastar a fonte.
    if not forcar:
        try:
            if mes_no_banco(ano_alvo, mes_num):
                print(f"Mês alvo {ano_alvo}-{mes_num:02d} já está no banco. Nada a fazer.")
                return
            print(f"Mês alvo {ano_alvo}-{mes_num:02d} ainda não está no banco. Coletando...")
        except Exception as e:
            print(f"⚠️  Não consegui checar o banco ({e}). Coletando por garantia...")

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

    # Verificação do mês alvo após a coleta
    tem_alvo = any(r["ano"] == ano_alvo and r["mes"] == mes_num for r in all_records)
    if tem_alvo:
        print(f"\n✅ Mês alvo {ano_alvo}-{mes_num:02d} publicado e carregado.")
    else:
        print(f"\n⏳ Fonte ainda não publicou {ano_alvo}-{mes_num:02d}. "
              f"Nova tentativa na próxima run da janela (dias 10-20).")
        # No fim da janela sem dado: falha de verdade, para abrir issue e alertar
        if datetime.now(timezone.utc).day >= 20 and not forcar:
            print("❌ Fim da janela de retry sem o mês alvo — verificar a fonte MDIC.")
            sys.exit(1)

    print("\n" + "=" * 60)
    print("CONCLUÍDO")


if __name__ == "__main__":
    main()
