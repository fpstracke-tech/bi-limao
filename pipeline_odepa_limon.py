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
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests
import pandas as pd


def week_of_year_pq(d: date) -> int:
    """
    Réplica de Date.WeekOfYear do Power Query (default):
    semana inicia no DOMINGO; semana 1 é a que contém 1º de janeiro.
    NÃO é semana ISO — mantido por fidelidade ao PBI (query Chile2026).
    Nota: a coluna Semana dos xlsx históricos da ODEPA (2023–2025) coincide
    com ISO em 100% das linhas, então o histórico já importado está correto.
    """
    jan1 = date(d.year, 1, 1)
    dias_desde_domingo = (jan1.weekday() + 1) % 7  # Mon=0 ... Sun=6
    inicio_semana1 = jan1 - timedelta(days=dias_desde_domingo)
    return (d - inicio_semana1).days // 7 + 1
try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False

# ── CONFIG ─────────────────────────────────────────────────────────────────────
# ANO dinâmico: a ODEPA publica um CSV por ano civil. Com o ano fixo no código o
# ETL continuaria baixando o ano anterior a partir de 1º de janeiro — rodaria
# "com sucesso" e os preços parariam de atualizar silenciosamente.
ANO = date.today().year

URL = (
    "https://datos.odepa.gob.cl/dataset/33f10516-acbe-4446-b633-68244b9b6b26"
    "/resource/580beca0-e87e-4dd4-9e8a-0bd92773f4a6"
    f"/download/precio_mayorista_fruta-hortaliza_{ANO}.csv"
)

PASTA_LOCAL = r"C:\Users\fpstr\ownCloud\TFruits PowerBI\Projeto Report Limão\Chile"
ARQ_RAW     = os.path.join(PASTA_LOCAL, f"odepa_raw_{ANO}.csv")
ARQ_EXCEL   = os.path.join(PASTA_LOCAL, f"{ANO}.xlsx")
OUTPUT_CSV  = "odepa_limon.csv"

# Câmbio: dólar observado do Banco Central do Chile (via mindicador.cl)
URL_CAMBIO = f"https://mindicador.cl/api/dolar/{ANO}"
CAMBIO_FIXO_PBI = 980  # usado só pelos dados 2023-2025 (cambio NULL no banco)


# ── CÂMBIO (dólar observado BCCh) ─────────────────────────────────────────────
def _preencher_dias_sem_cotacao(serie: dict[str, float], ano: int) -> dict[str, float]:
    """Fill-forward: dias sem cotação (fim de semana, feriado) herdam a última
    taxa anterior disponível — prática padrão de mercado."""
    completo, anterior = {}, None
    d, fim = date(ano, 1, 1), date(ano, 12, 31)
    for dia in (d + timedelta(days=n) for n in range((fim - d).days + 1)):
        iso = dia.isoformat()
        if iso in serie:
            anterior = serie[iso]
        if anterior is not None:
            completo[iso] = anterior
    return completo


def cambio_do_supabase(ano: int = ANO) -> dict[str, float]:
    """
    Fallback: relê a série de câmbio já persistida em `chile_precos`.

    Cada registro guarda o `cambio` da sua data, então o banco contém a própria
    série do BCCh coletada nos runs anteriores — não é estimativa, é o mesmo dado
    real, só mais antigo. Retorna {"AAAA-MM-DD": valor} sem fill-forward
    (quem chama decide até onde extrapolar).
    """
    from supabase_upsert import SUPABASE_URL, SUPABASE_KEY
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_URL/SUPABASE_KEY ausentes — fallback indisponível")

    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    url = f"{SUPABASE_URL}/rest/v1/chile_precos"
    params = {"select": "fecha,cambio", "ano": f"eq.{ano}",
              "cambio": "not.is.null", "order": "fecha.asc"}

    # PostgREST devolve no máximo 1000 linhas por página — paginar sempre.
    serie, offset, PAGINA = {}, 0, 1000
    while True:
        r = requests.get(url, headers={**headers,
                                       "Range-Unit": "items",
                                       "Range": f"{offset}-{offset + PAGINA - 1}"},
                         params=params, timeout=30)
        r.raise_for_status()
        lote = r.json()
        for row in lote:
            serie[row["fecha"][:10]] = float(row["cambio"])
        if len(lote) < PAGINA:
            break
        offset += PAGINA
    if not serie:
        raise RuntimeError(f"nenhum câmbio de {ano} gravado em chile_precos")
    return serie


