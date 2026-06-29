"""
ETL Master — BI Limão TFruits
==============================
Orquestra todos os scripts ETL em sequência.
Cada ETL salva CSV local + faz upsert no Supabase.

Uso:
    python etl_master.py              # roda todos
    python etl_master.py --skip clima # pula os de clima

ETLs executados:
    1. etl_hfbrasil.py          → brasil_precos
    2. pipeline_odepa_limon.py  → chile_precos
    3. etl_comexstat.py         → comexstat_exportacoes
    4. etl_aschenberg_playwright.py → containers
    5. etl_europa_franceagrimer.py  → europa_precos
    6. etl_clima_brasil.py      → clima_brasil_atual + clima_brasil_forecast
    7. etl_clima_openweather.py → clima_forecast
"""

import sys
import os
import time
import subprocess
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).parent

ETLS = [
    {"name": "HF Brasil",       "script": "etl_hfbrasil.py",              "tag": "brasil",     "optional": True},
    {"name": "ODEPA Chile",     "script": "pipeline_odepa_limon.py",       "tag": "chile"},
    {"name": "Comexstat",       "script": "etl_comexstat.py",              "tag": "comexstat"},
    {"name": "Aschenberg",      "script": "etl_aschenberg_playwright.py",  "tag": "aschenberg"},
    {"name": "Europa",          "script": "etl_europa_franceagrimer.py",   "tag": "europa"},
    {"name": "Clima Brasil",    "script": "etl_clima_brasil.py",           "tag": "clima"},
    {"name": "Clima Forecast",  "script": "etl_clima_openweather.py",      "tag": "clima"},
]


def run_etl(etl: dict) -> dict:
    script = BASE_DIR / etl["script"]
    if not script.exists():
        return {"name": etl["name"], "status": "SKIP", "msg": "script não encontrado", "elapsed": 0}

    start = time.time()
    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(BASE_DIR),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    elapsed = round(time.time() - start, 1)

    if result.returncode == 0:
        # Extrair linha de Supabase do output
        supabase_lines = [l for l in result.stdout.splitlines() if "Supabase" in l]
        msg = supabase_lines[-1].strip() if supabase_lines else "OK"
        return {"name": etl["name"], "status": "OK", "msg": msg, "elapsed": elapsed}
    else:
        last_err = result.stderr.strip().splitlines()[-1] if result.stderr else "erro desconhecido"
        return {"name": etl["name"], "status": "ERRO", "msg": last_err[:100], "elapsed": elapsed}


def main():
    skip_tags = set()
    for arg in sys.argv[1:]:
        if arg.startswith("--skip"):
            parts = arg.split()
            if len(parts) > 1:
                skip_tags.add(parts[1])
            elif "=" in arg:
                skip_tags.add(arg.split("=")[1])

    print("=" * 60)
    print(f"ETL MASTER — BI Limão TFruits")
    print(f"Início: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 60)

    results = []
    for etl in ETLS:
        if etl["tag"] in skip_tags:
            print(f"\n  ⏭  {etl['name']} — skipped")
            results.append({"name": etl["name"], "status": "SKIP", "msg": "", "elapsed": 0})
            continue

        print(f"\n{'─'*60}")
        print(f"  ▶  {etl['name']} ({etl['script']})")
        r = run_etl(etl)
        results.append(r)

        icon = "✅" if r["status"] == "OK" else ("⏭" if r["status"] == "SKIP" else "❌")
        print(f"  {icon} {r['status']} | {r['elapsed']}s | {r['msg']}")

    # Resumo
    print(f"\n{'='*60}")
    print("RESUMO")
    print(f"{'='*60}")
    ok   = sum(1 for r in results if r["status"] == "OK")
    err  = sum(1 for r in results if r["status"] == "ERRO")
    skip = sum(1 for r in results if r["status"] == "SKIP")
    print(f"  ✅ OK: {ok} | ❌ Erros: {err} | ⏭ Skipped: {skip}")
    print(f"  Fim: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")

    if err:
        print("\nDetalhes dos erros:")
        for r in results:
            if r["status"] == "ERRO":
                etl_def = next((e for e in ETLS if e["name"] == r["name"]), {})
                flag = " (opcional — não bloqueia)" if etl_def.get("optional") else ""
                print(f"  ❌ {r['name']}{flag}: {r['msg']}")

        # Só sai com erro se algum ETL não-opcional falhou
        critical_errors = sum(
            1 for r in results
            if r["status"] == "ERRO"
            and not next((e for e in ETLS if e["name"] == r["name"]), {}).get("optional")
        )
        if critical_errors:
            sys.exit(1)


if __name__ == "__main__":
    main()
