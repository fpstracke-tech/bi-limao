"""
ETL Europa — FranceAgriMer (rnm.franceagrimer.fr)
==================================================
Baixa o arquivo .slk de preços semanais de limão na Europa via Playwright,
converte para DataFrame e salva CSV/JSON para o Supabase.

Equivale ao fluxo Power Automate anterior:
  Chrome → click "Voir hebdomadaires" → salva .slk → converte para .xlsx

Uso:
    pip install playwright xlrd --break-system-packages
    python -m playwright install chromium
    python etl_europa_franceagrimer.py

Saída:
    europa_precos.csv   — formato tabular para Supabase
    europa_precos.json  — backup JSON
"""

import re
import json
import csv
import time
import tempfile
import os
from pathlib import Path
from datetime import datetime, timezone
import pandas as pd
from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

# ── CONFIG ─────────────────────────────────────────────────────────────────────
URL_SITE     = "https://rnm.franceagrimer.fr/prix?LIME&12MOIS"
OUTPUT_CSV   = "europa_precos.csv"
OUTPUT_JSON  = "europa_precos.json"

# ── PARSE SLK ──────────────────────────────────────────────────────────────────
def parse_slk(content: str) -> pd.DataFrame:
    """
    Parseia arquivo SYLK (.slk) do FranceAgriMer.

    Formato SYLK real:
      - Linha F;...;Xc;Yr  → define posição atual (col c, row r) sem valor
      - Linha C;Xc;Yr;K"val"  → célula com posição explícita
      - Linha C;K"val"  → célula na posição atual (herdada do F anterior)
      - C incrementa coluna automaticamente a cada célula sem X explícito
    """
    rows = {}
    cur_row, cur_col = 1, 1

    for line in content.splitlines():
        line = line.strip()

        # Linha F: pode definir posição atual (X e/ou Y)
        if line.startswith("F;"):
            parts = line.split(";")
            for p in parts:
                if p.startswith("Y") and p[1:].isdigit():
                    cur_row = int(p[1:])
                elif p.startswith("X") and p[1:].isdigit():
                    cur_col = int(p[1:])
            continue

        # Linha C: célula com valor
        if not line.startswith("C;"):
            continue

        parts = line.split(";")
        r, c, val = None, None, None

        for p in parts[1:]:  # pular o "C" inicial
            if p.startswith("Y") and p[1:].lstrip("-").isdigit():
                r = int(p[1:])
            elif p.startswith("X") and p[1:].isdigit():
                c = int(p[1:])
            elif p.startswith("K"):
                # Valor pode conter ";" — pegar tudo a partir do K
                k_idx = line.index(";K") + 2
                raw = line[k_idx:]
                if raw.startswith('"'):
                    # string: remover aspas envolventes (pode ter aspas internas escapadas)
                    val = raw.strip('"')
                else:
                    try:
                        val = float(raw) if "." in raw else int(raw)
                    except:
                        val = raw if raw else None
                break  # K é sempre o último campo relevante

        # Aplicar posição: usar X/Y explícito ou herdar posição atual
        if r is not None:
            cur_row = r
        if c is not None:
            cur_col = c

        if val is not None and val != "":
            if cur_row not in rows:
                rows[cur_row] = {}
            rows[cur_row][cur_col] = val

        # Incrementar coluna para próxima célula sem X explícito
        cur_col += 1

    if not rows:
        return pd.DataFrame()

    max_row = max(rows.keys())
    max_col = max(c for cols in rows.values() for c in cols.keys())
    data = []
    for r in range(1, max_row + 1):
        row_data = []
        for c in range(1, max_col + 1):
            row_data.append(rows.get(r, {}).get(c, None))
        data.append(row_data)

    return pd.DataFrame(data)


