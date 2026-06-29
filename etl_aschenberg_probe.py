"""
ETL Aschenberg — Probe & Discovery Script
==========================================
Faz login na API do Aschenberg, descobre os service names disponíveis
e testa extração de dados de shipment/containers.

Uso:
    python etl_aschenberg_probe.py

Dependências:
    pip install requests
"""

import requests
import json
import re

# ── CONFIG ────────────────────────────────────────────────────────────────────
API_URL   = "https://api.aschenberger.com.br/"
LOGIN_URL = "https://report.aschenberger.com.br/"
USERNAME  = "fesilva"
PASSWORD  = "e9NJwJ"

# ── SESSION COM COOKIES ───────────────────────────────────────────────────────
session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://report.aschenberger.com.br/",
    "Origin":  "https://report.aschenberger.com.br",
})

# ── STEP 1: DESCOBRIR SERVICE NAMES NO PRIORI.JS ──────────────────────────────
def discover_services():
    print("\n[1] Baixando priori.js para mapear service names...")
    r = session.get("https://report.aschenberger.com.br/js/priori.js", timeout=30)
    js = r.text

    # Buscar padrões: service:"nome.acao" ou service:'nome.acao'
    services = set(re.findall(r'["\']service["\']\s*:\s*["\']([^"\']+)["\']', js))

    # Buscar também padrões minificados: {service:"x.y"}
    services.update(re.findall(r'\bservice\b["\s:]+["\']([a-zA-Z]+\.[a-zA-Z]+)["\']', js))

    print(f"  Serviços encontrados: {len(services)}")
    for s in sorted(services):
        print(f"  - {s}")
    return sorted(services)


# ── STEP 2: LOGIN ──────────────────────────────────────────────────────────────
def login():
    print("\n[2] Fazendo login...")

    r = session.get(LOGIN_URL, timeout=30)
    print(f"  GET login page: {r.status_code}")

    r2 = session.post(API_URL, data=json.dumps([{
        "service": "login.client",
        "login": USERNAME,
        "password": PASSWORD
    }]), headers={"Content-Type": "application/json"}, timeout=30)

    print(f"  POST login.client: {r2.status_code}")
    try:
        resp = r2.json()
        # Mostrar resposta completa para mapear todos os campos
        print(f"  Resposta completa: {json.dumps(resp)}")
        print(f"  Cookies set: {dict(r2.cookies)}")
        print(f"  Headers resposta: {dict(r2.headers)}")

        # Token está no HEADER da resposta (Access-Control-Expose-Headers: token)
        token = r2.headers.get("token")
        if token:
            print(f"  ✅ Token no header: {token}")
            session.headers.update({"token": token})
        else:
            print("  ⚠️  Token não encontrado no header")

        return resp
    except Exception as e:
        print(f"  Resposta raw: {r2.text}")
        return None


