"""
ETL SIPSA Colômbia — Preços mayoristas de limão
===============================================
Lê o ANEXO SEMANAL (xlsx) do boletim de precios mayoristas do DANE/SIPSA e
faz upsert em colombia_precos.

Por que não o web service SOAP (15/11/2026): desde ~26/10/2026 o host
appweb.dane.gov.co derruba o handshake TLS de qualquer rede (GitHub Actions,
sandbox, rede do escritório) e devolve 503 em HTTP. www.dane.gov.co segue de
pé, e o anexo semanal é a MESMA operação SIPSA, na mesma unidade (COP/kg).

Fonte:
    Página  https://www.dane.gov.co/index.php/estadisticas-por-tema/agropecuario/
            sistema-de-informacion-de-precios-sipsa/mayoristas-boletin-semanal-1
    Arquivo /files/operaciones/SIPSA/anex-SIPSASemanal-<periodo>.xlsx
            O nome NÃO segue padrão estável (7nov13nov, 31oct06nov,
            22ago28agos...): o link é lido da página, nunca montado.
    Conteúdo aba "1.2 Frutas frescas": Producto | Mercado mayorista |
            mín | máx | medio (COP/kg) | tendencia — semana sábado→sexta.

Mudança de granularidade: o SOAP dava preço DIÁRIO por cidade; o anexo dá a
média SEMANAL por mercado. Cada semana vira UMA linha por cidade, datada na
sexta que fecha a semana (6 dos 7 dias caem na semana Power Query dessa
sexta). Cidade com dois mercados (Cali: Cavasa e Santa Elena) = média
simples deles. Só entram as cidades que já existiam na série do SOAP, para
a média "todos os mercados" não mudar de composição.

Câmbio: TRM oficial (Superfinanciera) via Socrata, casada pela data da linha.

Uso:
    python etl_colombia_sipsa.py                     # semana publicada na página
    python etl_colombia_sipsa.py --url URL [--url URL ...]   # backfill de semanas
    python etl_colombia_sipsa.py --dry-run           # não grava, só relatório

Saída:
    colombia_precos.csv (sempre)
    upsert em colombia_precos (exceto em --dry-run)
"""

import argparse
import csv
import io
import re
import sys
import unicodedata
from datetime import date, datetime, timedelta
from urllib.parse import urljoin

import openpyxl
import requests

from supabase_upsert import upsert, SUPABASE_URL, SUPABASE_KEY

# ── CONFIG ─────────────────────────────────────────────────────────────────────────────
PAGINA_SEMANAL = (
    "https://www.dane.gov.co/index.php/estadisticas-por-tema/agropecuario/"
    "sistema-de-informacion-de-precios-sipsa/mayoristas-boletin-semanal-1"
)
HEADERS = {"User-Agent": "BI-Limao-TFruits/1.0 (ETL semanal; contato: TI Tradeconnex)"}

# Produtos de interesse. O Tahití é a nossa variedade; o Común entra como
# contexto de mercado e NÃO deve ser misturado com o Tahití em média.
PRODUTOS = {"Limón Tahití", "Limón Común"}
# O anexo grafa "Limón común" (minúsculo); o banco guarda "Limón Común".
# "Limón común valluno" e "Limón mandarino" são outras variedades: fora.
PRODUTO_CANONICO = {p.lower(): p for p in PRODUTOS}

# Cidades da série histórica (SOAP). Chave = cidade normalizada sem acento,
# extraída do nome do mercado; valor = grafia gravada no banco.
CIDADES = {
    "ARMENIA": "ARMENIA",
    "BARRANQUILLA": "BARRANQUILLA",
    "BOGOTA": "BOGOTÁ, D.C.",
    "BUCARAMANGA": "BUCARAMANGA",
    "CALI": "CALI",
    "CARTAGENA": "CARTAGENA DE INDIAS",
    "CUCUTA": "SAN JOSÉ DE CÚCUTA",
    "IBAGUE": "IBAGUÉ",
    "MANIZALES": "MANIZALES",
    "MEDELLIN": "MEDELLÍN",
    "MONTERIA": "MONTERÍA",
    "NEIVA": "NEIVA",
    "PASTO": "PASTO",
    "PEREIRA": "PEREIRA",
    "SINCELEJO": "SINCELEJO",
    "TUNJA": "TUNJA",
    "VALLEDUPAR": "VALLEDUPAR",
    "VILLAVICENCIO": "VILLAVICENCIO",
}

