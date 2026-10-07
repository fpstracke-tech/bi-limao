"""
ETL EMMSA Peru — Preços e volume de limão no Gran Mercado Mayorista de Lima
===========================================================================
Consome o sistema legado da EMMSA (empresa municipal que opera o GMML) e faz
upsert em peru_precos.

Fonte:
    POST https://old.emmsa.com.pe/emmsa_spv/app/reportes/ajax/rpt07_gettable_new_web.php
    vid_tipo=1 → preço (S/ por kg)   vid_tipo=2 → volume ingressado (TM)
    vprod=30   → LIMON               vfecha=dd/mm/aaaa (uma data por requisição)
    Página pública: https://www.emmsa.com.pe/index.php/precios-diarios/

RESSALVA DE LEITURA, a mais importante deste ETL: a EMMSA cota **limón sutil**
(limão ácido pequeno, tipo key lime), que é OUTRA FRUTA, de consumo interno
peruano. A série serve para ler oferta e sazonalidade do Peru, nunca para
comparar preço com o Tahiti do Brasil, do México ou da Colômbia. A aba tem
nota fixa dizendo isso — não remover.

Três particularidades da fonte:
  1. **Uma requisição por data.** Não existe endpoint de intervalo funcional
     (rpt06_getgraph retorna vazio fora da sessão da página). Por isso o
     backfill é paralelizado em poucos workers e aceita --desde/--ate.
  2. **A cadeia TLS do servidor é incompleta.** old.emmsa.com.pe envia só o
     certificado folha, sem o intermediário, e qualquer cliente moderno
     recusa com "unable to verify the first certificate". A correção aqui é
     baixar o intermediário apontado pelo AIA do próprio certificado e montar
     um bundle — a verificação continua LIGADA, nunca use verify=False.
     Se um dia o intermediário mudar, redescubra a URL com:
         openssl s_client -connect old.emmsa.com.pe:443 \\
             -servername old.emmsa.com.pe </dev/null \\
             | openssl x509 -noout -ext authorityInfoAccess
  3. **O servidor só responde ao libcurl.** Com `requests`/urllib3 a conexão é
     derrubada (ConnectionReset ou CERTIFICATE_VERIFY_FAILED) mesmo com o
     bundle correto, e com curl_cffi em modo `impersonate` (BoringSSL) ela cai
     em SSL_ERROR_SYSCALL. O que funciona é curl_cffi **sem impersonate**, que
     é libcurl puro — testado nas duas pontas. Não troque por requests.
  4. É sistema legado (`old.`). O risco de descontinuidade é maior que o das
     outras fontes; o step no workflow é isolado para não derrubar as demais.

Câmbio: USD→PEN do BCRP (série PD04640PD), taxa da data gravada por registro.

Uso:
    python etl_peru_emmsa.py                         # incremental (10 dias)
    python etl_peru_emmsa.py --backfill              # desde 2023-01-01
    python etl_peru_emmsa.py --backfill --desde 2023-01-01 --ate 2023-12-31
    python etl_peru_emmsa.py --dry-run

Saída:
    peru_precos.csv (sempre)
    upsert em peru_precos (exceto em --dry-run)
"""

import argparse
import csv
import html
import os
import re
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

import certifi
import requests
from curl_cffi import requests as cr

from supabase_upsert import upsert

# ── CONFIG ─────────────────────────────────────────────────────────────────────
URL = ("https://old.emmsa.com.pe/emmsa_spv/app/reportes/ajax/"
       "rpt07_gettable_new_web.php")
PROD_ID = 30            # LIMON
PRODUTO = "LIMON"
MERCADO = "GMML - Lima"

# Intermediário Let's Encrypt que o servidor deixa de enviar (ver nota 2 acima).
INTERMEDIARIO_AIA = "http://ye2.i.lencr.org/"

HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded",
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"),
    "Accept": "text/html, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
}

BCRP_URL = ("https://estadisticas.bcrp.gob.pe/estadisticas/series/api/"
            "PD04640PD/json/{ini}/{fim}")

BACKFILL_DESDE = "2023-01-01"
TABELA = "peru_precos"
CHAVE = "fecha,variedad,mercado"
OUTPUT_CSV = "peru_precos.csv"