def carregar_cambio(ano: int = ANO, tentativas: int = 3) -> tuple[dict[str, float], str | None]:
    """
    Retorna (mapa, estimado_apos):
      - mapa: {"AAAA-MM-DD": valor_clp_por_usd} com o dólar observado do BCCh
      - estimado_apos: None quando a API respondeu; ISO da última cotação REAL
        quando caímos no fallback do Supabase (datas depois dela são extrapolação)

    Se o mindicador.cl estiver fora do ar, em vez de abortar o ETL inteiro
    reutilizamos a série já gravada em `chile_precos` e marcamos como estimadas
    apenas as datas posteriores à última cotação conhecida. Preço com câmbio
    carregado e sinalizado é melhor que uma semana sem Chile no dashboard.
    """
    ultimo_erro = None
    for i in range(tentativas):
        try:
            r = requests.get(URL_CAMBIO, timeout=30)
            r.raise_for_status()
            serie = {p["fecha"][:10]: float(p["valor"]) for p in r.json()["serie"]}
            if not serie:
                raise ValueError("série de câmbio vazia")
            print(f"    Câmbio BCCh {ano}: {len(serie)} cotações "
                  f"(min {min(serie.values()):.1f} / max {max(serie.values()):.1f})")
            return _preencher_dias_sem_cotacao(serie, ano), None
        except Exception as e:
            ultimo_erro = e
            print(f"    ⚠ câmbio tentativa {i+1}/{tentativas} falhou: {type(e).__name__}")
            if i < tentativas - 1:
                time.sleep(10 * (i + 1))

    print(f"    ⚠ mindicador.cl indisponível ({type(ultimo_erro).__name__}) — "
          f"usando fallback: série de câmbio já gravada em chile_precos")
    try:
        serie = cambio_do_supabase(ano)
    except Exception as e:
        raise RuntimeError(
            f"Câmbio BCCh indisponível ({ultimo_erro}) e fallback do Supabase "
            f"também falhou ({e}). Abortando sem gravar para não misturar "
            f"bases de câmbio."
        )
    ultima_real = max(serie)
    print(f"    Fallback: {len(serie)} cotações de {ano} no banco, "
          f"última real {ultima_real} = {serie[ultima_real]:.2f} CLP/USD")
    return _preencher_dias_sem_cotacao(serie, ano), ultima_real


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