def transform_slk_df(df: pd.DataFrame, extracted_at: str) -> list[dict]:
    """
    Aplica a mesma lógica de transformação do Power Query (Base_Europa):
    - Skip 2 linhas de header, remove 3 últimas
    - Promote headers (linha 3 do .slk = Stade, Marché, Libellé, Unité, s1 YYYY, s2 YYYY, ...)
    - Unpivot colunas de semana
    - Extrai Semana e Ano
    - Calcula Preco_4_5kg = Preco * 4.5 * 0.7
    - Detecta Modal (avion=Aéreo, bateau=Marítimo)
    - Filtra fora Aéreo
    """
    if df.empty:
        return []

    # Y1=título, Y2=subtítulo, Y3=vazia → skip 3
    # Y4=headers, Y5–Y13=dados, Y14–Y16=notas → remover linhas sem Stade/Grossiste
    df = df.iloc[3:].reset_index(drop=True)   # skip Y1, Y2, Y3

    # Promote headers (agora linha 0 = Y4 = Stade, Marché, Libellé, Unité, s27 2025, ...)
    df.columns = [str(v) if v is not None else f"col_{i}" for i, v in enumerate(df.iloc[0])]
    df = df.iloc[1:].reset_index(drop=True)

    # Remover linhas que não são dados (notas no final: sem valor na col 0)
    df = df[df.iloc[:, 0].notna() & (df.iloc[:, 0] != "")].reset_index(drop=True)

    # Identificar colunas fixas (primeiras 4) e colunas de semana
    fixed_cols = list(dict.fromkeys(df.columns[:4]))   # dedup se houver nomes iguais
    # Renomear fixas com índice se duplicadas
    seen = {}
    new_cols = []
    for col in df.columns:
        if col in seen:
            seen[col] += 1
            new_cols.append(f"{col}_{seen[col]}")
        else:
            seen[col] = 0
            new_cols.append(col)
    df.columns = new_cols
    fixed_cols = list(df.columns[:4])
    week_cols  = list(df.columns[4:])

    # Unpivot: melt das colunas de semana
    df_melted = df.melt(
        id_vars=fixed_cols,
        value_vars=week_cols,
        var_name="Semana_Raw",
        value_name="Preco"
    )

    # Remover linhas sem preço
    df_melted = df_melted.dropna(subset=["Preco"])
    df_melted = df_melted[df_melted["Preco"] != ""]

    # Parsear "s28 2025" → Semana=28, Ano=2025
    def parse_semana(raw):
        raw = str(raw).strip()
        m = re.match(r's?(\d+)\s+(\d{4})', raw, re.IGNORECASE)
        if m:
            return int(m.group(1)), int(m.group(2))
        return None, None

    df_melted[["Semana", "Ano"]] = df_melted["Semana_Raw"].apply(
        lambda x: pd.Series(parse_semana(x))
    )
    df_melted = df_melted.dropna(subset=["Semana", "Ano"])
    df_melted["Semana"] = df_melted["Semana"].astype(int)
    df_melted["Ano"]    = df_melted["Ano"].astype(int)

    # Normalizar nomes de colunas (FR → PT)
    col_map = {}
    for col in fixed_cols:
        col_lower = str(col).lower()
        if "stade" in col_lower:
            col_map[col] = "Stade"
        elif "march" in col_lower or "mercado" in col_lower:
            col_map[col] = "Mercado"
        elif "libell" in col_lower or "produto" in col_lower or "libel" in col_lower:
            col_map[col] = "Produto"
        elif "unit" in col_lower or "unidade" in col_lower:
            col_map[col] = "Unidade"
    df_melted = df_melted.rename(columns=col_map)

    # Converter Preco para float
    df_melted["Preco"] = pd.to_numeric(df_melted["Preco"], errors="coerce")
    df_melted = df_melted.dropna(subset=["Preco"])

    # Calcular Preco_4_5kg = Preco * 4.5 * 0.7  (€/kg → €/cx 4.5kg, ~30% margem)
    df_melted["Preco_4_5kg"] = (df_melted["Preco"] * 4.5 * 0.7).round(2)

    # Detectar Modal
    def detect_modal(produto):
        p = str(produto).lower()
        if "avion" in p:
            return "Aéreo"
        elif "bateau" in p:
            return "Marítimo"
        return None

    df_melted["Modal"] = df_melted.get("Produto", pd.Series(dtype=str)).apply(detect_modal)

    # Filtrar fora Aéreo
    df_melted = df_melted[df_melted["Modal"] != "Aéreo"]

    # Semana formatada e Ano-Semana
    df_melted["Semana_Formatada"] = df_melted["Semana"].apply(lambda x: str(x).zfill(2))
    df_melted["Ano_Semana"]       = df_melted["Ano"].astype(str) + "-" + df_melted["Semana_Formatada"]

    # Timestamp de extração
    df_melted["extracted_at"] = extracted_at

    # Selecionar e ordenar colunas finais
    out_cols = ["Stade", "Mercado", "Produto", "Unidade", "Semana", "Semana_Formatada",
                "Ano", "Preco", "Preco_4_5kg", "Modal", "Ano_Semana", "extracted_at"]
    final_cols = [c for c in out_cols if c in df_melted.columns]

    return df_melted[final_cols].to_dict(orient="records")


