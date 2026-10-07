"""
ETL SNIIM México — Preços mayoristas de limão
=============================================
Raspa o SNIIM (Sistema Nacional de Información e Integración de Mercados,
Secretaría de Economía) e faz upsert em mexico_precos.

Fonte:
    https://www.economia-sniim.gob.mx/Nuevo/Consultas/MercadosNacionales/
    PreciosDeMercado/Agricolas/ResultadosConsultaFechaFrutasYHortalizas.aspx
    ProductoId=426  → "Limón s/semilla - Primera" (limón persa = nosso Tahiti)
    PreciosPorId=2  → preço por quilograma já calculado (MXN/kg)
    OrigenId=-1 / DestinoId=-1 → todas as origens e todos os destinos

Duas armadilhas confirmadas na validação:
  1. A página só responde em https. Em http o proxy devolve 403.
  2. RegistrosPorPagina=1000 TRUNCA SEM AVISO. Uma janela de ano inteiro volta
     com exatamente 1000 linhas e só 4 dos 11 destinos, sem erro nenhum. Por
     isso o backfill é mês a mês (~490 linhas/mês) e o ETL aborta se qualquer
     janela voltar com exatamente 1000 linhas.

Câmbio: USD→MXN diário do Frankfurter (BCE), taxa da data gravada em cada
registro. Fim de semana e feriado recebem a última cotação útil anterior
(fill-forward silencioso, igual ao Chile); só datas posteriores à última
cotação conhecida são marcadas com cambio_estimado=true.

Uso:
    pip install requests --break-system-packages
    python etl_mexico_sniim.py                    # incremental (10 dias)
    python etl_mexico_sniim.py --backfill         # mês a mês desde 01/2023
    python etl_mexico_sniim.py --backfill --desde 2024-01
    python etl_mexico_sniim.py --dry-run

Saída:
    mexico_precos.csv (sempre)
    upsert em mexico_precos (exceto em --dry-run)
"""

import argparse
import csv
import html
import re
import sys
import time
from datetime import date, datetime, timedelta

import requests

from supabase_upsert import upsert

# ── CONFIG ─────────────────────────────────────────────────────────────────────
BASE = ("https://www.economia-sniim.gob.mx/Nuevo/Consultas/MercadosNacionales/"
        "PreciosDeMercado/Agricolas/ResultadosConsultaFechaFrutasYHortalizas.aspx")
PRODUTO_ID = 426
PRODUTO_NOME = "Limón s/semilla"
REGISTROS_POR_PAGINA = 1000

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"),
    "Accept-Language": "es-MX,es;q=0.9",
}

FX_URL = "https://api.frankfurter.dev/v1/{ini}..{fim}?base=USD&symbols=MXN"

BACKFILL_DESDE = "2023-01"
TABELA = "mexico_precos"
CHAVE = "fecha,mercado,origen,presentacion"
OUTPUT_CSV = "mexico_precos.csv"


# ── HELPERS ────────────────────────────────────────────────────────────────────
def week_of_year_pq(d: date) -> int:
    """
    Réplica de Date.WeekOfYear do Power Query (default): semana inicia no
    DOMINGO e a semana 1 é a que contém 1º de janeiro. Mesma função do Brasil,
    do Chile e da Colômbia — não trocar por ISO, senão as abas desalinham.
    """
    jan1 = date(d.year, 1, 1)
    dias_desde_domingo = (jan1.weekday() + 1) % 7
    inicio_semana1 = jan1 - timedelta(days=dias_desde_domingo)
    return (d - inicio_semana1).days // 7 + 1