# ── STEP 3: CHAMAR API ─────────────────────────────────────────────────────────
def call_api(services_list):
    """Testa os serviços reais descobertos no priori.js."""
    print("\n[3] Testando serviços de dados...")

    # Serviços reais mapeados no priori.js
    # cargo.getCargo e cargo.getGenericCargo são os principais de dados
    # entity/location/product são auxiliares (listas de filtros)
    candidate_services = [
        {"service": "message.getMessages"},
        {"service": "product.getProductList"},
        {"service": "location.getZoneFromList", "product": 1},
        {"service": "location.getZoneList",     "product": 1},
        {"service": "season.getSeasonList",     "product": 1},
        {"service": "transport.getTransportList","product": 1},
        {"service": "cargo.getCargo",
         "product": 1, "zoneFrom": 4, "zoneTo": 23,
         "transportType": 1, "year": 2026, "compareYear": 2025},
        {"service": "cargo.getGenericCargo",
         "product": 1, "zoneFrom": 4, "zoneTo": 23,
         "transportType": 1, "year": 2026},
        {"service": "compare.getCompareCargo",
         "product": 1, "zoneFrom": 4, "zoneTo": 23,
         "transportType": 1, "year": 2026},
        {"service": "entity.getAll", "product": 1},
    ]

    r = session.post(
        API_URL,
        data=json.dumps(candidate_services),
        headers={"Content-Type": "application/json"},
        timeout=30
    )

    print(f"  Status: {r.status_code}")
    try:
        resp = r.json()
        print(f"  Estrutura da resposta (batch de {len(resp) if isinstance(resp, list) else 1} itens):")
        if isinstance(resp, list):
            for i, item in enumerate(resp):
                if isinstance(item, dict):
                    keys = list(item.keys())
                    # Mostrar tamanho dos dados se houver lista
                    data_info = ""
                    for k in keys:
                        if isinstance(item[k], list):
                            data_info += f" | {k}[{len(item[k])}]"
                            # Mostrar keys do primeiro item da lista
                            if item[k]:
                                data_info += f" keys={list(item[k][0].keys()) if isinstance(item[k][0], dict) else ''}"
                    print(f"    [{i}] service resposta | keys: {keys}{data_info}")
                    # Sample do primeiro item de data se existir
                    for k in ['data', 'cargo', 'list', 'items']:
                        if k in item and isinstance(item[k], list) and item[k]:
                            print(f"         primeiro {k}: {json.dumps(item[k][0])[:250]}")
                            break
                else:
                    print(f"    [{i}] {type(item).__name__}: {str(item)[:100]}")
        else:
            print(f"  {json.dumps(resp)[:500]}")
        return resp
    except Exception as e:
        print(f"  Erro parsing JSON: {e}")
        print(f"  Raw: {r.text[:500]}")
        return None


# ── STEP 4: EXTRAIR DADOS DE SHIPMENT ─────────────────────────────────────────
def extract_shipment_data(service_name, year=2026, product=3):
    """Extrai dados reais de shipment para um ano."""
    print(f"\n[4] Extraindo dados: {service_name} / year={year} product={product}")

    # product=3 = Limes (Tahiti) conforme lastProductId na resposta de login
    # zoneFrom=4 = Brasil-All, zoneTo=23 = Europe-All (conforme preferences)
    payload = [{
        "service": service_name,
        "product": product,
        "zoneFrom": 4,
        "zoneTo": 23,
        "transportType": 1,
        "dateType": 1,
        "year": year,
        "compareYear": year - 1
    }]

    r = session.post(API_URL, data=json.dumps(payload),
                     headers={"Content-Type": "application/json"}, timeout=30)

    print(f"  Status: {r.status_code}")
    try:
        resp = r.json()
        print(f"  Registros: {len(resp) if isinstance(resp, list) else 'n/a'}")
        print(f"  Sample: {json.dumps(resp)[:600]}")
        return resp
    except Exception as e:
        print(f"  Erro: {e} | Raw: {r.text[:300]}")
        return None


