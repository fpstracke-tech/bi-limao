"""
Importação histórica — Chile (ODEPA planilhas 2023/2024/2025)
==============================================================
Estratégia por arquivo:
  2023.xlsx / 2024.xlsx — arquivo focado em limão (malla 18 kg), todas as linhas são limão
  2025.xlsx              — multi-produto, filtrar Variedad/Tipo = 'Tahití'

Unidades normalizadas para CLP/kg:
  $/malla 18 kilos  → / 18
  $/caja 18 kilos   → / 18
  $/caja 20 kilos   → / 20
  $/caja 24 kilos   → / 24

Colunas: Semana | Desde | Hasta | Ano | Mercado | Variedad/Tipo | Calidad | Procedencia | Precio promedio | Unidad

Uso:
    python import_historico_chile.py
    python import_historico_chile.py pasta/com/arquivos/
"""

import sys
import re
import os
from datetime import date, timezone, datetime
from pathlib import Path

import openpyxl

# Arquivos esperados
BASE_DIR   = Path(__file__).parent
UPLOAD_DIR = BASE_DIR.parent / "uploads"  # uploads da sessão

ARQUIVOS = {
    2023: {"file": "2023.xlsx",  "todos": True},   # arquivo focado em limão
    2024: {"file": "2024.xlsx",  "todos": True},   # arquivo focado em limão
    2025: {"file": "2025.xlsx",  "todos": False},  # multi-produto, filtrar Tahiti
}

KG_POR_UNIDADE = {
    "malla 18 kilos": 18,
    "malla 14 kilos": 14,
    "malla 15 kilos": 15,
    "malla 16 kilos": 16,
    "malla 20 kilos": 20,
    "caja 18 kilos":  18,
    "caja 20 kilos":  20,
    "caja 24 kilos":  24,
    "bandeja 15 kilos": 15,
    "bandeja 18 kilos": 18,
}


def parse_kg(unidade_str: str) -> float | None:
    """Extrai kg da string de unidade. Ex: '$/malla 18 kilos' → 18."""
    if not unidade_str:
        return None
    s = str(unidade_str).lower().replace("$/", "").strip()
    for key, kg in KG_POR_UNIDADE.items():
        if key in s:
            return kg
    # Extrai número: '$/caja 22 kilos' → 22
    m = re.search(r'(\d+(?:[.,]\d+)?)\s*kilos?', s)
    if m:
        return float(m.group(1).replace(",", "."))
    return None


def parse_date_cl(s: str):
    """Converte 'DD/MM/YYYY' para date."""
    try:
        d, m, y = str(s).strip().split("/")
        return date(int(y), int(m), int(d))
    except Exception:
        return None


def load_arquivo(path: Path, todos: bool) -> list[dict]:
    print(f"  Carregando {path.name}...")
    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    print(f"    {len(rows)-1} linhas")

    extracted_at = datetime.now(timezone.utc).isoformat()
    records = []
    skipped = 0

    for row in rows[1:]:
        if len(row) < 9:
            continue

        semana, desde, hasta, ano, mercado, variedad, calidad, procedencia, precio_raw, unidad = (
            row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7], row[8], row[9] if len(row) > 9 else None
        )

        # Filtro por variedad (apenas para 2025 que é multi-produto)
        if not todos:
            if not variedad or "tahit" not in str(variedad).lower():
                continue

        # Data: usar 'Desde' como data de referência
        dt = parse_date_cl(str(desde)) if desde else None
        if dt is None:
            skipped += 1
            continue

        # Preço
        try:
            precio = float(str(precio_raw).replace(",", "."))
        except Exception:
            skipped += 1
            continue

        # Normalizar para CLP/kg
        kg = parse_kg(str(unidad) if unidad else "")
        precio_kg = round(precio / kg, 2) if kg else None

        iso = dt.isocalendar()

        records.append({
            "fecha":        dt.isoformat(),
            "semana":       int(iso.week),
            "ano":          int(iso.year),
            "producto":     "Limón Tahiti",
            "mercado":      str(mercado or "").strip() or None,
            "presentacion": str(calidad or "").strip() or None,
            "precio":       precio_kg,    # CLP/kg normalizado
            "unidad":       "CLP/kg",
            "extracted_at": extracted_at,
        })

    if skipped:
        print(f"    Skipped: {skipped} (sem data ou preço)")
    print(f"    OK: {len(records)} registros")
    return records


def find_file(filename: str, extra_dirs: list[Path]) -> Path | None:
    candidatos = [BASE_DIR / filename, UPLOAD_DIR / filename] + [d / filename for d in extra_dirs]
    for c in candidatos:
        if c.exists():
            return c
    return None


def main():
    extra_dirs = [Path(sys.argv[1])] if len(sys.argv) > 1 else []

    print("=" * 60)
    print("CHILE ETL — Importação Histórica 2023/2024/2025")
    print("=" * 60)

    all_records = []
    for ano, cfg in ARQUIVOS.items():
        path = find_file(cfg["file"], extra_dirs)
        if not path:
            print(f"  ⚠  {cfg['file']} não encontrado — pulando {ano}")
            continue
        recs = load_arquivo(path, cfg["todos"])
        all_records.extend(recs)

    if not all_records:
        print("Nenhum registro obtido.")
        sys.exit(1)

    anos = sorted(set(r["ano"] for r in all_records))
    mercados = sorted(set(r["mercado"] for r in all_records if r["mercado"]))
    precos_validos = [r["precio"] for r in all_records if r["precio"] is not None]

    print(f"\nTotal: {len(all_records)} registros")
    print(f"Anos: {anos}")
    print(f"Mercados: {len(mercados)}")
    if precos_validos:
        print(f"Preço range: {min(precos_validos):.0f} – {max(precos_validos):.0f} CLP/kg")

    print("\nPreview (3 primeiros):")
    for r in all_records[:3]:
        print(f"  {r['fecha']} | S{r['semana']}/{r['ano']} | {r['mercado']} | {r['precio']} CLP/kg")

    print("\n[2] Enviando ao Supabase...")
    try:
        from supabase_upsert import upsert
        result = upsert("chile_precos", all_records, on_conflict="fecha,mercado,presentacion")
        print(f"    OK: {result['inserted']} registros inseridos/atualizados")
        if result.get("errors"):
            print(f"    Erros: {result['errors'][:3]}")
    except Exception as e:
        print(f"    ERRO Supabase: {e}")
        sys.exit(1)

    print("\nCONCLUÍDO.")


if __name__ == "__main__":
    main()
