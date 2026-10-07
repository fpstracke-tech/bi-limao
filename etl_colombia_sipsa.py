"""
ETL SIPSA Colômbia — Preços mayoristas de limão
===============================================
Consome o web service SOAP do DANE (SIPSA) e faz upsert em colombia_precos.

Fonte:
    WSDL     https://appweb.dane.gov.co/sipsaWS/SrvSipsaUpraBeanService?WSDL
    Endpoint https://appweb.dane.gov.co/sipsaWS/SrvSipsaUpraBeanService
    Operação promediosSipsaCiudad — preço médio diário por cidade, COP/kg

Particularidade da fonte: a operação NÃO aceita parâmetro. Cada chamada
devolve a base inteira (~112 MB, sem compressão, ~30s). Incremental e
backfill são a mesma chamada com corte de data diferente, por isso o
parse é streaming (iterparse) e a resposta vai para disco, nunca para a
memória de uma vez.

Câmbio: TRM oficial (Superfinanciera) via Socrata, que já vem com período
de vigência — casa a data da observação com vigenciadesde/vigenciahasta,
sem fill-forward artificial de fim de semana e feriado.

Uso:
    pip install requests --break-system-packages
    python etl_colombia_sipsa.py                 # incremental (14 dias)
    python etl_colombia_sipsa.py --backfill      # série inteira (desde 2020)
    python etl_colombia_sipsa.py --dias 30       # janela customizada
    python etl_colombia_sipsa.py --dry-run       # não grava, só relatório

Saída:
    colombia_precos.csv (sempre)
    upsert em colombia_precos (exceto em --dry-run)
"""

import argparse
import csv
import os
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta

import requests

from supabase_upsert import upsert, SUPABASE_URL, SUPABASE_KEY

# ── CONFIG ─────────────────────────────────────────────────────────────────────
WS_URL = "https://appweb.dane.gov.co/sipsaWS/SrvSipsaUpraBeanService"
WS_NS = "http://servicios.sipsa.co.gov.dane/"

SOAP_ENVELOPE = (
    "<?xml version='1.0' encoding='UTF-8'?>"
    "<soap:Envelope xmlns:soap='http://www.w3.org/2003/05/soap-envelope'"
    " xmlns:ser='http://servicios.sipsa.co.gov.dane/'>"
    "<soap:Body><ser:promediosSipsaCiudad/></soap:Body></soap:Envelope>"
)

# Produtos de interesse. O Tahití é a nossa variedade; o Común entra como
# contexto de mercado e NÃO deve ser misturado com o Tahití em média.
PRODUTOS = {"Limón Tahití", "Limón Común"}

# A série antiga traz CÚCUTA e SAN JOSÉ DE CÚCUTA como cidades distintas,
# que são o mesmo mercado. Sem isso vira mercado fantasma no filtro da aba
# e a média de "todos os mercados" conta Cúcuta duas vezes.
SINONIMOS_CIDADE = {
    "CUCUTA": "SAN JOSÉ DE CÚCUTA",
    "CÚCUTA": "SAN JOSÉ DE CÚCUTA",
    "SAN JOSE DE CUCUTA": "SAN JOSÉ DE CÚCUTA",
    "BOGOTA, D.C.": "BOGOTÁ, D.C.",
    "BOGOTÁ D.C.": "BOGOTÁ, D.C.",
    "MEDELLIN": "MEDELLÍN",
}

TRM_URL = "https://www.datos.gov.co/resource/32sa-8pi3.json"
TRM_DESDE = "2019-12-01"  # folga antes do início da série do SIPSA

# Guardas de sanidade. A fonte não avisa quando devolve menos do que deveria;
# gravar meia base é pior do que não gravar (mesma política do Chile).
MIN_REGISTROS_TOTAIS = 100_000
DIAS_FRESCOR = 10  # no incremental, exige Tahití em algum dia recente

TABELA = "colombia_precos"
CHAVE = "fecha,mercado,producto"
OUTPUT_CSV = "colombia_precos.csv"


# ── HELPERS ────────────────────────────────────────────────────────────────────
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


def norm_cidade(nome: str) -> str:
    n = " ".join((nome or "").split()).upper()
    return SINONIMOS_CIDADE.get(n, n)


def tag(elem) -> str:
    """Nome do elemento sem o namespace."""
    t = elem.tag
    return t.rsplit("}", 1)[-1] if "}" in t else t


# ── [1] EXTRAÇÃO ───────────────────────────────────────────────────────────────
def baixar_resposta(destino: str, tentativas: int = 3) -> int:
    """
    POST SOAP 1.2 gravando a resposta em disco. Retorna o tamanho em bytes.
    O servidor ignora Accept-Encoding: gzip, então são ~112 MB por chamada.
    """
    headers = {"Content-Type": "application/soap+xml;charset=UTF-8"}
    ultimo_erro = None
    for i in range(1, tentativas + 1):
        try:
            with requests.post(WS_URL, data=SOAP_ENVELOPE.encode("utf-8"),
                               headers=headers, timeout=(30, 300), stream=True) as r:
                r.raise_for_status()
                total = 0
                with open(destino, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1 << 20):
                        if chunk:
                            f.write(chunk)
                            total += len(chunk)
                if total < 1_000_000:
                    raise RuntimeError(f"resposta curta demais ({total} bytes)")
                return total
        except Exception as e:  # noqa: BLE001
            ultimo_erro = e
            print(f"   aviso: tentativa {i}/{tentativas} falhou — {e}")
            if i < tentativas:
                import time
                time.sleep(20 * i)
    raise RuntimeError(f"web service do DANE indisponível: {ultimo_erro}")