# ── DOWNLOAD VIA PLAYWRIGHT ────────────────────────────────────────────────────
def download_slk_playwright() -> str | None:
    """
    Abre o site do FranceAgriMer, aguarda renderizar e clica no link
    'Voir ... hebdomadaires' para baixar o .slk.
    Retorna o conteúdo do arquivo como string, ou None se falhar.
    """
    slk_content = None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(accept_downloads=True)
        page    = context.new_page()

        print("[1] Abrindo FranceAgriMer...")
        page.goto(URL_SITE, wait_until="networkidle", timeout=30000)

        # Aguardar página carregar (JS-rendered)
        print("[2] Aguardando renderização...")
        try:
            # Esperar link de download aparecer (contém "hebdomadaires" ou "Voir")
            page.wait_for_selector("a[href*='.slk'], a:has-text('hebdomadaires'), a:has-text('Voir')",
                                   timeout=20000)
        except PwTimeout:
            print("    ⚠️  Link de download não encontrado por seletor, tentando por texto...")

        time.sleep(3)  # garantir renderização completa

        # Tentar encontrar o link de download do .slk
        slk_url = None

        # Estratégia 1: buscar href com .slk
        slk_link = page.locator("a[href*='.slk']").first
        if slk_link.count() > 0:
            href = slk_link.get_attribute("href")
            if href:
                slk_url = href if href.startswith("http") else f"https://rnm.franceagrimer.fr{href}"
                print(f"    ✅ URL .slk encontrada: {slk_url}")

        # Estratégia 2: clicar no link "hebdomadaires" e capturar download
        if not slk_url:
            print("[3] Tentando click + captura de download...")
            try:
                with page.expect_download(timeout=15000) as dl_info:
                    link = page.locator("text=hebdomadaires").first
                    link.click()
                download = dl_info.value
                with tempfile.NamedTemporaryFile(delete=False, suffix=".slk") as tmp:
                    download.save_as(tmp.name)
                    with open(tmp.name, "r", encoding="latin-1", errors="replace") as f:
                        slk_content = f.read()
                os.unlink(tmp.name)
                print(f"    ✅ Download capturado: {len(slk_content)} chars")
            except Exception as e:
                print(f"    ⚠️  Falha no download por click: {e}")

        # Estratégia 3: buscar URL .slk nos network requests
        if not slk_url and not slk_content:
            print("[3b] Buscando URL .slk nos requests da página...")
            # Inspecionar JS da página para encontrar o link
            links = page.evaluate("""
                () => Array.from(document.querySelectorAll('a'))
                         .map(a => ({text: a.innerText.trim(), href: a.href}))
                         .filter(a => a.href.includes('.slk') || a.text.toLowerCase().includes('hebdo'))
            """)
            print(f"    Links encontrados: {links}")
            if links:
                slk_url = links[0]["href"]
                print(f"    ✅ URL via JS: {slk_url}")

        # Se temos URL direta, baixar com requests
        if slk_url and not slk_content:
            import requests
            r = requests.get(slk_url, timeout=30)
            slk_content = r.content.decode("latin-1", errors="replace")
            print(f"    ✅ Conteúdo baixado via requests: {len(slk_content)} chars")

        # Fallback: tentar ler da pasta local (ownCloud sync)
        if not slk_content:
            local_paths = [
                r"C:\Users\fpstr\ownCloud\TFruits PowerBI\Projeto Report Limão\Europa\Renomeado\limao_europa.xlsx",
                r"C:\Users\fpstr\ownCloud\TFruits PowerBI\Projeto Report Limão\Europa\sheets\PREÇOS_SEMANAIS_EUROPA_LIMPO.xlsx",
            ]
            print("    ⚠️  Não foi possível baixar o .slk. Verifique a URL do site.")
            print("    💡  Fallback: lendo arquivos locais do ownCloud...")

        browser.close()

    return slk_content


