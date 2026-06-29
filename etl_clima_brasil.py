"""
ETL Clima Brasil — HG Brasil Weather API (regiões produtoras)
==============================================================
Consulta clima atual + forecast 15 dias para as cidades produtoras de limão.
Replica a tabela Weather_Brasil do Power BI.

API: https://api.hgbrasil.com/weather (gratuita, WOEID-based)
Chave: 1ea8c99a (embutida no Power BI original)

Uso:
    pip install requests --break-system-packages
    python etl_clima_brasil.py

Saída:
    clima_brasil_atual.csv    — condições atuais por cidade
    clima_brasil_forecast.csv — previsão 15 dias por cidade
    clima_brasil.json         — backup JSON completo
"""

import json
import csv
import requests
from datetime import datetime, timezone

# ── CONFIG ─────────────────────────────────────────────────────────────────────
HGB_KEY    = "1ea8c99a"
HGB_URL    = "https://api.hgbrasil.com/weather"
OUT_ATUAL     = "clima_brasil_atual.csv"
OUT_FORECAST  = "clima_brasil_forecast.csv"
OUT_JSON      = "clima_brasil.json"

# Cidades conforme tabela embutida no Power BI (Weather_Brasil)
CIDADES = [
    {"cidade": "Fernando Prestes/SP", "woeid": 433580},
    {"cidade": "Marapoama/SP",        "woeid": 439542},
    {"cidade": "Matias Cardoso/MG",   "woeid": 457568},
    {"cidade": "Itaberaba/BA",        "woeid": 456249},
    {"cidade": "Tapiramuta/BA",       "woeid": 461365},
    {"cidade": "Japoatã/SE",          "woeid": 436559},
    {"cidade": "Capitão Poço/PA",     "woeid": 56123478},
    {"cidade": "Cruz das Almas/BA",   "woeid": 459274},
]