MESES = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5,
         "junio": 6, "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9,
         "octubre": 10, "noviembre": 11, "diciembre": 12}

TRM_URL = "https://www.datos.gov.co/resource/32sa-8pi3.json"
TRM_DESDE = "2019-12-01"

# Guardas de sanidade: gravar meia semana é pior do que não gravar.
MIN_CIDADES_TAHITI = 8      # a série tem ~15 cidades com Tahití
MAX_IDADE_SEMANA = 21       # dias: anexo mais velho que isso = página parada

TABELA = "colombia_precos"
CHAVE = "fecha,mercado,producto"
OUTPUT_CSV = "colombia_precos.csv"


# ── HELPERS ────────────────────────────────────────────────────────────────────────────
def week_of_year_pq(d: date) -> int:
    """
    Réplica de Date.WeekOfYear do Power Query (default): semana inicia no
    DOMINGO e a semana 1 é a que contém 1º de janeiro. Mesma função usada
    no Brasil e no Chile — não trocar por ISO, senão as abas desalinham.
    """
    jan1 = date(d.year, 1, 1)
    dias_desde_domingo = (jan1.weekday() + 1) % 7  # Mon=0 ... Sun=6
    inicio_semana1 = jan1 - timedelta(days=dias_desde_domingo)
    return (d - inicio_semana1).days // 7 + 1