# ── FALLBACK: LER XLSX LOCAL ───────────────────────────────────────────────────
def read_local_xlsx(extracted_at: str) -> list[dict]:
    """
    Fallback: lê os arquivos Excel locais (ownCloud sync) se o download falhar.
    Replica a lógica de Base_Europa + Historico_Europa → Base_EuropaTotal.
    """
    records = []

    # --- Base_Europa (limao_europa.xlsx - formato wide) ---
    path_base = r"C:\Users\fpstr\ownCloud\TFruits PowerBI\Projeto Report Limão\Europa\Renomeado\limao_europa.xlsx"
    if Path(path_base).exists():
        print(f"    Lendo {path_base}...")
        df = pd.read_excel(path_base, header=None)

        # Skip 2 linhas + remove 3 últimas
        df = df.iloc[2:-3].reset_index(drop=True)

        # Promote headers
        df.columns = df.iloc[0]
        df = df.iloc[1:].reset_index(drop=True)

        fixed_cols = list(df.columns[:4])
        week_cols  = list(df.columns[4:])

        df_m = df.melt(id_vars=fixed_cols, value_vars=week_cols,
                       var_name="Semana_Raw", value_name="Preco")
        df_m = df_m.dropna(subset=["Preco"])
        df_m = df_m[df_m["Preco"] != ""]

        def parse_sem(raw):
            raw = str(raw).strip()
            m = re.match(r's?(\d+)\s+(\d{4})', raw, re.IGNORECASE)
            if m:
                return int(m.group(1)), int(m.group(2))
            return None, None

        df_m[["Semana", "Ano"]] = df_m["Semana_Raw"].apply(
            lambda x: pd.Series(parse_sem(x))
        )
        df_m = df_m.dropna(subset=["Semana", "Ano"])
        df_m["Semana"]    = df_m["Semana"].astype(int)
        df_m["Ano"]       = df_m["Ano"].astype(int)
        df_m["Preco"]     = pd.to_numeric(df_m["Preco"], errors="coerce")
        df_m              = df_m.dropna(subset=["Preco"])
        df_m["Preco_4_5kg"] = (df_m["Preco"] * 4.5 * 0.7).round(2)

        col_map = {}
        for col in fixed_cols:
            col_lower = str(col).lower()
            if "stade" in col_lower:               col_map[col] = "Stade"
            elif "march" in col_lower:             col_map[col] = "Mercado"
            elif "libell" in col_lower:            col_map[col] = "Produto"
            elif "unit" in col_lower:              col_map[col] = "Unidade"
        df_m = df_m.rename(columns=col_map)

        def detect_modal(p):
            s = str(p).lower()
            if "avion" in s: return "Aéreo"
            if "bateau" in s: return "Marítimo"
            return None

        df_m["Modal"] = df_m.get("Produto", pd.Series(dtype=str)).apply(detect_modal)
        df_m = df_m[df_m["Modal"] != "Aéreo"]
        df_m["Semana_Formatada"] = df_m["Semana"].apply(lambda x: str(x).zfill(2))
        df_m["Ano_Semana"]       = df_m["Ano"].astype(str) + "-" + df_m["Semana_Formatada"]
        df_m["extracted_at"]     = extracted_at
        df_m["fonte"]            = "Base_Europa"

        records.extend(df_m.to_dict(orient="records"))
        print(f"    {len(df_m)} registros de Base_Europa")

    # --- Historico_Europa (PREÇOS_SEMANAIS_EUROPA_LIMPO.xlsx - formato long) ---
    path_hist = r"C:\Users\fpstr\ownCloud\TFruits PowerBI\Projeto Report Limão\Europa\sheets\PREÇOS_SEMANAIS_EUROPA_LIMPO.xlsx"
    if Path(path_hist).exists():
        print(f"    Lendo {path_hist}...")
        df_h = pd.read_excel(path_hist)
        df_h = df_h.rename(columns={"Preço": "Preco"})
        df_h["Preco_4_5kg"]      = pd.to_numeric(df_h["Preco"], errors="coerce")
        df_h["Semana"]           = df_h["Semana"].astype(int)
        df_h["Ano"]              = df_h["Ano"].astype(int)
        df_h["Semana_Formatada"] = df_h["Semana"].apply(lambda x: str(x).zfill(2))
        df_h["Ano_Semana"]       = df_h["Ano"].astype(str) + "-" + df_h["Semana_Formatada"]
        df_h["extracted_at"]     = extracted_at
        df_h["fonte"]            = "Historico_Europa"
        df_h["Modal"]            = "Marítimo"

        records.extend(df_h.to_dict(orient="records"))
        print(f"    {len(df_h)} registros de Historico_Europa")

    # Deduplicar por (Semana, Ano, Produto, Unidade) — igual ao Power Query
    if records:
        df_all = pd.DataFrame(records)
        key_cols = [c for c in ["Semana", "Ano", "Produto", "Unidade"] if c in df_all.columns]
        if key_cols:
            before = len(df_all)
            df_all = df_all.drop_duplicates(subset=key_cols, keep="first")
            print(f"    Deduplicação: {before} → {len(df_all)} registros")
        return df_all.to_dict(orient="records")

    return records


