"""
etl_ceagesp.py — Limão Taiti no atacado da CEAGESP (série ALTERNATIVA ao HF Brasil)
==================================================================================
Por que existe (17/11/2026): o HF Brasil/Cepea colocou as páginas de estatística
atrás de desafio Cloudflare e a licença de dados do Cepea é CC BY-NC (uso
comercial só com autorização — pedido enviado ao Cepea). Enquanto isso, o BI
mostra a cotação pública da CEAGESP como referência de preço no Brasil.

ATENÇÃO — não é continuação da série HF Brasil:
  - HF Brasil/Cepea: preço ao PRODUTOR, praças paulistas (Bebedouro, Limeira…)
  - CEAGESP:          preço de ATACADO no Entreposto de São Paulo (ETSP)
O dashboard mostra as duas em blocos separados e rotulados. Não misturar.

Fonte: formulário público https://ceagesp.gov.br/cotacoes/
  POST cot_grupo=FRUTAS & cot_data=DD/MM/AAAA
  Pregões de frutas: segunda, quarta e sexta. Feriado/dia sem pregão → tabela vazia.
  "LIMÃO TAITI" em 3 classificações: 15 A 18 DZ, 21 A 27 DZ, ACIMA DE 31 DZ
  Colunas: Menor / Comum / Maior, em R$/kg.

Uso:
  python etl_ceagesp.py                      # últimos 14 dias (rotina semanal)
  python etl_ceagesp.py --desde 2023-01-01   # backfill
  python etl_ceagesp.py --desde 2026-11-01 --ate 2026-11-13 --dry-run
"""

import argparse
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone

import requests

URL = "https://ceagesp.gov.br/cotacoes/"
GRUPO = "FRUTAS"
PRODUTO = "LIMÃO TAITI"
TABELA = "ceagesp_precos"
DIAS_PREGAO = {0, 2, 4}          # seg, qua, sex
JANELA_PADRAO_DIAS = 14
PAUSA_S = 1.0                    # gentileza com o servidor público
HEADERS = {"User-Agent": "BI-Limao-TFruits/1.0 (cotacoes publicas CEAGESP)"}

RE_DATA = re.compile(r"<b>Data:</b>\s*<span>\s*([0-9/]+)\s*</span>")
RE_LINHA = re.compile(
    r'<td class="ta_l">\s*LIM[ÃA]O TAITI\s*</td>'
    r"<td>([^<]*)</td><td>([^<]*)</td><td>([^<]*)</td><td>([^<]*)</td><td>([^<]*)</td>",
    re.IGNORECASE,
)


def week_of_year_pq(d: date) -> int:
    """Mesma semana do brasil_precos (Date.WeekOfYear do Power Query, início no domingo)."""
    jan1 = date(d.year, 1, 1)
    inicio_semana1 = jan1 - timedelta(days=(jan1.weekday() + 1) % 7)
    return (d - inicio_semana1).days // 7 + 1


def _num(txt: str):
    txt = txt.strip().replace(".", "").replace(",", ".")
    try:
        return float(txt)
    except ValueError:
        return None


def buscar_dia(s: requests.Session, d: date) -> list[dict]:
    r = s.post(URL, data={"cot_grupo": GRUPO, "cot_data": d.strftime("%d/%m/%Y")}, timeout=40)
    r.raise_for_status()
    m = RE_DATA.search(r.text)
    if not m:
        return []                                   # sem pregão (feriado etc.)
    if m.group(1) != d.strftime("%d/%m/%Y"):
        raise RuntimeError(f"CEAGESP devolveu a data {m.group(1)} para {d:%d/%m/%Y}")

    agora = datetime.now(timezone.utc).isoformat()
    linhas = []
    for classif, unidade, menor, comum, maior in RE_LINHA.findall(r.text):
        p_comum = _num(comum)
        if p_comum is None:
            continue
        linhas.append({
            "data": d.isoformat(),
            "semana": week_of_year_pq(d),
            "ano": d.year,
            "produto": PRODUTO,
            "classificacao": classif.strip(),
            "unidade": unidade.strip(),
            "preco_min": _num(menor),
            "preco_comum": p_comum,
            "preco_max": _num(maior),
            "preco_comum_4_5kg": round(p_comum * 4.5, 4),
            "extracted_at": agora,
        })
    return linhas


def dias_pregao(desde: date, ate: date):
    d = desde
    while d <= ate:
        if d.weekday() in DIAS_PREGAO:
            yield d
        d += timedelta(days=1)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--desde", type=date.fromisoformat)
    ap.add_argument("--ate", type=date.fromisoformat, default=date.today())
    ap.add_argument("--dry-run", action="store_true", help="só imprime, não grava")
    args = ap.parse_args()
    desde = args.desde or (args.ate - timedelta(days=JANELA_PADRAO_DIAS))

    print(f"CEAGESP — {PRODUTO} — {desde:%d/%m/%Y} a {args.ate:%d/%m/%Y}")
    s = requests.Session()
    s.headers.update(HEADERS)

    registros, dias_ok, dias_vazios, falhas = [], 0, 0, []
    for d in dias_pregao(desde, args.ate):
        try:
            linhas = buscar_dia(s, d)
        except Exception as e:                      # um dia ruim não derruba o lote
            falhas.append(f"{d}: {e}")
            print(f"  {d:%d/%m/%Y}  ERRO {e}")
            time.sleep(PAUSA_S * 3)
            continue
        if linhas:
            dias_ok += 1
            registros.extend(linhas)
            resumo = "  ".join(f"{l['classificacao']}={l['preco_comum']:.2f}" for l in linhas)
            print(f"  {d:%d/%m/%Y}  {resumo}")
        else:
            dias_vazios += 1
            print(f"  {d:%d/%m/%Y}  sem pregão")
        time.sleep(PAUSA_S)

    print(f"Pregões com dado: {dias_ok} · sem pregão: {dias_vazios} · falhas: {len(falhas)} · linhas: {len(registros)}")

    if not registros:
        print("❌ Nenhuma cotação de Limão Taiti na janela — layout da CEAGESP mudou ou site fora do ar.")
        sys.exit(1)

    if args.dry_run:
        print("(dry-run — nada gravado)")
        return

    from supabase_upsert import upsert
    res = upsert(TABELA, registros, on_conflict="data,classificacao")
    print(f"Supabase: {res['inserted']} linhas gravadas em {TABELA}")
    if res["errors"]:
        print("❌ Erros no upsert:", res["errors"])
        sys.exit(1)
    if falhas and len(falhas) > dias_ok:            # mais falha que sucesso = algo errado
        print("❌ Muitas falhas de requisição:", falhas[:5])
        sys.exit(1)
    print("✅ CEAGESP ok")


if __name__ == "__main__":
    main()