# ── FETCH ──────────────────────────────────────────────────────────────────────
def fetch_clima(cidade: str, woeid: int) -> dict | None:
    """Consulta clima atual + forecast 15 dias para uma cidade via WOEID."""
    params = {"woeid": woeid, "key": HGB_KEY}
    r = requests.get(HGB_URL, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()

    if not data.get("valid_key") or "results" not in data:
        print(f"    ⚠️  Chave inválida ou sem dados para {cidade}")
        return None

    return {"cidade_label": cidade, "woeid": woeid, "data": data["results"]}


def _parse_date_br(s: str) -> str | None:
    """Converte '29/06/2026' → '2026-06-29'. Retorna None se inválido."""
    try:
        d, m, y = s.strip().split("/")
        return f"{y}-{m.zfill(2)}-{d.zfill(2)}"
    except Exception:
        return None


def parse_atual(entrada: dict, extracted_at: str) -> dict:
    """Extrai condições atuais (1 registro por cidade)."""
    d = entrada["data"]
    return {
        "Cidade":       entrada["cidade_label"],
        "WOEID":        entrada["woeid"],
        "Data":         _parse_date_br(d.get("date", "")) or d.get("date", ""),
        "Hora":         d.get("time", ""),
        "Temp":         d.get("temp"),
        "Humidade":     d.get("humidity"),
        "Chuva_mm":     d.get("rain", 0.0),
        "Descricao":    d.get("description", ""),
        "VentoKmh":     d.get("wind_speedy", ""),
        "Nascer":       d.get("sunrise", ""),
        "Por":          d.get("sunset", ""),
        "extracted_at": extracted_at,
    }


def parse_forecast(entrada: dict, extracted_at: str) -> list[dict]:
    """Extrai forecast diário (até 15 dias por cidade)."""
    records = []
    cidade = entrada["cidade_label"]
    woeid  = entrada["woeid"]

    for fc in entrada["data"].get("forecast", []):
        raw_date = fc.get("full_date", fc.get("date", ""))
        records.append({
            "Cidade":             cidade,
            "WOEID":              woeid,
            "Data":               _parse_date_br(raw_date) or raw_date,
            "DiaSemana":          fc.get("weekday", ""),
            "TempMax":            fc.get("max"),
            "TempMin":            fc.get("min"),
            "Humidade":           fc.get("humidity"),
            "Chuva_mm":           fc.get("rain", 0.0),
            "ProbChuva_pct":      fc.get("rain_probability", 0),
            "Nebulosidade_pct":   fc.get("cloudiness", 0.0),
            "Descricao":          fc.get("description", ""),
            "Condicao":           fc.get("condition", ""),
            "extracted_at":       extracted_at,
        })
    return records


# ── MAIN ───────────────────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("CLIMA BRASIL ETL — HG Brasil Weather (regiões produtoras)")
    print("=" * 60)

    extracted_at = datetime.now(timezone.utc).isoformat()
    all_atual    = []
    all_forecast = []
    all_raw      = []

    for entry in CIDADES:
        cidade, woeid = entry["cidade"], entry["woeid"]
        try:
            resultado = fetch_clima(cidade, woeid)
            if resultado:
                all_raw.append(resultado)
                all_atual.append(parse_atual(resultado, extracted_at))
                fc = parse_forecast(resultado, extracted_at)
                all_forecast.extend(fc)
                print(f"  ✅ {cidade}: atual OK + {len(fc)} dias de forecast")
        except requests.HTTPError as e:
            print(f"  ❌ {cidade}: HTTP {e.response.status_code}")
        except Exception as e:
            print(f"  ❌ {cidade}: {e}")

    print(f"\n[Total] {len(all_atual)} cidades | {len(all_forecast)} registros de forecast")

    if not all_atual:
        print("Nenhum dado obtido.")
        return

    # Salvar atual
    with open(OUT_ATUAL, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=all_atual[0].keys())
        writer.writeheader()
        writer.writerows(all_atual)
    print(f"    Salvo: {OUT_ATUAL}")

    # Salvar forecast
    if all_forecast:
        with open(OUT_FORECAST, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=all_forecast[0].keys())
            writer.writeheader()
            writer.writerows(all_forecast)
        print(f"    Salvo: {OUT_FORECAST}")

    # ── UPSERT SUPABASE ────────────────────────────────────────────────
    try:
        from supabase_upsert import upsert, insert
        # atual — sem UNIQUE, usar insert simples (snapshot diário)
        rows_atual = [{
            "cidade":      r["Cidade"],
            "woeid":       r["WOEID"],
            "data_ref":    r["Data"] if r["Data"] else None,
            "hora_ref":    r["Hora"] or None,
            "temp_c":      r["Temp"],
            "humidade_pct": r["Humidade"],
            "chuva_mm":    r["Chuva_mm"],
            "descricao":   r["Descricao"],
            "vento_kmh":   r["VentoKmh"],
            "nascer_sol":  r["Nascer"],
            "por_sol":     r["Por"],
            "extracted_at": r["extracted_at"],
        } for r in all_atual]
        res = insert("clima_brasil_atual", rows_atual)
        print(f"    Supabase clima_brasil_atual: {res['inserted']} registros")
        # forecast — tem UNIQUE (cidade, data_previsao, extracted_at)
        rows_fc = [{
            "cidade":          r["Cidade"],
            "woeid":           r["WOEID"],
            "data_previsao":   r["Data"] if r["Data"] else None,
            "dia_semana":      r["DiaSemana"],
            "temp_max":        r["TempMax"],
            "temp_min":        r["TempMin"],
            "humidade_pct":    r["Humidade"],
            "chuva_mm":        r["Chuva_mm"],
            "prob_chuva_pct":  r["ProbChuva_pct"],
            "nebulosidade_pct": r["Nebulosidade_pct"],
            "descricao":       r["Descricao"],
            "condicao":        r["Condicao"],
            "extracted_at":    r["extracted_at"],
        } for r in all_forecast]
        res2 = upsert("clima_brasil_forecast", rows_fc, on_conflict="cidade,data_previsao,extracted_at")
        print(f"    Supabase clima_brasil_forecast: {res2['inserted']} registros")
        if res["errors"] or res2["errors"]:
            print(f"    Erros: {res['errors'] + res2['errors']}")
    except Exception as e:
        print(f"    Supabase skipped: {e}")

    # Salvar JSON
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(all_raw, f, ensure_ascii=False, indent=2)
    print(f"    Salvo: {OUT_JSON}")

    # Preview atual
    print("\nCondições atuais:")
    for r in all_atual:
        print(f"  {r['Cidade']:25s} | {r['Data']} | {r['Temp']}°C | chuva:{r['Chuva_mm']}mm | {r['Descricao']}")

    print("\n" + "=" * 60)
    print("CONCLUÍDO")


if __name__ == "__main__":
    main()