# ── MAIN ───────────────────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("EUROPA ETL — FranceAgriMer")
    print("=" * 60)

    extracted_at = datetime.now(timezone.utc).isoformat()
    records = []

    # Tentativa 1: Download do .slk via Playwright
    print("\n[Modo 1] Download via Playwright...")
    slk_content = download_slk_playwright()

    if slk_content and "ID;P" in slk_content[:20]:  # header SYLK válido
        print("\n[Parse] Processando arquivo .slk...")
        df_slk = parse_slk(slk_content)
        print(f"    Dimensões brutas: {df_slk.shape}")
        records = transform_slk_df(df_slk, extracted_at)
        print(f"    Registros transformados: {len(records)}")
    else:
        # Fallback: ler xlsx locais
        print("\n[Modo 2] Fallback — lendo arquivos Excel locais (ownCloud)...")
        records = read_local_xlsx(extracted_at)

    if not records:
        print("\n❌ Nenhum dado obtido. Verifique as fontes.")
        return

    print(f"\n[Total] {len(records)} registros")

    # Salvar CSV
    df_out = pd.DataFrame(records)
    df_out.to_csv(OUTPUT_CSV, index=False, encoding="utf-8")
    print(f"    Salvo: {OUTPUT_CSV}")

    # Salvar JSON
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2, default=str)
    print(f"    Salvo: {OUTPUT_JSON}")

    # Preview
    print(f"\nPreview (5 primeiras linhas):")
    print(df_out[["Semana", "Ano", "Preco", "Preco_4_5kg", "Modal"]].head())

    # ── UPSERT SUPABASE ────────────────────────────────────────────────
    try:
        from supabase_upsert import upsert
        col_map = {
            "Semana": "semana", "Semana_Formatada": "semana_fmt", "Ano": "ano",
            "Ano_Semana": "ano_semana", "Stade": "stade", "Mercado": "mercado",
            "Produto": "produto", "Unidade": "unidade", "Preco": "preco",
            "Preco_4_5kg": "preco_4_5kg", "Modal": "modal", "extracted_at": "extracted_at",
        }
        rows = [{col_map[k]: v for k, v in r.items() if k in col_map} for r in records]
        result = upsert("europa_precos", rows, on_conflict="semana,ano,mercado,produto")
        print(f"    Supabase: {result['inserted']} registros inseridos")
        if result["errors"]:
            print(f"    ⚠️  Erros: {result['errors']}")
    except Exception as e:
        print(f"    ⚠️  Supabase skipped: {e}")

    print("\n" + "=" * 60)
    print("CONCLUÍDO")


if __name__ == "__main__":
    main()