# ── MAIN ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 60)
    print("ASCHENBERG API PROBE")
    print("=" * 60)

    # 1. Descobrir services
    services = discover_services()

    # 2. Login
    login_resp = login()

    # 3. Testar chamadas
    api_resp = call_api(services)

    # Extrair parâmetros das permissões do login
    login_data = login_resp[0]["data"] if login_resp and isinstance(login_resp, list) else {}
    perms = login_data.get("permissions", {})
    allowed_report = perms.get("allowedReports", [{}])[0] if perms.get("allowedReports") else {}
    report_data = json.loads(allowed_report.get("data", "{}"))
    from_zones = report_data.get("fromZoneList", [4])
    to_zones   = report_data.get("toZoneList", [23])
    report_id  = allowed_report.get("id", 7)
    print(f"\n  Permissões: report_id={report_id} | fromZones={from_zones} | toZones={to_zones}")

    # Adicionar header version (listado em Access-Control-Allow-Headers)
    session.headers.update({"version": "1"})

    # 4. Testar cargo com parâmetros exatos das permissões
    print("\n[4] Testando cargo com parâmetros das permissões...")
    # Parâmetros reais descobertos no priori.js:
    # dateStart/dateEnd (MySQL date), productId, varietyId, dateType,
    # transportType, fromZone, toZone, mode (searchMode)
    # O priori.js envia múltiplos objetos no array — um por ano (yearList)
    # e usa helper_DateIso.byIso(year, week) para calcular dateStart/dateEnd
    # Semana ISO: ano começa na semana 1 (primeira semana com quinta-feira)
    # Testar passando yearList diretamente e também datas ISO corretas

    from datetime import date, timedelta

    def iso_week_to_date(year, week, last_day=False):
        """Converte semana ISO para data MySQL. last_day=True retorna domingo da semana."""
        d = date.fromisocalendar(year, week, 7 if last_day else 1)
        return d.strftime("%Y-%m-%d")

    cargo_variants = [
        # Testar com yearList (como a página faz internamente)
        {"service": "cargo.getCargo",
         "yearList": [2025], "productId": 3, "varietyId": 0,
         "dateType": 1, "transportType": 1,
         "fromZone": from_zones[0], "toZone": to_zones[2], "mode": 0},
        # Datas ISO week corretas para 2025 (semana 1 a 52)
        {"service": "cargo.getCargo",
         "dateStart": iso_week_to_date(2025, 1, False),
         "dateEnd":   iso_week_to_date(2025, 52, True),
         "productId": 3, "varietyId": 0,
         "dateType": 1, "transportType": 1,
         "fromZone": from_zones[0], "toZone": to_zones[2], "mode": 0},
        # Datas ISO week 2026 (semana 1 até semana atual ~26)
        {"service": "cargo.getCargo",
         "dateStart": iso_week_to_date(2026, 1, False),
         "dateEnd":   iso_week_to_date(2026, 26, True),
         "productId": 3, "varietyId": 0,
         "dateType": 1, "transportType": 1,
         "fromZone": from_zones[0], "toZone": to_zones[2], "mode": 0},
        # Sem dateType (campo opcional?)
        {"service": "cargo.getCargo",
         "dateStart": iso_week_to_date(2025, 1, False),
         "dateEnd":   iso_week_to_date(2025, 52, True),
         "productId": 3, "varietyId": 0,
         "transportType": 1,
         "fromZone": from_zones[0], "toZone": to_zones[2], "mode": 0},
        # Múltiplos anos no mesmo request (como a página faz para o gráfico comparativo)
        {"service": "cargo.getCargo",
         "dateStart": iso_week_to_date(2025, 1, False),
         "dateEnd":   iso_week_to_date(2025, 52, True),
         "productId": 3, "varietyId": 0,
         "dateType": 1, "transportType": 1,
         "fromZone": from_zones[0], "toZone": to_zones[2], "mode": 0,
         "compareYear": 2024},
    ]
    print(f"  dateStart 2025-wk1: {iso_week_to_date(2025,1,False)} | dateEnd 2025-wk52: {iso_week_to_date(2025,52,True)}")

    for svc in cargo_variants:
        r = session.post(API_URL, data=json.dumps([svc]),
                         headers={"Content-Type": "application/json"}, timeout=30)
        try:
            resp = r.json()
            if isinstance(resp, list) and resp and not resp[0].get("errorMessage"):
                d = resp[0]
                info = ""
                for k, v in d.items():
                    if isinstance(v, list):
                        info += f" {k}[{len(v)}]"
                        if v and isinstance(v[0], dict):
                            info += f"(keys:{list(v[0].keys())})"
                        if v:
                            print(f"    SAMPLE {k}[0]: {json.dumps(v[0])[:300]}")
                print(f"  ✅ {svc['service']} variante: {info or json.dumps(d)[:300]}")
            else:
                err = resp[0].get("errorMessage") if isinstance(resp, list) else resp
                print(f"  ❌ {svc['service']}: {err}")
        except Exception as e:
            print(f"  ❌ {svc['service']}: {e} | {r.text[:100]}")

    print("\n" + "=" * 60)
    print("FIM DO PROBE")
    print("Cookies da sessão:", dict(session.cookies))
