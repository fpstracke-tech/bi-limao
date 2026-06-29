"""
ETL Clima — OpenWeatherMap Forecast API (mercados globais)
===========================================================
Consulta previsão de 5 dias (40 × 3h) para as cidades compradoras de limão.
Replica a tabela Weather_Forecast do Power BI.

Uso:
    pip install requests --break-system-packages
    python etl_clima_openweather.py

Saída:
    clima_forecast.csv   — dados de previsão por cidade/hora
    clima_forecast.json  — backup JSON
"""

import json
import csv
import requests
from datetime import datetime, timezone

# ── CONFIG ─────────────────────────────────────────────────────────────────────
OWM_API_KEY = "551acced990d145528dd0febe1a3bf2d"
OWM_URL     = "https://api.openweathermap.org/data/2.5/forecast"
OUTPUT_CSV  = "clima_forecast.csv"
OUTPUT_JSON = "clima_forecast.json"

# Cidades conforme tabela embutida no Power BI (Weather_Forecast)
CIDADES = [
    {"cidade": "Paris",      "pais": "FR"},
    {"cidade": "London",     "pais": "GB"},
    {"cidade": "Amsterdam",  "pais": "NL"},
    {"cidade": "Madrid",     "pais": "ES"},
    {"cidade": "Lisbon",     "pais": "PT"},
    {"cidade": "Athens",     "pais": "GR"},
    {"cidade": "Warsaw",     "pais": "PL"},
    {"cidade": "Santiago",   "pais": "CL"},
    {"cidade": "Toronto",    "pais": "CA"},
]

# ── FETCH ──────────────────────────────────────────────────────────────────────
def fetch_forecast(cidade: str, pais: str) -> list[dict]:
    """Consulta forecast de 5 dias (40 registros × 3h) para uma cidade."""
    params = {
        "q":     f"{cidade},{pais}",
        "appid": OWM_API_KEY,
        "units": "metric",
        "lang":  "pt_br",
    }
    r = requests.get(OWM_URL, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()

    records = []
    for item in data.get("list", []):
        records.append({
            "Cidade":         cidade,
            "Pais":           pais,
            "DataHora":       datetime.fromtimestamp(item["dt"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "Temp":           round(item["main"]["temp"]),
            "TempMin":        round(item["main"]["temp_min"]),
            "TempMax":        round(item["main"]["temp_max"]),
            "Humidade":       item["main"]["humidity"],
            "DescricaoTempo": item["weather"][0]["description"] if item.get("weather") else "",
            "Rain_3h":        item.get("rain", {}).get("3h", 0.0),
            "Chuva_mm":       item.get("rain", {}).get("3h", 0.0),
            "extracted_at":   datetime.now(timezone.utc).isoformat(),
        })
    return records


# ── MAIN ───────────────────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("CLIMA ETL — OpenWeatherMap Forecast (mercados globais)")
    print("=" * 60)

    all_records = []

    for entry in CIDADES:
        cidade, pais = entry["cidade"], entry["pais"]
        try:
            recs = fetch_forecast(cidade, pais)
            print(f"  ✅ {cidade},{pais}: {len(recs)} registros")
            all_records.extend(recs)
        except requests.HTTPError as e:
            print(f"  ❌ {cidade},{pais}: HTTP {e.response.status_code}")
        except Exception as e:
            print(f"  ❌ {cidade},{pais}: {e}")

    print(f"\n[Total] {len(all_records)} registros")

    if not all_records:
        print("Nenhum dado obtido.")
        return

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=all_records[0].keys())
        writer.writeheader()
        writer.writerows(all_records)
    print(f"    Salvo: {OUTPUT_CSV}")

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(all_records, f, ensure_ascii=False, indent=2)
    print(f"    Salvo: {OUTPUT_JSON}")

    print("\nPreview (3 primeiros):")
    for r in all_records[:3]:
        print(f"  {r['Cidade']} | {r['DataHora']} | {r['Temp']}°C | chuva:{r['Rain_3h']}mm")

    # ── UPSERT SUPABASE ────────────────────────────────────────────────
    try:
        from supabase_upsert import upsert
        rows = [{
            "cidade": r["Cidade"], "pais": r["Pais"],
            "data_hora": r["DataHora"], "temp_c": r["Temp"],
            "temp_min": r["TempMin"], "temp_max": r["TempMax"],
            "humidade_pct": r["Humidade"], "descricao": r["DescricaoTempo"],
            "rain_3h": r["Rain_3h"], "chuva_mm": r["Chuva_mm"],
            "extracted_at": r["extracted_at"],
        } for r in all_records]
        result = upsert("clima_forecast", rows, on_conflict="cidade,pais,data_hora")
        print(f"    Supabase: {result['inserted']} registros inseridos")
        if result["errors"]:
            print(f"    ⚠️  Erros: {result['errors']}")
    except Exception as e:
        print(f"    ⚠️  Supabase skipped: {e}")

    print("\n" + "=" * 60)
    print("CONCLUÍDO")


if __name__ == "__main__":
    main()