def _sem_acento(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def cidade_do_mercado(mercado: str) -> str | None:
    """'Bogotá, D.C., Corabastos' → 'BOGOTÁ, D.C.'; 'Cartago (Valle)' → None."""
    base = re.split(r"[,(]", mercado or "", maxsplit=1)[0]
    chave = _sem_acento(" ".join(base.split())).upper()
    return CIDADES.get(chave)


def fim_da_semana(titulo: str) -> date:
    """
    'Boletín semanal precios mayoristas - 31 de octubre al 6 de noviembre
    de 2026' → date(2026, 11, 6). A data final é a última 'D de MES de AAAA'
    do título (o ano sempre acompanha a data final, inclusive na virada).
    """
    t = _sem_acento(titulo or "").lower()
    achados = re.findall(r"(\d{1,2})\s+de\s+([a-z]+)\s+de\s+(\d{4})", t)
    if not achados:
        raise RuntimeError(f"título sem data reconhecível: {titulo!r}")
    d, mes, ano = achados[-1]
    if mes not in MESES:
        raise RuntimeError(f"mês desconhecido no título: {titulo!r}")
    return date(int(ano), MESES[mes], int(d))


# ── [1] EXTRAÇÃO ─────────────────────────────────────────────────────────────────────────
def links_da_pagina() -> list[str]:
    r = requests.get(PAGINA_SEMANAL, headers=HEADERS, timeout=60)
    r.raise_for_status()
    hrefs = re.findall(r'href="([^"]*anex-SIPSASemanal[^"]*\.xlsx)"', r.text, re.I)
    urls = list(dict.fromkeys(urljoin(PAGINA_SEMANAL, h) for h in hrefs))
    if not urls:
        raise RuntimeError("nenhum anex-SIPSASemanal-*.xlsx na página do DANE "
                           "(layout mudou?)")
    return urls


def baixar(url: str, tentativas: int = 3) -> bytes:
    ultimo = None
    for i in range(1, tentativas + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=(30, 120))
            r.raise_for_status()
            if not r.content.startswith(b"PK"):
                raise RuntimeError(f"resposta não é xlsx "
                                   f"({r.headers.get('content-type')})")
            return r.content
        except Exception as e:  # noqa: BLE001
            ultimo = e
            print(f"   aviso: tentativa {i}/{tentativas} falhou — {e}")
            if i < tentativas:
                import time
                time.sleep(20 * i)
    raise RuntimeError(f"download do anexo falhou: {ultimo}")


def parsear(xlsx: bytes) -> tuple[date, list[dict], int]:
    """
    Devolve (sexta_da_semana, linhas_por_cidade, n_mercados_lidos).
    Procura em TODAS as abas (a numeração 1.2 já mudou no passado).
    """
    wb = openpyxl.load_workbook(io.BytesIO(xlsx), read_only=True, data_only=True)
    titulo, brutos = None, []
    for ws in wb.worksheets:
        aba_titulo = None
        for row in ws.iter_rows(values_only=True):
            c0 = row[0] if row else None
            if isinstance(c0, str) and "boletín semanal" in c0.lower():
                aba_titulo = c0
            produto = PRODUTO_CANONICO.get(c0.strip().lower()) if isinstance(c0, str) else None
            if produto and len(row) >= 5 and isinstance(row[4], (int, float)):
                brutos.append((produto, str(row[1] or ""), float(row[4])))
                titulo = titulo or aba_titulo
    if titulo is None:
        raise RuntimeError("anexo sem linhas de limão ou sem título de semana")

    sexta = fim_da_semana(titulo)
    acc: dict[tuple[str, str], list[float]] = {}
    for produto, mercado, preco in brutos:
        cidade = cidade_do_mercado(mercado)
        if cidade and preco > 0:
            acc.setdefault((produto, cidade), []).append(preco)

    linhas = [{
        "fecha": sexta,
        "semana": week_of_year_pq(sexta),
        "ano": sexta.year,
        "producto": produto,
        "mercado": cidade,
        "precio": round(sum(v) / len(v), 2),
    } for (produto, cidade), v in sorted(acc.items())]
    return sexta, linhas, len(brutos)


# ── [2] CÂMBIO (TRM) ───────────────────────────────────────────────────────────────────────
def carregar_trm() -> list[tuple[date, date, float]]:
    """
    Série da TRM como intervalos (desde, ate, valor). A Socrata devolve
    vigenciadesde/vigenciahasta, então feriado e fim de semana já vêm
    resolvidos pela própria fonte.
    """
    params = {
        "$select": "vigenciadesde,vigenciahasta,valor",
        "$where": f"vigenciadesde >= '{TRM_DESDE}T00:00:00.000'",
        "$order": "vigenciadesde ASC",
        "$limit": 50000,
    }
    r = requests.get(TRM_URL, params=params, timeout=60)
    r.raise_for_status()
    faixas = []
    for row in r.json():
        try:
            d1 = datetime.strptime(row["vigenciadesde"][:10], "%Y-%m-%d").date()
            d2 = datetime.strptime(row["vigenciahasta"][:10], "%Y-%m-%d").date()
            faixas.append((d1, d2, float(row["valor"])))
        except (KeyError, ValueError):
            continue
    if not faixas:
        raise RuntimeError("TRM voltou vazia")
    return faixas


def trm_do_supabase() -> list[tuple[date, date, float]]:
    """
    Fallback: o banco É a série histórica, porque cada registro guarda a TRM
    da sua data. Mesma estratégia do fallback de câmbio do Chile.
    """
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/{TABELA}",
        params={"select": "fecha,cambio", "cambio": "not.is.null",
                "order": "fecha.asc", "limit": 100000},
        headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
        timeout=60,
    )
    r.raise_for_status()
    vistos = {}
    for row in r.json():
        d = datetime.strptime(row["fecha"][:10], "%Y-%m-%d").date()
        vistos.setdefault(d, float(row["cambio"]))
    if not vistos:
        raise RuntimeError("nenhuma TRM gravada no banco para usar de fallback")
    return [(d, d, v) for d, v in sorted(vistos.items())]


def aplicar_cambio(linhas: list[dict], faixas: list[tuple[date, date, float]]) -> int:
    """
    Casa cada observação com a TRM vigente. Datas posteriores à última
    cotação conhecida recebem a última taxa com cambio_estimado=True;
    datas anteriores à primeira cotação ficam sem preço convertido e são
    descartadas, para não gravar preço em USD com taxa de outra época.
    """
    faixas = sorted(faixas)
    primeira, ultima = faixas[0][0], faixas[-1][1]
    ultima_taxa = faixas[-1][2]
    estimados = 0

    por_data = {}
    for d1, d2, v in faixas:
        dia = d1
        while dia <= d2:
            por_data[dia] = v
            dia += timedelta(days=1)

    mantidas = []
    for row in linhas:
        f = row["fecha"]
        if f < primeira:
            continue
        if f in por_data:
            row["cambio"] = round(por_data[f], 4)
            row["cambio_estimado"] = False
        elif f > ultima:
            row["cambio"] = round(ultima_taxa, 4)
            row["cambio_estimado"] = True
            estimados += 1
        else:
            # buraco no meio da série: usa a cotação válida mais recente
            anteriores = [d for d in por_data if d < f]
            if not anteriores:
                continue
            row["cambio"] = round(por_data[max(anteriores)], 4)
            row["cambio_estimado"] = False
        mantidas.append(row)

    linhas[:] = mantidas
    return estimados