def baixar_arquivo(url: str, destino: str = None, tentativas: int = 4) -> bytes:
    """Baixa o CSV e salva localmente se destino fornecido. Retorna bytes.

    Faz retry com backoff em caso de timeout/erro de conexão — o servidor da
    ODEPA (datos.odepa.gob.cl) esporadicamente recusa/demora a conexão vindo
    de IPs de datacenter (ex.: runners do GitHub Actions).
    """
    ultimo_erro = None
    for tentativa in range(1, tentativas + 1):
        try:
            resp = requests.get(url, headers=BROWSER_HEADERS, stream=True, timeout=90)
            resp.raise_for_status()
            break
        except (requests.exceptions.ConnectTimeout,
                requests.exceptions.ConnectionError,
                requests.exceptions.ReadTimeout) as e:
            ultimo_erro = e
            if tentativa == tentativas:
                raise
            espera = 20 * tentativa  # 20s, 40s, 60s...
            print(f"    ⚠️  Tentativa {tentativa}/{tentativas} falhou ({type(e).__name__}). "
                  f"Retentando em {espera}s...")
            time.sleep(espera)
    total = int(resp.headers.get("content-length", 0))

    chunks = []
    if HAS_TQDM and destino:
        with open(destino, "wb") as f, tqdm(
            total=total, unit="B", unit_scale=True, desc=f"Baixando CSV ODEPA {ANO}"
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


def transform(df: pd.DataFrame, extracted_at: str,
              cambio_map: dict[str, float] | None = None,
              estimado_apos: str | None = None) -> list[dict]:
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
    # Variedad + Procedencia entram na chave de dedup junto com Calidad — sem isso
    # linhas legitimamente distintas colapsam e a média semanal sobe ~10% vs o PBI
    col_var     = _find_col(df_limon, "Variedad / Tipo", "Variedad", "variedad")
    col_orig    = _find_col(df_limon, "Origen", "Procedencia", "origen")

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

        precio_raw = row[col_precio] if col_precio else None
        unidad_str = str(row[col_unidad]) if col_unidad and pd.notna(row[col_unidad]) else ""
        precio_kg  = _parse_precio_clp_kg(precio_raw, unidad_str)

        records.append({
            "fecha":        fecha_date.isoformat(),
            "semana":       week_of_year_pq(fecha_date),  # Date.WeekOfYear (PBI, não-ISO)
            "ano":          fecha_date.year,              # ano civil (PBI usa Date.Year)
            "producto":     str(row[col_prod]).strip(),
            "mercado":      str(row[col_mercado] or "").strip() or None if col_mercado else None,
            "presentacion": "|".join(
                str(row[c] or "").strip() if c else ""
                for c in (col_var, col_pres, col_orig)
            ),
            "precio":       precio_kg,
            "unidad":       "CLP/kg",
            # Câmbio da DATA da observação — fica gravado por registro, então a
            # série não se revaloriza retroativamente nos runs seguintes.
            "cambio":       (cambio_map or {}).get(fecha_date.isoformat()),
            # True só quando o câmbio veio do fallback E a data é posterior à
            # última cotação real do BCCh (extrapolação). Fill-forward normal de
            # fim de semana/feriado NÃO é marcado — é prática padrão de mercado.
            "cambio_estimado": bool(
                estimado_apos and fecha_date.isoformat() > estimado_apos
            ),
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
    print(f"\n[1] Baixando CSV ODEPA {ANO}...")
    destino = ARQ_RAW if Path(PASTA_LOCAL).exists() else None
    data = baixar_arquivo(URL, destino)
    if destino:
        print(f"    Salvo em: {destino}")

    print("[2] Validando arquivo...")
    validar_csv(data)

    print("[3] Carregando CSV...")
    df = ler_csv_robusto(data)
    print(f"    {len(df)} linhas totais | colunas: {list(df.columns)}")

    print(f"[4] Carregando câmbio (dólar observado BCCh {ANO})...")
    cambio_map, estimado_apos = carregar_cambio()

    print("[5] Filtrando e transformando...")
    records = transform(df, extracted_at, cambio_map, estimado_apos)
    print(f"    {len(records)} registros de LIMÓN")
    sem_cambio = sum(1 for r in records if r.get("cambio") is None)
    if sem_cambio:
        print(f"    ⚠ {sem_cambio} registros sem câmbio na data (frontend usará "
              f"{CAMBIO_FIXO_PBI} fixo) — verificar cobertura da série BCCh")
    estimados = sum(1 for r in records if r.get("cambio_estimado"))
    if estimados:
        print(f"    ⚠ {estimados} registros com cambio_estimado=true (datas após "
              f"{estimado_apos}) — reprocessar quando o mindicador.cl voltar")

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
        cb = r.get("cambio")
        usd = f" → US$ {r['precio']*4.5/cb:.2f}/cx4,5kg @ {cb:.1f}" if cb and r["precio"] else ""
        print(f"  {r['fecha']} | sem {r['semana']}/{r['ano']} | {r['mercado']} | "
              f"{r['precio']} CLP/kg{usd}")

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
