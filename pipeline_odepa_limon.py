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
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-CL,es;q=0.9",
    "Referer": "https://datos.odepa.gob.cl/",
}


def baixar_arquivo(url: str, destino: str = None) -> bytes:
    """Baixa o CSV e salva localmente se destino fornecido. Retorna bytes."""
    resp = requests.get(url, headers=BROWSER_HEADERS, stream=True, timeout=60)
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
def _find_col(df, *candidates):
    """Retorna o primeiro nome de coluna que existir no df (case-insensitive)."""
    cols_lower = {c.lower(): c for c in df.columns}
    for c in candidates:
        if c.lower() in cols_lower:
            return cols_lower[c.lower()]
    return None


def transform(df: pd.DataFrame, extracted_at: str) -> list[dict]:
    print(f"    Colunas CSV: {list(df.columns)}")

    # Coluna de produto — pode ser 'Producto', 'Producto ' etc.
    col_prod = _find_col(df, "Producto", "Producto ", "producto")
    if col_prod is None:
        raise KeyError(f"Coluna 'Producto' nao encontrada. Colunas: {list(df.columns)}")

    # Normaliza texto: remove acentos para comparação robusta
    import unicodedata, re as _re
    def norm(s):
        return unicodedata.normalize("NFD", str(s)).encode("ascii", "ignore").decode().upper().strip()

    mask = df[col_prod].astype(str).apply(norm).isin(["LIMON", "LIMA", "LIMON TAHITI", "LIMON ACIDO"])
    df_limon = df[mask].copy()
    if len(df_limon) == 0:
        mask2 = df[col_prod].astype(str).apply(norm).str.contains("LIMON")
        df_limon = df[mask2].copy()
    print(f"    Registros LIMON: {len(df_limon)}")
    if len(df_limon) == 0:
        print(f"    Produtos encontrados: {df[col_prod].astype(str).apply(norm).unique()[:20]}")
        return []

    col_fecha   = _find_col(df_limon, "Fecha", "fecha")
    col_precio  = _find_col(df_limon, "Precio promedio", "Precio", "PrecioPromedio", "precio")
    col_mercado = _find_col(df_limon, "Mercado", "mercado")
    col_pres    = _find_col(df_limon, "Calidad", "Presentacion", "presentacion")
    col_unidad  = _find_col(df_limon, "Unidad de comercializacion", "Unidad de comercialización", "Unidad", "unidad")

    print(f"    Mapeamento: fecha={col_fecha}, precio={col_precio}, mercado={col_mercado}, unidad={col_unidad}")

    # Filtrar apenas unidade "$/malla 18 kilos"
    if col_unidad:
        df_limon = df_limon[df_limon[col_unidad].astype(str).str.strip() == "$/malla 18 kilos"].copy()
        print(f"    Após filtro '$/malla 18 kilos': {len(df_limon)} registros")
        if len(df_limon) == 0:
            unidades_disp = df[col_unidad].astype(str).unique()[:20]
            print(f"    Unidades disponíveis: {unidades_disp}")
            return []
    else:
        print("    ⚠️  Coluna 'Unidad de comercialización' não encontrada — filtro ignorado")

    if col_fecha:
        df_limon = df_limon.copy()
        df_limon[col_fecha] = pd.to_datetime(df_limon[col_fecha], errors="coerce")

    def _parse_precio_clp_kg(precio_raw, unidad_str):
        """Converte preço da unidade para CLP/kg."""
        try:
            total = float(str(precio_raw).replace(",", ".").replace(" ", ""))
        except Exception:
            return None
        if not unidad_str:
            return round(total, 2)
        s = str(unidad_str).lower()
        # Extrai kg da string: '$/bandeja 15 kilos', '$/malla 18 kilos', etc.
        m = _re.search(r'(\d+(?:[.,]\d+)?)\s*kilos?', s)
        if m:
            kg = float(m.group(1).replace(",", "."))
            return round(total / kg, 2) if kg > 0 else None
        # $/kilo ou $/kg = já é por kg
        if "$/kilo" in s or "$/kg" in s:
            return round(total, 2)
        # bins (450/400 kilos)
        m2 = _re.search(r'bins?\s*[(\[]?\s*(\d+)\s*kilos?', s)
        if m2:
            kg = float(m2.group(1))
            return round(total / kg, 2) if kg > 0 else None
        return round(total, 2)  # fallback sem normalização

    records = []
    for _, row in df_limon.iterrows():
        fecha = row[col_fecha] if col_fecha else None
        if fecha is None or pd.isnull(fecha):
            continue
        fecha_date = fecha.date() if hasattr(fecha, "date") else fecha
        iso = fecha_date.isocalendar()

        precio_raw = row[col_precio] if col_precio else None
        unidad_str = str(row[col_unidad]) if col_unidad and pd.notna(row[col_unidad]) else ""
        precio_kg  = _parse_precio_clp_kg(precio_raw, unidad_str)

        records.append({
            "fecha":        fecha_date.isoformat(),
            "semana":       int(iso.week),
            "ano":          int(iso.year),
            "producto":     str(row[col_prod]).strip(),
            "mercado":      str(row[col_mercado] or "").strip() or None if col_mercado else None,
            "presentacion": str(row[col_pres] or "").strip() or None if col_pres else None,
            "precio":       precio_kg,
            "unidad":       "CLP/kg",
            "extracted_at": extracted_at,
        })

    print(f"    Com preco valido: {sum(1 for r in records if r['precio'] is not None)}")

    # Deduplicar por (fecha, mercado, presentacion) — média de precio_kg
    from collections import defaultdict
    grupos = defaultdict(list)
    for r in records:
        key = (r["fecha"], r["mercado"] or "", r["presentacion"] or "")
        if r["precio"] is not None:
            grupos[key].append(r)

    dedup = []
    for key, rows in grupos.items():
        avg = round(sum(r["precio"] for r in rows) / len(rows), 2)
        r0 = rows[0].copy()
        r0["precio"] = avg
        dedup.append(r0)

    print(f"    Apos dedup: {len(dedup)} registros unicos")
    if dedup:
        ex = dedup[0]
        print(f"    Ex normalizado: {ex['fecha']} | {ex['mercado']} | {ex['precio']} CLP/kg")
    return dedup


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