# ── [3] DEDUP ────────────────────────────────────────────────────────────────────────────
def dedup(linhas: list[dict]) -> tuple[list[dict], int]:
    """
    Dedup pela chave de conflito. Nenhuma coluna da chave aceita NULL no
    schema: no Postgres NULL nunca conflita com NULL, e foi assim que
    chile_precos acumulou 48 mil duplicatas.
    """
    vistos = {}
    for row in linhas:
        vistos[(row["fecha"], row["mercado"], row["producto"])] = row
    unicas = list(vistos.values())
    return unicas, len(linhas) - len(unicas)


# ── MAIN ─────────────────────────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="ETL SIPSA Colômbia — preços de limão")
    ap.add_argument("--url", action="append", default=[],
                    help="anexo semanal explícito (repetível) — backfill")
    ap.add_argument("--dry-run", action="store_true",
                    help="não grava no Supabase, só gera o CSV e o relatório")
    args = ap.parse_args()

    urls = args.url or links_da_pagina()
    print(f"ETL SIPSA Colômbia — {len(urls)} anexo(s) semanal(is)")

    linhas = []
    for url in urls:
        print(f"[1] {url.rsplit('/', 1)[-1]}")
        sexta, sem, n = parsear(baixar(url))
        tahiti = {r["mercado"] for r in sem if r["producto"] == "Limón Tahití"}
        print(f"    semana até {sexta} (S{week_of_year_pq(sexta)}): {n} linhas de "
              f"limão, {len(tahiti)} cidades com Tahití")
        if len(tahiti) < MIN_CIDADES_TAHITI:
            print(f"ABORTADO: só {len(tahiti)} cidades com Tahití (mínimo "
                  f"{MIN_CIDADES_TAHITI}). Anexo parcial não é gravado.")
            return 2
        if not args.url and (date.today() - sexta).days > MAX_IDADE_SEMANA:
            print(f"ABORTADO: anexo publicado é de {sexta}, mais de "
                  f"{MAX_IDADE_SEMANA} dias atrás — página do DANE parada.")
            return 2
        linhas.extend(sem)

    print("[2] Carregando TRM (Superfinanciera via datos.gov.co)")
    try:
        faixas = carregar_trm()
        origem = "datos.gov.co"
    except Exception as e:  # noqa: BLE001
        print(f"    aviso: TRM indisponível ({e}); tentando a série já gravada")
        faixas = trm_do_supabase()
        origem = "fallback Supabase"
    estimados = aplicar_cambio(linhas, faixas)
    print(f"    {len(faixas)} faixas de TRM ({origem}), "
          f"{estimados} observações com taxa extrapolada")

    print("[3] Dedup")
    linhas, removidas = dedup(linhas)
    print(f"    {removidas} duplicatas removidas, {len(linhas)} registros finais")
    if not linhas:
        print("ABORTADO: nada para gravar.")
        return 2

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["fecha", "semana", "ano", "producto",
                                          "mercado", "precio", "cambio",
                                          "cambio_estimado"])
        w.writeheader()
        for row in sorted(linhas, key=lambda r: (r["fecha"], r["mercado"], r["producto"])):
            w.writerow({**row, "fecha": row["fecha"].isoformat()})
    print(f"[4] {OUTPUT_CSV} gravado")

    tahiti = [r for r in linhas if r["producto"] == "Limón Tahití"]
    if tahiti:
        ultimo = max(tahiti, key=lambda r: (r["fecha"], r["mercado"]))
        usd = ultimo["precio"] * 4.5 / ultimo["cambio"]
        print(f"    amostra: {ultimo['fecha']} {ultimo['mercado']} "
              f"{ultimo['precio']} COP/kg = US$ {usd:.2f}/cx 4,5kg "
              f"(TRM {ultimo['cambio']})")

    if args.dry_run:
        print("[5] --dry-run: nada enviado ao Supabase")
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