def parsear(caminho: str, corte: date | None):
    """
    Parse streaming do envelope SOAP. Devolve (linhas, total_registros).
    `corte` descarta observações anteriores à data; None mantém tudo.
    """
    linhas = []
    total = 0
    for _evt, elem in ET.iterparse(caminho, events=("end",)):
        if tag(elem) != "return":
            continue
        total += 1
        campos = {tag(c): (c.text or "").strip() for c in elem}
        elem.clear()

        produto = campos.get("producto", "")
        if produto not in PRODUTOS:
            continue

        bruto = campos.get("fechaCaptura", "")[:10]
        preco = campos.get("precioPromedio", "")
        if not bruto or not preco:
            continue
        try:
            fecha = datetime.strptime(bruto, "%Y-%m-%d").date()
            preco_f = float(preco)
        except ValueError:
            continue
        if preco_f <= 0:
            continue
        if corte and fecha < corte:
            continue

        mercado = norm_cidade(campos.get("ciudad", ""))
        if not mercado:
            continue

        linhas.append({
            "fecha": fecha,
            "semana": week_of_year_pq(fecha),
            "ano": fecha.year,
            "producto": produto,
            "mercado": mercado,
            "precio": round(preco_f, 2),
        })
    return linhas, total


# ── [2] CÂMBIO (TRM) ───────────────────────────────────────────────────────────
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


# ── [3] DEDUP ──────────────────────────────────────────────────────────────────
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


# ── MAIN ───────────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="ETL SIPSA Colômbia — preços de limão")
    ap.add_argument("--backfill", action="store_true",
                    help="mantém a série inteira devolvida pela fonte (desde 2020)")
    ap.add_argument("--dias", type=int, default=14,
                    help="janela do modo incremental em dias (padrão 14)")
    ap.add_argument("--dry-run", action="store_true",
                    help="não grava no Supabase, só gera o CSV e o relatório")
    args = ap.parse_args()

    corte = None if args.backfill else date.today() - timedelta(days=args.dias)
    modo = "backfill" if args.backfill else f"incremental ({args.dias} dias)"
    print(f"ETL SIPSA Colômbia — modo {modo}")

    with tempfile.TemporaryDirectory() as tmp:
        bruto = os.path.join(tmp, "sipsa_ciudad.xml")

        print("[1] Baixando promediosSipsaCiudad (sem filtro na origem, ~112 MB)")
        tamanho = baixar_resposta(bruto)
        print(f"    {tamanho/1_048_576:.1f} MB recebidos")

        print("[2] Parse streaming e filtro de produto")
        linhas, total = parsear(bruto, corte)

    print(f"    {total} registros na resposta, {len(linhas)} de limão na janela")

    if total < MIN_REGISTROS_TOTAIS:
        print(f"ABORTADO: a fonte devolveu {total} registros, abaixo do mínimo "
              f"de {MIN_REGISTROS_TOTAIS}. Base parcial não é gravada.")
        return 2
    if not linhas:
        print("ABORTADO: nenhuma linha de limão na janela.")
        return 2
    if not args.backfill:
        limite = date.today() - timedelta(days=DIAS_FRESCOR)
        recentes = [r for r in linhas
                    if r["producto"] == "Limón Tahití" and r["fecha"] >= limite]
        if not recentes:
            print(f"ABORTADO: nenhum Limón Tahití nos últimos {DIAS_FRESCOR} dias. "
                  "A fonte pode ter mudado o nome do produto.")
            return 2

    print("[3] Carregando TRM (Superfinanciera via datos.gov.co)")
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

    print("[4] Dedup")
    linhas, removidas = dedup(linhas)
    print(f"    {removidas} duplicatas removidas, {len(linhas)} registros finais")

    datas = [r["fecha"] for r in linhas]
    mercados = sorted({r["mercado"] for r in linhas})
    print(f"    período {min(datas)} a {max(datas)} | {len(mercados)} mercados")

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["fecha", "semana", "ano", "producto",
                                          "mercado", "precio", "cambio",
                                          "cambio_estimado"])
        w.writeheader()
        for row in sorted(linhas, key=lambda r: (r["fecha"], r["mercado"], r["producto"])):
            w.writerow({**row, "fecha": row["fecha"].isoformat()})
    print(f"[5] {OUTPUT_CSV} gravado")

    if args.dry_run:
        print("[6] --dry-run: nada enviado ao Supabase")
        tahiti = [r for r in linhas if r["producto"] == "Limón Tahití"]
        if tahiti:
            ultimo = max(tahiti, key=lambda r: r["fecha"])
            usd = ultimo["precio"] * 4.5 / ultimo["cambio"]
            print(f"    amostra: {ultimo['fecha']} {ultimo['mercado']} "
                  f"{ultimo['precio']} COP/kg = US$ {usd:.2f}/cx 4,5kg "
                  f"(TRM {ultimo['cambio']})")
        return 0

    print(f"[6] Upsert em {TABELA}")
    res = upsert(TABELA, linhas, batch_size=1000, on_conflict=CHAVE)
    print(f"    {res['inserted']} registros enviados")
    if res["errors"]:
        for err in res["errors"][:5]:
            print(f"    ERRO lote {err['batch_start']}: {err['status']} {err['detail']}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
