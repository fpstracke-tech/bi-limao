"""
ETL Aschenberg — Extração via Playwright DOM
=============================================
Faz login, navega pelos filtros e extrai dados de containers por semana.
Os dados ficam no DOM — allowDownload:false bloqueia a API direta.

Uso:
    pip install playwright
    playwright install chromium
    python etl_aschenberg_playwright.py

Saída:
    aschenberg_containers.json  — dados brutos por combinação de filtro
    aschenberg_containers.csv   — formato tabular para Supabase
"""

import json
import csv
import re
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

# ── CONFIG ────────────────────────────────────────────────────────────────────
# Carregar .env local se existir
_env = Path(__file__).parent / ".env"
if _env.exists():
    for _line in _env.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ[_k.strip()] = _v.strip()

URL       = "https://report.aschenberger.com.br"
USERNAME  = os.environ.get("ASCHENBERG_USER", "fesilva")
PASSWORD  = os.environ.get("ASCHENBERG_PASS", "e9NJwJ")
YEARS     = [2024, 2025, 2026]
OUTPUT_JSON = "aschenberg_containers.json"
OUTPUT_CSV  = "aschenberg_containers.csv"

# Filtros conforme configuração atual do relatório
FILTER_COMBINATIONS = [
    ("Brasil - All", "Europe - All (Med. + N. Europe + UK)"),
]

# ── HELPERS ───────────────────────────────────────────────────────────────────
def parse_table(text: str, years: list) -> list[dict]:
    """
    Parseia o bloco de texto da tabela WK do DOM.
    Formato esperado:
        Week\n2026\n2025\n2024\nWK 28\n187\n5\n197\n...
    """
    rows = []
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]

    # Encontrar onde começa a tabela (após "Week")
    try:
        start = lines.index("Week")
    except ValueError:
        return rows

    # Próximas linhas devem ser os anos
    header_years = []
    i = start + 1
    while i < len(lines) and re.match(r'^\d{4}$', lines[i]):
        header_years.append(int(lines[i]))
        i += 1

    if not header_years:
        return rows

    # Parsear linhas de WK
    while i < len(lines):
        line = lines[i]
        if re.match(r'^WK\s+\d+', line):
            week_label = line  # ex: "WK 28"
            week_num = int(re.search(r'\d+', week_label).group())
            values = {}
            for yr in header_years:
                i += 1
                if i < len(lines):
                    val_str = lines[i].replace(',', '.').strip()
                    try:
                        values[yr] = float(val_str) if val_str != '-' else None
                    except ValueError:
                        values[yr] = None
            rows.append({"week": week_num, "values": values})
        elif line.lower() == "total":
            # Pegar totais
            totals = {}
            i += 1  # pular linha de anos repetida
            for yr in header_years:
                i += 1
                if i < len(lines):
                    val_str = lines[i].replace(',', '.').strip()
                    try:
                        totals[yr] = float(val_str) if val_str != '-' else None
                    except ValueError:
                        totals[yr] = None
            rows.append({"week": "TOTAL", "values": totals})
            break
        i += 1

    return rows


def select_filter(page, label: str, dropdown_label: str):
    """Seleciona uma opção em dropdown From ou To."""
    # Encontrar o label do dropdown e clicar
    try:
        # Os dropdowns têm texto "From:" e "To:"
        container = page.locator(f"text={dropdown_label}").first
        # Clicar no dropdown pai
        parent = container.locator("..").first
        parent.click()
        time.sleep(0.5)
        # Clicar na opção
        page.locator(f"text={label}").first.click()
        time.sleep(1)
        return True
    except Exception as e:
        print(f"    ⚠️  Erro ao selecionar {dropdown_label}={label}: {e}")
        return False


def wait_for_table(page, timeout=10):
    """Aguarda qualquer linha WK aparecer no DOM."""
    try:
        page.wait_for_function(
            "document.body.innerText.includes('WK ')",
            timeout=timeout * 1000
        )
        return True
    except PwTimeout:
        return False