def limpar(celula: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", "", celula)).split())


def num(txt: str):
    txt = (txt or "").replace(",", "").strip()
    try:
        v = float(txt)
    except ValueError:
        return None
    return v if v > 0 else None


def meses(desde: date, ate: date):
    """Gera (primeiro_dia, ultimo_dia) de cada mês do intervalo."""
    cur = date(desde.year, desde.month, 1)
    while cur <= ate:
        prox = date(cur.year + (cur.month == 12), (cur.month % 12) + 1, 1)
        yield cur, min(prox - timedelta(days=1), ate)
        cur = prox


# ── [1] EXTRAÇÃO ───────────────────────────────────────────────────────────────
def baixar_janela(ini: date, fim: date, tentativas: int = 3) -> str:
    params = {
        "ProductoId": PRODUTO_ID,
        "OrigenId": -1, "Origen": "Todos",
        "DestinoId": -1, "Destino": "Todos",
        "PreciosPorId": 2,
        "RegistrosPorPagina": REGISTROS_POR_PAGINA,
        "fechaInicio": ini.strftime("%d/%m/%Y"),
        "fechaFinal": fim.strftime("%d/%m/%Y"),
    }
    ultimo = None
    for i in range(1, tentativas + 1):
        try:
            r = requests.get(BASE, params=params, headers=HEADERS, timeout=90)
            r.raise_for_status()
            r.encoding = "utf-8"
            return r.text
        except Exception as e:  # noqa: BLE001
            ultimo = e
            print(f"      aviso: tentativa {i}/{tentativas} falhou — {e}")
            if i < tentativas:
                time.sleep(20 * i)
    raise RuntimeError(f"SNIIM indisponível para {ini}..{fim}: {ultimo}")


def parsear(pagina: str, rotulo: str) -> list[dict]:
    """
    Lê a #tblResultados. Colunas: Fecha, Presentación, Origen, Destino,
    Precio Mín, Precio Max, Precio Frec, Obs.
    Descarta o cabeçalho e a linha de grupo ("Frutas").
    """
    m = re.search(r'<table[^>]*id="tblResultados".*?</table>', pagina, re.S | re.I)
    if not m:
        raise RuntimeError(f"tabela #tblResultados não encontrada em {rotulo} "
                           "— layout da página pode ter mudado")

    linhas_html = re.findall(r"<tr[^>]*>(.*?)</tr>", m.group(0), re.S | re.I)
    dados = []
    for tr in linhas_html:
        cel = [limpar(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S | re.I)]
        if len(cel) < 7:
            continue                      # cabeçalho de grupo ("Frutas")
        if cel[0].lower().startswith("fecha"):
            continue                      # cabeçalho da tabela
        try:
            fecha = datetime.strptime(cel[0], "%d/%m/%Y").date()
        except ValueError:
            continue
        precio = num(cel[6]) or num(cel[4]) or num(cel[5])
        if precio is None:
            continue
        dados.append({
            "fecha": fecha,
            "semana": week_of_year_pq(fecha),
            "ano": fecha.year,
            "producto": PRODUTO_NOME,
            "origen": cel[2],
            "mercado": cel[3],
            "presentacion": cel[1],
            "precio_min": num(cel[4]),
            "precio_max": num(cel[5]),
            "precio": precio,
        })

    # Truncamento silencioso: a página corta no limite sem dizer nada.
    if len(dados) >= REGISTROS_POR_PAGINA:
        raise RuntimeError(
            f"{rotulo} devolveu {len(dados)} linhas, no limite de "
            f"{REGISTROS_POR_PAGINA}. A janela foi truncada em silêncio — "
            "reduza o intervalo. Nada foi gravado.")
    return dados


# ── [2] CÂMBIO ─────────────────────────────────────────────────────────────────
def carregar_cambio(ini: date, fim: date) -> dict[date, float]:
    r = requests.get(FX_URL.format(ini=ini - timedelta(days=10), fim=fim),
                     timeout=60)
    r.raise_for_status()
    rates = r.json().get("rates", {})
    if not rates:
        raise RuntimeError("Frankfurter devolveu série vazia")
    return {datetime.strptime(d, "%Y-%m-%d").date(): float(v["MXN"])
            for d, v in rates.items()}


def aplicar_cambio(linhas: list[dict], fx: dict[date, float]) -> int:
    """
    Fim de semana e feriado herdam a última cotação útil (sem marcação, senão
    toda segunda-feira vira ressalva). Datas posteriores à última cotação
    conhecida são marcadas como estimadas.
    """
    if not fx:
        raise RuntimeError("sem série de câmbio")
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
    """
    Dedup pela chave de conflito completa. No México o mesmo mercado recebe
    limão de várias origens em apresentações diferentes no mesmo dia: chave
    curta demais colapsa linha real e desloca a média semanal (foi o que
    aconteceu na auditoria do Chile).
    """
    vistos = {}
    for row in linhas:
        vistos[(row["fecha"], row["mercado"], row["origen"], row["presentacion"])] = row
    unicas = list(vistos.values())
    return unicas, len(linhas) - len(unicas)


# ── MAIN ───────────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="ETL SNIIM México — preços de limão")
    ap.add_argument("--backfill", action="store_true", help="coleta mês a mês")
    ap.add_argument("--desde", default=BACKFILL_DESDE,
                    help=f"mês inicial do backfill, AAAA-MM (padrão {BACKFILL_DESDE})")
    ap.add_argument("--ate", default=None,
                    help="mês final do backfill, AAAA-MM (padrão: mês corrente). "
                         "Serve para quebrar o backfill em pedaços — o upsert é "
                         "idempotente, então rodar ano a ano é seguro.")
    ap.add_argument("--dias", type=int, default=10,
                    help="janela do modo incremental em dias (padrão 10)")
    ap.add_argument("--dry-run", action="store_true", help="não grava no Supabase")
    args = ap.parse_args()

    hoje = date.today()
    if args.backfill:
        ini = datetime.strptime(args.desde, "%Y-%m").date()
        if args.ate:
            fim_mes = datetime.strptime(args.ate, "%Y-%m").date()
            prox = date(fim_mes.year + (fim_mes.month == 12), (fim_mes.month % 12) + 1, 1)
            fim = min(prox - timedelta(days=1), hoje)
        else:
            fim = hoje
        janelas = list(meses(ini, fim))
        print(f"ETL SNIIM México — backfill de {args.desde} a {fim:%m/%Y} "
              f"({len(janelas)} meses)")
    else:
        janelas = [(hoje - timedelta(days=args.dias), hoje)]
        print(f"ETL SNIIM México — modo incremental ({args.dias} dias)")

    print("[1] Coletando janelas")
    linhas = []
    for i, (a, b) in enumerate(janelas, 1):
        rotulo = f"{a:%d/%m/%Y}..{b:%d/%m/%Y}"
        pagina = baixar_janela(a, b)
        parcial = parsear(pagina, rotulo)
        linhas.extend(parcial)
        print(f"    [{i}/{len(janelas)}] {rotulo}: {len(parcial)} linhas")
        if i < len(janelas):
            time.sleep(1.5)

    if not linhas:
        print("ABORTADO: nenhuma linha coletada.")
        return 2

    print("[2] Carregando câmbio USD→MXN (Frankfurter/BCE)")
    datas = [r["fecha"] for r in linhas]
    fx = carregar_cambio(min(datas), max(datas))
    estimados = aplicar_cambio(linhas, fx)
    print(f"    {len(fx)} cotações, {estimados} observações com taxa extrapolada")

    print("[3] Dedup")
    linhas, removidas = dedup(linhas)
    print(f"    {removidas} duplicatas removidas, {len(linhas)} registros finais")

    datas = [r["fecha"] for r in linhas]
    mercados = sorted({r["mercado"] for r in linhas})
    print(f"    período {min(datas)} a {max(datas)} | {len(mercados)} mercados")

    campos = ["fecha", "semana", "ano", "producto", "origen", "mercado",
              "presentacion", "precio_min", "precio_max", "precio",
              "cambio", "cambio_estimado"]
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=campos)
        w.writeheader()
        for row in sorted(linhas, key=lambda r: (r["fecha"], r["mercado"])):
            w.writerow({**row, "fecha": row["fecha"].isoformat()})
    print(f"[4] {OUTPUT_CSV} gravado")

    if args.dry_run:
        print("[5] --dry-run: nada enviado ao Supabase")
        ultimo = max(linhas, key=lambda r: r["fecha"])
        usd = ultimo["precio"] * 4.5 / ultimo["cambio"]
        print(f"    amostra: {ultimo['fecha']} {ultimo['mercado'][:45]} "
              f"{ultimo['precio']} MXN/kg = US$ {usd:.2f}/cx 4,5kg "
              f"(câmbio {ultimo['cambio']})")
        return 0

    print(f"[5] Upsert em {TABELA}")
    res = upsert(TABELA, linhas, batch_size=1000, on_conflict=CHAVE)
    print(f"    {res['inserted']} registros enviados")
    if res["errors"]:
        for err in res["errors"][:5]:
            print(f"    ERRO lote {err['batch_start']}: {err['status']} {err['detail']}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