# ── HELPERS ────────────────────────────────────────────────────────────────────
def week_of_year_pq(d: date) -> int:
    """Date.WeekOfYear do Power Query: semana inicia no DOMINGO, semana 1 contém 1º/jan."""
    jan1 = date(d.year, 1, 1)
    dias_desde_domingo = (jan1.weekday() + 1) % 7
    return (d - (jan1 - timedelta(days=dias_desde_domingo))).days // 7 + 1


def num(txt):
    try:
        v = float((txt or "").replace(",", "").strip())
    except ValueError:
        return None
    return v if v > 0 else None


def celulas(linha_html: str) -> list[str]:
    return [" ".join(html.unescape(re.sub(r"<[^>]+>", "", c)).split())
            for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", linha_html, re.S | re.I)]


# ── [0] TLS: completar a cadeia que o servidor não envia ───────────────────────
def montar_bundle() -> str | None:
    """
    Devolve o caminho de um bundle = certifi + intermediário, ou None se a
    conexão já valida sozinha (caso a EMMSA conserte o servidor um dia).
    """
    try:
        cr.get("https://old.emmsa.com.pe/emmsa_spv/", timeout=20,
               headers={"User-Agent": HEADERS["User-Agent"]})
        print("    cadeia TLS do servidor já está completa, bundle dispensado")
        return None
    except Exception:  # noqa: BLE001
        pass  # cadeia incompleta (o esperado) ou rede instável: segue e monta

    r = requests.get(INTERMEDIARIO_AIA, timeout=30)
    r.raise_for_status()
    der = r.content
    import base64
    b64 = base64.b64encode(der).decode()
    pem = ("-----BEGIN CERTIFICATE-----\n"
           + "\n".join(b64[i:i + 64] for i in range(0, len(b64), 64))
           + "\n-----END CERTIFICATE-----\n")

    # Parte da âncora de confiança EFETIVA do ambiente, não do certifi cru:
    # em runner/sandbox com proxy corporativo o REQUESTS_CA_BUNDLE aponta para
    # outro arquivo, e ignorar isso quebra a verificação de todo o resto.
    base_ca = (os.environ.get("REQUESTS_CA_BUNDLE")
               or os.environ.get("CURL_CA_BUNDLE")
               or certifi.where())
    fd, caminho = tempfile.mkstemp(suffix=".pem", prefix="emmsa_ca_")
    with os.fdopen(fd, "w", encoding="ascii") as f:
        f.write(open(base_ca, encoding="ascii").read())
        f.write("\n" + pem)
    print(f"    bundle TLS montado com o intermediário de {INTERMEDIARIO_AIA}")
    return caminho


def nova_sessao(bundle: str | None):
    # curl_cffi SEM impersonate = libcurl puro. É a única combinação que o
    # servidor da EMMSA aceita (ver nota 3 no topo do arquivo).
    s = cr.Session(verify=bundle) if bundle else cr.Session()
    s.headers.update(HEADERS)
    return s


# ── [1] EXTRAÇÃO ───────────────────────────────────────────────────────────────
def consultar(sessao, dia: date, tipo: int,
              tentativas: int = 3) -> list[list[str]]:
    dados = {"vid_tipo": tipo, "vprod": PROD_ID, "vvari": "",
             "vfecha": dia.strftime("%d/%m/%Y")}
    ultimo = None
    for i in range(1, tentativas + 1):
        try:
            r = sessao.post(URL, data=dados, timeout=45)
            r.raise_for_status()
            r.encoding = "utf-8"
            linhas = []
            for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", r.text, re.S | re.I):
                cel = celulas(tr)
                if cel and cel[0].upper() == PRODUTO:
                    linhas.append(cel)
            return linhas
        except Exception as e:  # noqa: BLE001
            ultimo = e
            if i < tentativas:
                import time
                time.sleep(3 * i)
    raise RuntimeError(f"EMMSA falhou em {dia:%d/%m/%Y} (tipo {tipo}): {ultimo}")


def coletar_dia(sessao, dia: date) -> list[dict]:
    """Junta preço (vid_tipo=1) e volume (vid_tipo=2) da mesma data e variedade."""
    por_variedad: dict[str, dict] = {}

    for cel in consultar(sessao, dia, 1):
        if len(cel) < 5:
            continue
        variedad = cel[1]
        preco = num(cel[4]) or num(cel[2]) or num(cel[3])
        if preco is None:
            continue
        por_variedad[variedad] = {
            "fecha": dia,
            "semana": week_of_year_pq(dia),
            "ano": dia.year,
            "producto": PRODUTO,
            "variedad": variedad,
            "mercado": MERCADO,
            "precio_min": num(cel[2]),
            "precio_max": num(cel[3]),
            "precio": preco,
            "volumen_t": None,
        }

    if por_variedad:  # só busca volume se houve pregão
        for cel in consultar(sessao, dia, 2):
            if len(cel) < 3:
                continue
            reg = por_variedad.get(cel[1])
            if reg is not None:
                reg["volumen_t"] = num(cel[2])

    return list(por_variedad.values())


def coletar(dias: list[date], bundle: str | None, workers: int) -> list[dict]:
    """
    Paraleliza em poucos workers: é um sistema legado e são 2 requisições por
    data, então nada de abrir dezenas de conexões. Cada thread tem a sua
    sessão (requests.Session não é thread-safe).
    """
    locais = {}

    def tarefa(dia: date):
        tid = id(__import__("threading").current_thread())
        if tid not in locais:
            locais[tid] = nova_sessao(bundle)
        return coletar_dia(locais[tid], dia)

    linhas = []
    vazias = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, (dia, parcial) in enumerate(zip(dias, ex.map(tarefa, dias)), 1):
            linhas.extend(parcial)
            if not parcial:
                vazias.append(dia)
            if i % 100 == 0 or i == len(dias):
                print(f"    {i}/{len(dias)} datas — {len(linhas)} linhas")

    # Repasse serial nas datas que voltaram vazias. Dia sem pregão existe e é
    # legítimo, mas o servidor TAMBÉM devolve página vazia quando acha que está
    # sendo martelado — e aí a data entraria como 'sem pregão' para sempre.
    # Conferir uma a uma, devagar, separa um caso do outro.
    if vazias:
        print(f"    repassando {len(vazias)} datas vazias, uma a uma")
        sessao = nova_sessao(bundle)
        recuperadas = 0
        for j, dia in enumerate(vazias, 1):
            parcial = coletar_dia(sessao, dia)
            if parcial:
                linhas.extend(parcial)
                recuperadas += 1
            time.sleep(0.4)
            if j % 100 == 0:
                print(f"      {j}/{len(vazias)}")
        if recuperadas:
            print(f"    ATENÇÃO: {recuperadas} datas tinham dado e voltaram "
                  f"vazias na coleta paralela. Reduza --workers.")
        else:
            print("    nenhuma recuperada: as vazias são dias sem pregão mesmo")
    return linhas


# ── [2] CÂMBIO ─────────────────────────────────────────────────────────────────
MESES_BCRP = {"Ene": 1, "Feb": 2, "Mar": 3, "Abr": 4, "May": 5, "Jun": 6,
              "Jul": 7, "Ago": 8, "Set": 9, "Sep": 9, "Oct": 10, "Nov": 11, "Dic": 12}


def carregar_cambio(ini: date, fim: date) -> dict[date, float]:
    url = BCRP_URL.format(ini=(ini - timedelta(days=10)).isoformat(),
                          fim=fim.isoformat())
    r = requests.get(url, timeout=60, headers={"User-Agent": HEADERS["User-Agent"]})
    r.raise_for_status()
    fx = {}
    for per in r.json().get("periods", []):
        nome = per.get("name", "")           # ex.: "02.Oct.26"
        valores = per.get("values", [])
        if not valores or valores[0] in ("n.d.", ""):
            continue
        m = re.match(r"(\d{2})\.(\w{3})\.(\d{2})", nome)
        if not m:
            continue
        dia, mes_txt, ano = m.groups()
        mes = MESES_BCRP.get(mes_txt.capitalize())
        if not mes:
            continue
        try:
            fx[date(2000 + int(ano), mes, int(dia))] = float(valores[0])
        except ValueError:
            continue
    if not fx:
        raise RuntimeError("BCRP devolveu série de câmbio vazia")
    return fx


def aplicar_cambio(linhas: list[dict], fx: dict[date, float]) -> int:
    datas_fx = sorted(fx)
    primeira, ultima = datas_fx[0], datas_fx[-1]
    estimados = 0
    mantidas = []
    for row in linhas:
        f = row["fecha"]
        if f < primeira:
            continue
        if f in fx:
            row["cambio"] = round(fx[f], 4)
            row["cambio_estimado"] = False
        elif f > ultima:
            row["cambio"] = round(fx[ultima], 4)
            row["cambio_estimado"] = True
            estimados += 1
        else:
            anterior = max(d for d in datas_fx if d < f)
            row["cambio"] = round(fx[anterior], 4)
            row["cambio_estimado"] = False
        mantidas.append(row)
    linhas[:] = mantidas
    return estimados


# ── [3] DEDUP ──────────────────────────────────────────────────────────────────
def dedup(linhas: list[dict]) -> tuple[list[dict], int]:
    vistos = {}
    for row in linhas:
        vistos[(row["fecha"], row["variedad"], row["mercado"])] = row
    unicas = list(vistos.values())
    return unicas, len(linhas) - len(unicas)


# ── MAIN ───────────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="ETL EMMSA Peru — preço e volume de limão")
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--desde", default=BACKFILL_DESDE, help="AAAA-MM-DD")
    ap.add_argument("--ate", default=None, help="AAAA-MM-DD (padrão: hoje)")
    ap.add_argument("--dias", type=int, default=10, help="janela do incremental")
    ap.add_argument("--workers", type=int, default=2,
                    help="requisições simultâneas (padrão 2). NÃO aumentar: com "
                         "6 workers o servidor passa a devolver página vazia em "
                         "vez de erro, e a data entra como 'sem pregão' — perda "
                         "silenciosa de dado, medida em 07/10/2026.")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    hoje = date.today()
    if args.backfill:
        ini = datetime.strptime(args.desde, "%Y-%m-%d").date()
        fim = datetime.strptime(args.ate, "%Y-%m-%d").date() if args.ate else hoje
    else:
        ini, fim = hoje - timedelta(days=args.dias), hoje
    dias = [ini + timedelta(days=i) for i in range((fim - ini).days + 1)]
    print(f"ETL EMMSA Peru — {ini} a {fim} ({len(dias)} datas, "
          f"{args.workers} workers)")

    print("[1] Preparando conexão TLS")
    bundle = montar_bundle()

    print("[2] Coletando (2 requisições por data: preço e volume)")
    linhas = coletar(dias, bundle, args.workers)
    if not linhas:
        print("ABORTADO: nenhuma linha coletada na janela.")
        return 2

    print("[3] Carregando câmbio USD→PEN (BCRP)")
    datas = [r["fecha"] for r in linhas]
    fx = carregar_cambio(min(datas), max(datas))
    estimados = aplicar_cambio(linhas, fx)
    print(f"    {len(fx)} cotações, {estimados} observações com taxa extrapolada")

    print("[4] Dedup")
    linhas, removidas = dedup(linhas)
    print(f"    {removidas} duplicatas removidas, {len(linhas)} registros finais")
    datas = [r["fecha"] for r in linhas]
    variedades = sorted({r["variedad"] for r in linhas})
    com_vol = sum(1 for r in linhas if r["volumen_t"])
    print(f"    período {min(datas)} a {max(datas)} | variedades: {', '.join(variedades)}")
    print(f"    {com_vol} registros com volume")

    campos = ["fecha", "semana", "ano", "producto", "variedad", "mercado",
              "precio_min", "precio_max", "precio", "volumen_t",
              "cambio", "cambio_estimado"]
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=campos)
        w.writeheader()
        for row in sorted(linhas, key=lambda r: (r["fecha"], r["variedad"])):
            w.writerow({**row, "fecha": row["fecha"].isoformat()})
    print(f"[5] {OUTPUT_CSV} gravado")

    if args.dry_run:
        print("[6] --dry-run: nada enviado ao Supabase")
        ultimo = max(linhas, key=lambda r: r["fecha"])
        usd = ultimo["precio"] * 4.5 / ultimo["cambio"]
        print(f"    amostra: {ultimo['fecha']} {ultimo['variedad']} "
              f"S/ {ultimo['precio']}/kg = US$ {usd:.2f}/cx 4,5kg "
              f"(câmbio {ultimo['cambio']}), volume {ultimo['volumen_t']} TM")
        return 0

    print(f"[7] Upsert em {TABELA}")
    res = upsert(TABELA, linhas, batch_size=1000, on_conflict=CHAVE)
    print(f"    {res['inserted']} registros enviados")
    if res["errors"]:
        for err in res["errors"][:5]:
            print(f"    ERRO lote {err['batch_start']}: {err['status']} {err['detail']}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
