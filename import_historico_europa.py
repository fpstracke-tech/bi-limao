"""
Importação histórica — Europa (FranceAgriMer histórico)
========================================================
Fonte: PREÇOS_SEMANAIS_EUROPA_LIMPO.xlsx
Colunas: Ano | Semana | Data | Preço  (€/cx 4,5 kg, mercado único agregado)

A tabela europa_precos usa UNIQUE (semana, ano, mercado).
Mercado fixo: "Agregado" para este histórico.

Uso:
    python import_historico_europa.py PREÇOS_SEMANAIS_EUROPA_LIMPO.xlsx
"""

import sys
import os
from datetime import date, timezone, datetime
from pathlib import Path

import openpyxl

UPLOAD_PATH = Path(__file__).parent / "PREÇOS_SEMANAIS_EUROPA_LIMPO.xlsx"
MERCADO_PADRAO = "Agregado"


def load_xlsx(path: Path) -> list[dict]:
    print(f"[1] Carregando {path.name}...")
    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    print(f"    Header: {rows[0]}")
    print(f"    Total linhas: {len(rows)-1}")

    extracted_at = datetime.now(timezone.utc).isoformat()
    records = []

    for row in rows[1:]:
        ano, semana, data_ref, preco = row[0], row[1], row[2], row[3]

        if not all([ano, semana, preco]):
            continue

        try:
            ano    = int(ano)
            semana = int(semana)
            preco  = float(preco)
        except Exception:
            continue

        # data_ref pode ser datetime ou None
        if hasattr(data_ref, 'date'):
            data_iso = data_ref.date().isoformat()
        elif isinstance(data_ref, str):
            data_iso = data_ref[:10]
        else:
            # Reconstruir segunda-feira da semana ISO
            d = date.fromisocalendar(ano, semana, 1)
            data_iso = d.isoformat()

        # ano_semana string para índice
        ano_semana = f"{ano}-S{semana:02d}"

        records.append({
            "semana":       semana,
            "ano":          ano,
            "ano_semana":   ano_semana,
            "mercado":      MERCADO_PADRAO,
            "preco_4_5kg":  round(preco, 4),
            "extracted_at": extracted_at,
        })

    print(f"    Registros: {len(records)}")
    return records


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else UPLOAD_PATH
    if not path.exists():
        uploads = Path(__file__).parent.parent / "uploads" / "PREÇOS_SEMANAIS_EUROPA_LIMPO.xlsx"
        alts = [
            uploads,
            Path(__file__).parent / "PRECOS_SEMANAIS_EUROPA_LIMPO.xlsx",
        ]
        for alt in alts:
            if alt.exists():
                path = alt
                break
        else:
            print(f"Arquivo não encontrado: {path}")
            sys.exit(1)

    records = load_xlsx(path)
    if not records:
        print("Nenhum registro obtido.")
        sys.exit(1)

    anos = sorted(set(r["ano"] for r in records))
    print(f"\n    Anos: {anos}")
    print(f"    Semanas: {len(records)}")
    print(f"    Preço range: € {min(r['preco_4_5kg'] for r in records):.2f} – {max(r['preco_4_5kg'] for r in records):.2f} /cx4,5kg")
    print("\nPreview (3 primeiros):")
    for r in records[:3]:
        print(f"  S{r['semana']}/{r['ano']} | € {r['preco_4_5kg']:.3f}")

    print("\n[2] Enviando ao Supabase...")
    try:
        from supabase_upsert import upsert
        result = upsert("europa_precos", records, on_conflict="semana,ano,mercado")
        print(f"    OK: {result['inserted']} registros inseridos/atualizados")
        if result.get("errors"):
            print(f"    Erros: {result['errors'][:3]}")
    except Exception as e:
        print(f"    ERRO Supabase: {e}")
        sys.exit(1)

    print("\nCONCLUÍDO.")


if __name__ == "__main__":
    main()