# ── EXTRAÇÃO PRINCIPAL ────────────────────────────────────────────────────────
def extract_all(page) -> list[dict]:
    all_data = []
    from_label, to_label = FILTER_COMBINATIONS[0]

    # ── Aplicar filtros From / To ──────────────────────────────────────────────
    print(f"\n  Aplicando filtros: From={from_label} | To={to_label}")
    try:
        # From: primeiro select com "Brasil" ou label "From:"
        from_sel = page.locator("select").filter(has_text="Brasil").first
        if from_sel.count():
            from_sel.select_option(label=from_label)
            time.sleep(1)
            print(f"    ✅ From: {from_label}")
        else:
            print(f"    ⚠️  Select 'From' não encontrado")
    except Exception as e:
        print(f"    ⚠️  Erro ao setar From: {e}")

    try:
        # To: select que contém "Europe"
        to_sel = page.locator("select").filter(has_text="Europe").first
        if to_sel.count():
            to_sel.select_option(label=to_label)
            time.sleep(1)
            print(f"    ✅ To: {to_label}")
        else:
            print(f"    ⚠️  Select 'To' não encontrado")
    except Exception as e:
        print(f"    ⚠️  Erro ao setar To: {e}")

    wait_for_table(page, timeout=10)

    for flow in ["Shipped", "Arrivals"]:
        print(f"\n  Fluxo: {flow} | {from_label} → {to_label}")

        # Shipped/Arrivals são <option> dentro de <select> — usar select_option
        try:
            sel = page.locator("select").filter(has_text=flow).first
            sel.select_option(label=flow)
            time.sleep(2)
        except Exception as e:
            print(f"    ⚠️  Erro ao selecionar {flow}: {e}")
            continue

        # Aguardar tabela renderizar
        if not wait_for_table(page, timeout=10):
            print(f"    ⚠️  Tabela não carregou para {flow}")
            continue

        # Extrair texto da página e parsear
        body_text = page.inner_text("body")
        rows = parse_table(body_text, YEARS)
        print(f"    {len([r for r in rows if r['week'] != 'TOTAL'])} semanas + total")

        for row in rows:
            for year, value in row["values"].items():
                all_data.append({
                    "flow":           flow,
                    "from_zone":      from_label,
                    "to_zone":        to_label,
                    "week":           row["week"],
                    "year":           year,
                    "containers":     value,
                    "extracted_at":   datetime.now(timezone.utc).isoformat()
                })

    return all_data


# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("ASCHENBERG ETL — Playwright DOM")
    print("=" * 60)

    all_data = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # ── LOGIN ──────────────────────────────────────────────────────
        print("\n[1] Abrindo site...")
        page.goto(URL, wait_until="networkidle")

        print("[2] Fazendo login...")
        page.fill("input[type='text']", USERNAME)
        page.fill("input[type='password']", PASSWORD)
        page.click("button:has-text('Ok')")

        # Aguardar Summary aparecer
        try:
            page.wait_for_selector("text=Summary", timeout=15000)
            print("    ✅ Login OK")
        except PwTimeout:
            print("    ❌ Timeout aguardando login")
            browser.close()
            return

        # ── AGUARDAR DADOS INICIAIS ────────────────────────────────────
        print("[3] Aguardando dados iniciais...")
        wait_for_table(page, timeout=15)

        # ── FILTRO: By Sea (40' Containers) ────────────────────────────
        print("[4] Selecionando filtro 'By Sea (40' Containers)'...")
        try:
            sel_transport = page.locator("select").filter(has_text="By Sea").first
            if sel_transport.count():
                sel_transport.select_option(label="By Sea (40' Containers)")
                time.sleep(1.5)
                print("    ✅ Filtro de transporte OK")
            else:
                # Tentar por posição (segundo select na barra de filtros)
                selects = page.locator("select").all()
                if len(selects) >= 2:
                    selects[1].select_option(label="By Sea (40' Containers)")
                    time.sleep(1.5)
                    print("    ✅ Filtro de transporte OK (por posição)")
                else:
                    print("    ⚠️  Filtro de transporte não encontrado — continuando")
        except Exception as e:
            print(f"    ⚠️  Erro ao selecionar transporte: {e}")

        # ── FILTRO: Aba Limes (Tahiti) ─────────────────────────────────
        print("[5] Selecionando aba 'Limes (Tahiti)'...")
        try:
            page.locator("text=Limes (Tahiti)").first.click()
            time.sleep(2)
            wait_for_table(page, timeout=10)
            print("    ✅ Aba Limes (Tahiti) OK")
        except Exception as e:
            print(f"    ⚠️  Erro ao selecionar aba Limes (Tahiti): {e}")

        # ── EXTRAIR DADOS ──────────────────────────────────────────────
        print("[6] Extraindo combinações de filtro...")
        all_data = extract_all(page)

        browser.close()

    print(f"\n[5] Total de registros: {len(all_data)}")

    # ── SALVAR JSON ────────────────────────────────────────────────────
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2)
    print(f"    Salvo: {OUTPUT_JSON}")

    # ── SALVAR CSV ─────────────────────────────────────────────────────
    if all_data:
        with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=all_data[0].keys())
            writer.writeheader()
            writer.writerows(all_data)
        print(f"    Salvo: {OUTPUT_CSV}")

    # ── UPSERT SUPABASE ────────────────────────────────────────────────
    if all_data:
        try:
            from supabase_upsert import upsert
            # Mapear campos para a tabela containers
            rows = [{
                "flow":        r["flow"],
                "from_zone":   r["from_zone"],
                "to_zone":     r["to_zone"],
                "week":        r["week"] if r["week"] != "TOTAL" else None,
                "year":        r["year"],
                "containers":  r["containers"],
                "extracted_at": r["extracted_at"],
            } for r in all_data if r["week"] != "TOTAL"]
            result = upsert("containers", rows, on_conflict="flow,from_zone,to_zone,week,year")
            print(f"    Supabase: {result['inserted']} registros inseridos")
            if result["errors"]:
                print(f"    ⚠️  Erros: {result['errors']}")
        except Exception as e:
            print(f"    ⚠️  Supabase skipped: {e}")

    print("\n" + "=" * 60)
    print("CONCLUÍDO")


if __name__ == "__main__":
    main()
