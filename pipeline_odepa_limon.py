"""
ETL ODEPA Chile — Preços mayoristas de limão
=============================================
Baixa o CSV da ODEPA, filtra LIMÓN e faz upsert no Supabase.
Mantém compatibilidade com o script original (salva xlsx local).

Uso:
    pip install requests pandas openpyxl tqdm --break-system-packages
    python pipeline_odepa_limon.py

Saída:
    odepa_limon.csv
    (opcional) xlsx local no ownCloud se a pasta existir
"""

import os
import io
import csv
from datetime import datetime, timezone
from pathlib import Path

import requests
import pandas as pd
try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False

# ── CONFIG ─────────────────────────────────────────────────────────────────────
URL = (
    "https://datos.odepa.gob.cl/dataset/33f10516-acbe-4446-b633-68244b9b6b26"
    "/resource/580beca0-e87e-4dd4-9e8a-0bd92773f4a6"
    "/download/precio_mayorista_fruta-hortaliza_2026.csv"
)

PASTA_LOCAL = r"C:\Users\fpstr\ownCloud\TFruits PowerBI\Projeto Report Limão\Chile"
ARQ_RAW     = os.path.join(PASTA_LOCAL, "odepa_raw_2026.csv")
ARQ_EXCEL   = os.path.join(PASTA_LOCAL, "2026.xlsx")
OUTPUT_CSV  = "odepa_limon.csv"


# ── DOWNLOAD ──────────────────────────────────────────────────────────────────
def baixar_arquivo(url: str, destino: str = None) -> bytes:
    """Baixa o CSV e salva localmente se destino fornecido. Retorna bytes."""
    resp = requests.get(url, stream=True, timeout=60)
    resp.raise_for_status()
    total = int(resp.headers.get("content-length", 0))

    chunks = []
    if HAS_TQDM and destino:
        with open(destino, "wb") as f, tqdm(
            total=total, unit="B", unit_scale=True, desc="Baixando CSV ODEPA 2026"
        ) as bar:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)
                    chunks.append(chunk)
                    bar.update(len(chunk))
    else:
        for chunk in resp.iter_content(chunk_size=1024 * 1024):
            if chunk:
                chunks.append(chunk)
        if destino:
            with open(destino, "wb") as f:
                for chunk in chunks:
                    f.write(chunk)

    return b"".join(chunks)


def validar_csv(data: bytes):
    inicio = data[:4096].decode("utf-8", errors="ignore").lower()
    marcadores_html = ["<!doctype html", "<html", "<head", "<body", "not found", "404", "ckan"]
    if any(m in inicio for m in marcadores_html):
        raise ValueError("Arquivo baixado parece ser HTML/erro, não CSV.")


def ler_csv_robusto(data: bytes) -> pd.DataFrame:
    tentativas = [
        {"dtype": str},
        {"dtype": str, "sep": ";"},
        {"dtype": str, "encoding": "latin-1"},
        {"dtype": str, "encoding": "latin-1", "sep": ";"},
    ]
    for params in tentativas:
        try:
            return pd.read_csv(io.BytesIO(data), **params)
        except Exception:
            continue
    raise ValueError("Não foi possível interpretar o CSV da ODEPA.")


# ── TRANSFORM ─────────────────────────────────────────────────────────────────
def transform(df: pd.DataFrame, extracted_at: str) -> list[dict]:
    if "Producto" not in df.columns:
        raise KeyError(f"Coluna 'Producto' não encontrada. Colunas: {list(df.columns)}")

    df_limon = df[df["Producto"].astype(str).str.upper() == "LIMÓN"].copy()
    print(f"    Registros LIMÓN: {len(df_limon)}")

    if "Fecha" in df_limon.columns:
        df_limon["Fecha"] = pd.to_datetime(df_limon["Fecha"], errors="coerce")

    records = []
    for _, row in df_limon.iterrows():
        fecha = row.get("Fecha")
        if pd.isna(fecha):
            continue

        fecha_date = fecha.date() if hasattr(fecha, "date") else fecha
        iso = fecha_date.isocalendar()

        precio_raw = row.get("Precio") or row.get("PrecioPromedio") or row.get("precio")
        try:
            precio = float(str(precio_raw).replace(",", "."))
        except:
            precio = None

        records.append({
            "fecha":        fecha_date.isoformat(),
            "semana":       int(iso.week),
            "ano":          int(iso.year),
            "producto":     str(row.get("Producto", "LIMÓN")).strip(),
            "mercado":      str(row.get("Mercado", "") or "").strip() or None,
            "presentacion": str(row.get("Presentacion", "") or row.get("presentacion", "") or "").strip() or None,
            "precio":       precio,
            "unidad":       str(row.get("Unidad", "") or "").strip() or None,
            "extracted_at": extracted_at,
        })

    return records


# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("ODEPA CHILE ETL — Preços Mayoristas Limão")
    print("=" * 60)

    extracted_at = datetime.now(timezone.utc).isoformat()

    # Download
    print("\n[1] Baixando CSV ODEPA 2026...")
    destino = ARQ_RAW if Path(PASTA_LOCAL).exists() else None
    data = baixar_arquivo(URL, destino)
    if destino:
        print(f"    Salvo em: {destino}")

    print("[2] Validando arquivo...")
    validar_csv(data)

    print("[3] Carregando CSV...")
    df = ler_csv_robusto(data)
    print(f"    {len(df)} linhas totais | colunas: {list(df.columns)}")

    print("[4] Filtrando e transformando...")
    records = transform(df, extracted_at)
    print(f"    {len(records)} registros de LIMÓN")

    if not records:
        print("❌ Nenhum registro obtido.")
        return

    # Salvar CSV ETL
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=records[0].keys())
        writer.writeheader()
        writer.writerows(records)
    print(f"    Salvo: {OUTPUT_CSV}")

    # Salvar Excel local (ownCloud) se pasta existir
    if Path(PASTA_LOCAL).exists():
        df_limon = pd.DataFrame(records)
        df_limon.to_excel(ARQ_EXCEL, index=False)
        print(f"    Excel local: {ARQ_EXCEL}")

    # Preview
    print("\nPreview (3 primeiros):")
    for r in records[:3]:
        print(f"  {r['fecha']} | sem {r['semana']}/{r['ano']} | {r['mercado']} | {r['precio']} CLP/kg")

    # Upsert Supabase
    try:
        from supabase_upsert import upsert
        result = upsert("chile_precos", records, on_conflict="fecha,mercado,presentacion")
        print(f"    Supabase: {result['inserted']} registros inseridos")
        if result["errors"]:
            print(f"    ⚠️  Erros: {result['errors']}")
    except Exception as e:
        print(f"    ⚠️  Supabase skipped: {e}")

    print("\n" + "=" * 60)
    print("CONCLUÍDO")


if __name__ == "__main__":
    main()
