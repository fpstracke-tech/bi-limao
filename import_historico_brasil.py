"""
Importação histórica — Brasil (HF Brasil / Cepea)
==================================================
Fonte: limao_brasil.xlsx  (Lima Ácida Tahiti - Colhida - Mercado)
Colunas: Produto | Região | Dia | Mês | Ano | Moeda | Unidade | Preço

Preço original: R$/caixa 27 kg  →  convertido para R$/kg e R$/cx 4,5 kg

A tabela brasil_precos usa UNIQUE (data, regiao, tipo).
Este script usa upsert — seguro para rodar múltiplas vezes.

Uso:
    python import_historico_brasil.py limao_brasil.xlsx
    python import_historico_brasil.py  # usa caminho padrão nos uploads
"""

import sys
import os
from datetime import date, timezone, datetime
from pathlib import Path

import openpyxl

UPLOAD_PATH = Path(__file__).parent / "limao_brasil.xlsx"
PRODUTO_FILTRO = "Lima Ácida Tahiti - Colhida - Mercado"
TIPO_PADRAO    = "HF Brasil"   # tipo único para esta fonte


def load_xlsx(path: Path) -> list[dict]:
    print(f"[1] Carregando {path.name}...")
    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    header = rows[0]
    print(f"    Header: {header}")
    print(f"    Total linhas: {len(rows)-1}")

    extracted_at = datetime.now(timezone.utc).isoformat()
    records = []

    for row in rows[1:]:
        if not row[0] or PRODUTO_FILTRO not in str(row[0]):
            continue

        produto, regiao, dia, mes, ano, moeda, unidade, preco = row[:8]

        if not all([regiao, dia, mes, ano, preco]):
            continue

        try:
            dt = date(int(ano), int(mes), int(dia))
        except Exception:
            continue

        try:
            preco_cx27 = float(preco)
        except Exception:
            continue

        preco_kg    = round(preco_cx27 / 27, 4)
        preco_45kg  = round(preco_kg * 4.5, 2)
        iso         = dt.isocalendar()

        records.append({
            "data":         dt.isoformat(),
            "semana":       int(iso.week),
            "ano":          int(iso.year),
            "regiao":       str(regiao).strip(),
            "tipo":         TIPO_PADRAO,
            "preco_kg":     preco_kg,
            "preco_4_5kg":  preco_45kg,
            "extracted_at": extracted_at,
        })

    print(f"    Registros Lima Ácida Tahiti: {len(records)}")
    return records


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else UPLOAD_PATH
    if not path.exists():
        # Tenta caminho de uploads da sessão
        uploads = Path(__file__).parent.parent / "uploads" / "limao_brasil.xlsx"
        if uploads.exists():
            path = uploads
        else:
            print(f"Arquivo não encontrado: {path}")
            sys.exit(1)

    records = load_xlsx(path)
    if not records:
        print("Nenhum registro obtido.")
        sys.exit(1)

    # Preview
    anos = sorted(set(r["ano"] for r in records))
    regioes = sorted(set(r["regiao"] for r in records))
    print(f"\n    Anos: {anos}")
    print(f"    Regiões: {regioes}")
    print(f"    Preço range: R$ {min(r['preco_kg'] for r in records):.4f} – {max(r['preco_kg'] for r in records):.4f} /kg")
    print("\nPreview (3 primeiros):")
    for r in records[:3]:
        print(f"  {r['data']} | {r['regiao']:30s} | R$ {r['preco_kg']:.4f}/kg | S{r['semana']}/{r['ano']}")

    # Upsert Supabase
    print("\n[2] Enviando ao Supabase...")
    try:
        from supabase_upsert import upsert
        result = upsert("brasil_precos", records, on_conflict="data,regiao,tipo")
        print(f"    OK: {result['inserted']} registros inseridos/atualizados")
        if result.get("errors"):
            print(f"    Erros: {result['errors'][:3]}")
    except Exception as e:
        print(f"    ERRO Supabase: {e}")
        sys.exit(1)

    print("\nCONCLUÍDO.")


if __name__ == "__main__":
    main()
