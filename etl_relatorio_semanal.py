"""
Relatório Semanal BI Limão — TFruits
Gera PDF com dados do Supabase e envia via Resend API.
Seções: Preços (Brasil/Chile/Europa) | Share Brasil | Containers | Clima
"""

import os
import io
import json
import math
import requests
from datetime import datetime, timezone

# ── Configuração ────────────────────────────────────────────────────────────
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
RESEND_API_KEY = os.environ["RESEND_API_KEY"]

FROM_EMAIL   = "reports@tradeconnex.com"
TO_EMAILS    = ["felipe.passos@tradeconnex.com"]
SUBJECT      = f"📊 Relatório Semanal BI Limão — {datetime.now().strftime('%d/%m/%Y')}"

VERDE   = (0.298, 0.682, 0.310)   # #4CAE4F
LARANJA = (0.945, 0.353, 0.133)   # #F15A22
CINZA_ESCURO  = (0.2,  0.2,  0.2)
CINZA_CLARO   = (0.95, 0.95, 0.95)
BRANCO  = (1, 1, 1)

# ── Supabase helper ─────────────────────────────────────────────────────────
def sb_fetch(table, params=None):
    """
    params pode ser dict ou lista de tuplas (para order múltiplo).
    Ex: [("select","semana,ano"), ("order","ano.desc"), ("order","semana.desc")]
    """
    PAGE = 1000
    if params is None:
        params = []
    elif isinstance(params, dict):
        params = list(params.items())
    all_rows = []
    offset = 0
    while True:
        headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Range": f"{offset}-{offset + PAGE - 1}",
            "Range-Unit": "items",
            "Prefer": "count=none",
        }
        qs = "&".join(f"{k}={v}" for k, v in params)
        url = f"{SUPABASE_URL}/rest/v1/{table}?{qs}" if qs else f"{SUPABASE_URL}/rest/v1/{table}"
        r = requests.get(url, headers=headers, timeout=30)
        r.raise_for_status()
        page = r.json()
        all_rows.extend(page)
        if len(page) < PAGE:
            break
        offset += PAGE
    return all_rows

# ── Coleta de dados ──────────────────────────────────────────────────────────
def fetch_precos_brasil():
    rows = sb_fetch("brasil_precos", [("select","semana,ano,preco_medio,variedade"), ("order","ano.desc"), ("order","semana.desc")])
    anos = sorted(set(r["ano"] for r in rows), reverse=True)
    cur_ano = anos[0] if anos else datetime.now().year
    cur = [r for r in rows if r["ano"] == cur_ano]
    variedades = sorted(set(r["variedade"] for r in cur))
    result = []
    for v in variedades[:4]:
        semanas = sorted(set(r["semana"] for r in cur if r["variedade"] == v), reverse=True)[:4]
        ultimas = []
        for s in semanas:
            r = next((x for x in cur if x["variedade"] == v and x["semana"] == s), None)
            if r:
                ultimas.append({"semana": s, "preco": float(r["preco_medio"] or 0)})
        if ultimas:
            result.append({"variedade": v, "dados": sorted(ultimas, key=lambda x: x["semana"])})
    return result, cur_ano

def fetch_precos_chile():
    rows = sb_fetch("chile_precos", [("select","semana,ano,precio,mercado"), ("order","ano.desc"), ("order","semana.desc")])
    if not rows:
        return [], datetime.now().year
    anos = sorted(set(r["ano"] for r in rows), reverse=True)
    cur_ano = anos[0]
    cur = [r for r in rows if r["ano"] == cur_ano]
    # top mercado por registros
    mercado_count = {}
    for r in cur:
        mercado_count[r["mercado"]] = mercado_count.get(r["mercado"], 0) + 1
    top_mercado = max(mercado_count, key=mercado_count.get) if mercado_count else None
    if not top_mercado:
        return [], cur_ano
    # CLP→USD (fallback)
    clp_usd = 1 / 970
    try:
        fx = requests.get("https://open.er-api.com/v6/latest/USD", timeout=10).json()
        clp_usd = 1 / fx["rates"]["CLP"]
    except Exception:
        pass
    mercado_rows = [r for r in cur if r["mercado"] == top_mercado]
    semanas = sorted(set(r["semana"] for r in mercado_rows), reverse=True)[:4]
    dados = []
    for s in semanas:
        r = next((x for x in mercado_rows if x["semana"] == s), None)
        if r:
            dados.append({"semana": s, "preco": round(float(r["precio"] or 0) * clp_usd, 2)})
    return sorted(dados, key=lambda x: x["semana"]), cur_ano, top_mercado

def fetch_precos_europa():
    rows = sb_fetch("europa_precos", [("select","semana,ano,preco_eur"), ("order","ano.desc"), ("order","semana.desc")])
    if not rows:
        return [], datetime.now().year
    anos = sorted(set(r["ano"] for r in rows), reverse=True)
    cur_ano = anos[0]
    cur = [r for r in rows if r["ano"] == cur_ano]
    semanas = sorted(set(r["semana"] for r in cur), reverse=True)[:4]
    dados = []
    for s in semanas:
        r = next((x for x in cur if x["semana"] == s), None)
        if r:
            dados.append({"semana": s, "preco": float(r["preco_eur"] or 0)})
    return sorted(dados, key=lambda x: x["semana"]), cur_ano

def fetch_share_brasil():
    rows = sb_fetch("comexstat_exportacoes", [("select","pais,kg_liquido,ano"), ("order","ano.desc")])
    if not rows:
        return [], datetime.now().year
    cur_ano = max(r["ano"] for r in rows)
    cur = [r for r in rows if r["ano"] == cur_ano]
    totais = {}
    for r in cur:
        totais[r["pais"]] = totais.get(r["pais"], 0) + float(r["kg_liquido"] or 0)
    top10 = sorted(totais.items(), key=lambda x: x[1], reverse=True)[:10]
    total_geral = sum(v for _, v in top10)
    return [{"pais": p, "volume_t": round(v / 1000), "pct": round(v / total_geral * 100, 1) if total_geral else 0} for p, v in top10], cur_ano

def fetch_containers():
    rows = sb_fetch("v_containers_semanal", [("select","week,ano,total_containers,flow"), ("order","ano.desc"), ("order","week.desc")])
    if not rows:
        return {}
    anos = sorted(set(r["ano"] for r in rows), reverse=True)
    cur_ano = anos[0]
    cur = [r for r in rows if r["ano"] == cur_ano]
    shipped = [r for r in cur if r["flow"] == "Shipped" and (r["total_containers"] or 0) > 0]
    arrivals = [r for r in cur if r["flow"] == "Arrivals" and (r["total_containers"] or 0) > 0]
    last_shipped  = max((r["week"] for r in shipped),  default=0)
    last_arrivals = max((r["week"] for r in arrivals), default=0)
    def get_val(data, week):
        r = next((x for x in data if x["week"] == week), None)
        return int(r["total_containers"]) if r else 0
    return {
        "shipped":  get_val(shipped,  last_shipped),
        "arrivals": get_val(arrivals, last_arrivals),
        "semana_shipped":  last_shipped,
        "semana_arrivals": last_arrivals,
        "ano": cur_ano,
    }

def fetch_clima():
    # Clima local: última extração
    rows_fc = sb_fetch("clima_brasil_forecast", [("select","cidade,data_previsao,temp_max,temp_min,chuva_mm,descricao,extracted_at"), ("order","extracted_at.desc"), ("order","data_previsao.asc")])
    cidades = list(dict.fromkeys(r["cidade"] for r in rows_fc))
    resumo = []
    for cidade in cidades[:4]:
        fc_cidade = [r for r in rows_fc if r["cidade"] == cidade]
        max_ext = max((r["extracted_at"] for r in fc_cidade), default="")
        fc_latest = [r for r in fc_cidade if r["extracted_at"] == max_ext][:3]
        if fc_latest:
            resumo.append({"cidade": cidade, "forecast": fc_latest})
    return resumo

# ── PDF ──────────────────────────────────────────────────────────────────────
def build_pdf(brasil, chile_data, europa_data, share, containers, clima, ano_brasil, ano_chile=None, mercado_chile=None, ano_europa=None):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=20*mm, rightMargin=20*mm,
                            topMargin=15*mm, bottomMargin=15*mm)

    W = A4[0] - 40*mm  # largura útil

    styles = getSampleStyleSheet()
    verde_rl   = colors.Color(*VERDE)
    laranja_rl = colors.Color(*LARANJA)
    cinza_rl   = colors.Color(0.95, 0.95, 0.95)
    cinza_dark = colors.Color(0.2, 0.2, 0.2)

    title_style = ParagraphStyle("title", parent=styles["Normal"],
                                 fontSize=22, textColor=verde_rl,
                                 fontName="Helvetica-Bold", spaceAfter=4)
    sub_style   = ParagraphStyle("sub", parent=styles["Normal"],
                                 fontSize=10, textColor=cinza_dark, spaceAfter=12)
    section_style = ParagraphStyle("section", parent=styles["Normal"],
                                   fontSize=13, textColor=colors.white,
                                   fontName="Helvetica-Bold",
                                   backColor=verde_rl, leftIndent=4,
                                   spaceBefore=14, spaceAfter=6,
                                   borderPad=4)
    label_style = ParagraphStyle("label", parent=styles["Normal"],
                                 fontSize=9, textColor=cinza_dark)
    normal = styles["Normal"]
    normal.fontSize = 9

    def section_header(text):
        return [
            Spacer(1, 4*mm),
            Paragraph(f"  {text}", section_style),
        ]

    def kpi_table(kpis):
        # kpis = list of (label, value, unit)
        n = len(kpis)
        col_w = W / n
        data = [[Paragraph(f"<b>{v}</b> <font size=8>{u}</font>", ParagraphStyle("kv", fontSize=16, textColor=verde_rl, fontName="Helvetica-Bold"))
                 for _, v, u in kpis],
                [Paragraph(l, ParagraphStyle("kl", fontSize=8, textColor=cinza_dark, alignment=TA_CENTER))
                 for l, _, _ in kpis]]
        t = Table(data, colWidths=[col_w]*n)
        t.setStyle(TableStyle([
            ("ALIGN",      (0,0), (-1,-1), "CENTER"),
            ("VALIGN",     (0,0), (-1,-1), "MIDDLE"),
            ("ROWBACKGROUNDS", (0,0), (-1,-1), [cinza_rl, colors.white]),
            ("BOX",        (0,0), (-1,-1), 0.5, colors.lightgrey),
            ("INNERGRID",  (0,0), (-1,-1), 0.3, colors.lightgrey),
            ("TOPPADDING", (0,0), (-1,-1), 4),
            ("BOTTOMPADDING", (0,0), (-1,-1), 4),
        ]))
        return t

    def preco_table(dados, moeda="R$"):
        if not dados:
            return Paragraph("Sem dados disponíveis.", normal)
        header = ["Semana"] + [f"S{d['semana']}" for d in dados]
        vals   = [moeda] + [f"{d['preco']:.2f}" for d in dados]
        t = Table([header, vals], colWidths=[W/5] + [W/5]*(len(dados)))
        t.setStyle(TableStyle([
            ("BACKGROUND",    (0,0), (-1,0), verde_rl),
            ("TEXTCOLOR",     (0,0), (-1,0), colors.white),
            ("FONTNAME",      (0,0), (-1,0), "Helvetica-Bold"),
            ("ALIGN",         (0,0), (-1,-1), "CENTER"),
            ("ROWBACKGROUNDS",(0,1), (-1,-1), [cinza_rl, colors.white]),
            ("FONTSIZE",      (0,0), (-1,-1), 9),
            ("TOPPADDING",    (0,0), (-1,-1), 4),
            ("BOTTOMPADDING", (0,0), (-1,-1), 4),
            ("BOX",           (0,0), (-1,-1), 0.5, colors.lightgrey),
            ("INNERGRID",     (0,0), (-1,-1), 0.3, colors.lightgrey),
        ]))
        return t

    story = []

    # ── Capa ────────────────────────────────────────────────────────────────
    story.append(Spacer(1, 8*mm))
    story.append(Paragraph("🍋 BI Limão — TFruits", title_style))
    story.append(Paragraph(f"Relatório Semanal · {datetime.now().strftime('%d de %B de %Y')}", sub_style))
    story.append(HRFlowable(width=W, thickness=2, color=verde_rl, spaceAfter=8))

    # ── Preços Brasil ────────────────────────────────────────────────────────
    story += section_header(f"Preços Brasil — {ano_brasil}")
    if brasil:
        for item in brasil:
            story.append(Paragraph(f"<b>{item['variedade']}</b>", ParagraphStyle("var", fontSize=10, textColor=cinza_dark, fontName="Helvetica-Bold", spaceBefore=4)))
            story.append(preco_table(item["dados"], "R$/kg"))
            story.append(Spacer(1, 3*mm))
    else:
        story.append(Paragraph("Sem dados disponíveis.", normal))

    # ── Preços Chile ─────────────────────────────────────────────────────────
    ano_ch = ano_chile or datetime.now().year
    mkt_ch = mercado_chile or ""
    story += section_header(f"Preços Chile — {ano_ch} · {mkt_ch}")
    if isinstance(chile_data, list) and chile_data:
        story.append(preco_table(chile_data, "USD/kg"))
    else:
        story.append(Paragraph("Sem dados disponíveis.", normal))
    story.append(Spacer(1, 3*mm))

    # ── Preços Europa ─────────────────────────────────────────────────────────
    ano_eu = ano_europa or datetime.now().year
    story += section_header(f"Preços Europa — {ano_eu}")
    if isinstance(europa_data, list) and europa_data:
        story.append(preco_table(europa_data, "€/kg"))
    else:
        story.append(Paragraph("Sem dados disponíveis.", normal))
    story.append(Spacer(1, 3*mm))

    # ── Share Brasil ─────────────────────────────────────────────────────────
    ano_sh = share[1] if isinstance(share, tuple) else datetime.now().year
    share_rows = share[0] if isinstance(share, tuple) else share
    story += section_header(f"Share Brasil (Exportações) — {ano_sh}")
    if share_rows:
        total_t = sum(r["volume_t"] for r in share_rows)
        story.append(kpi_table([("Total Exportado", f"{total_t:,}".replace(",","."), "t")]))
        story.append(Spacer(1, 4*mm))
        tbl_data = [["País", "Volume (t)", "%"]]
        for row in share_rows:
            tbl_data.append([row["pais"], f"{row['volume_t']:,}".replace(",","."), f"{row['pct']}%"])
        t = Table(tbl_data, colWidths=[W*0.55, W*0.25, W*0.2])
        t.setStyle(TableStyle([
            ("BACKGROUND",    (0,0), (-1,0), verde_rl),
            ("TEXTCOLOR",     (0,0), (-1,0), colors.white),
            ("FONTNAME",      (0,0), (-1,0), "Helvetica-Bold"),
            ("ALIGN",         (0,0), (0,-1), "LEFT"),
            ("ALIGN",         (1,0), (-1,-1), "CENTER"),
            ("ROWBACKGROUNDS",(0,1), (-1,-1), [cinza_rl, colors.white]),
            ("FONTSIZE",      (0,0), (-1,-1), 9),
            ("TOPPADDING",    (0,0), (-1,-1), 3),
            ("BOTTOMPADDING", (0,0), (-1,-1), 3),
            ("BOX",           (0,0), (-1,-1), 0.5, colors.lightgrey),
            ("INNERGRID",     (0,0), (-1,-1), 0.3, colors.lightgrey),
        ]))
        story.append(t)
    else:
        story.append(Paragraph("Sem dados disponíveis.", normal))
    story.append(Spacer(1, 3*mm))

    # ── Containers ───────────────────────────────────────────────────────────
    story += section_header(f"Containers — {containers.get('ano', '')}")
    if containers:
        story.append(kpi_table([
            (f"Shipped · S{containers['semana_shipped']}", str(containers["shipped"]), "ctrs"),
            (f"Arrivals · S{containers['semana_arrivals']}", str(containers["arrivals"]), "ctrs"),
        ]))
    else:
        story.append(Paragraph("Sem dados disponíveis.", normal))
    story.append(Spacer(1, 3*mm))

    # ── Clima ─────────────────────────────────────────────────────────────────
    story += section_header("Clima Local — Próximos 3 dias")
    if clima:
        for city in clima:
            story.append(Paragraph(f"<b>{city['cidade']}</b>", ParagraphStyle("city", fontSize=10, fontName="Helvetica-Bold", textColor=cinza_dark, spaceBefore=4)))
            fc = city["forecast"]
            tbl_data = [["Data", "Máx °C", "Mín °C", "Chuva mm", "Descrição"]]
            for f in fc:
                tbl_data.append([
                    f.get("data_previsao","")[:10],
                    f.get("temp_max","—"),
                    f.get("temp_min","—"),
                    f"{float(f.get('chuva_mm') or 0):.1f}",
                    f.get("descricao","—")[:30],
                ])
            t = Table(tbl_data, colWidths=[W*0.18, W*0.12, W*0.12, W*0.15, W*0.43])
            t.setStyle(TableStyle([
                ("BACKGROUND",    (0,0), (-1,0), colors.Color(0.6,0.8,0.6)),
                ("FONTNAME",      (0,0), (-1,0), "Helvetica-Bold"),
                ("ALIGN",         (0,0), (-1,-1), "CENTER"),
                ("ALIGN",         (4,0), (4,-1), "LEFT"),
                ("ROWBACKGROUNDS",(0,1), (-1,-1), [cinza_rl, colors.white]),
                ("FONTSIZE",      (0,0), (-1,-1), 8),
                ("TOPPADDING",    (0,0), (-1,-1), 3),
                ("BOTTOMPADDING", (0,0), (-1,-1), 3),
                ("BOX",           (0,0), (-1,-1), 0.5, colors.lightgrey),
                ("INNERGRID",     (0,0), (-1,-1), 0.3, colors.lightgrey),
            ]))
            story.append(t)
            story.append(Spacer(1, 3*mm))
    else:
        story.append(Paragraph("Sem dados disponíveis.", normal))

    # ── Rodapé ───────────────────────────────────────────────────────────────
    story.append(Spacer(1, 6*mm))
    story.append(HRFlowable(width=W, thickness=1, color=colors.lightgrey))
    story.append(Paragraph(
        f"<font size=7 color='grey'>Gerado automaticamente em {datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M')} UTC · BI Limão TFruits · Dados: Supabase</font>",
        ParagraphStyle("footer", alignment=TA_CENTER, spaceAfter=0)
    ))

    doc.build(story)
    buf.seek(0)
    return buf.read()

# ── Envio Resend ─────────────────────────────────────────────────────────────
def send_email(pdf_bytes):
    import base64
    payload = {
        "from": FROM_EMAIL,
        "to": TO_EMAILS,
        "subject": SUBJECT,
        "html": f"""
        <div style="font-family:Arial,sans-serif;max-width:600px;margin:auto">
          <div style="background:#4CAE4F;padding:24px;border-radius:8px 8px 0 0">
            <h1 style="color:white;margin:0;font-size:22px">🍋 BI Limão — Relatório Semanal</h1>
            <p style="color:rgba(255,255,255,.85);margin:6px 0 0">{datetime.now().strftime('%d de %B de %Y')}</p>
          </div>
          <div style="padding:24px;background:#f9f9f9;border-radius:0 0 8px 8px">
            <p>Olá,</p>
            <p>Segue em anexo o relatório semanal do BI Limão com os dados mais recentes de:</p>
            <ul>
              <li>Preços Brasil, Chile e Europa</li>
              <li>Share de exportações Brasil</li>
              <li>Containers (Shipped &amp; Arrivals)</li>
              <li>Clima local (próximos 3 dias)</li>
            </ul>
            <p style="margin-top:20px">
              <a href="https://bi-limao.vercel.app" style="background:#4CAE4F;color:white;padding:10px 20px;border-radius:6px;text-decoration:none;font-weight:bold">
                Abrir Dashboard →
              </a>
            </p>
            <p style="color:#888;font-size:12px;margin-top:24px">
              Enviado automaticamente · TFruits · <a href="https://bi-limao.vercel.app" style="color:#4CAE4F">bi-limao.vercel.app</a>
            </p>
          </div>
        </div>
        """,
        "attachments": [{
            "filename": f"relatorio_bi_limao_{datetime.now().strftime('%Y_%m_%d')}.pdf",
            "content": base64.b64encode(pdf_bytes).decode(),
        }],
    }
    r = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )
    r.raise_for_status()
    return r.json()

# ── Main ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("📥 Buscando dados do Supabase...")

    brasil_data, ano_brasil = fetch_precos_brasil()
    print(f"  Brasil: {len(brasil_data)} variedades")

    chile_result = fetch_precos_chile()
    if len(chile_result) == 3:
        chile_data, ano_chile, mercado_chile = chile_result
    else:
        chile_data, ano_chile, mercado_chile = [], None, None
    print(f"  Chile: {len(chile_data)} semanas ({mercado_chile})")

    europa_data, ano_europa = fetch_precos_europa()
    print(f"  Europa: {len(europa_data)} semanas")

    share_rows, ano_share = fetch_share_brasil()
    print(f"  Share: {len(share_rows)} países")

    containers = fetch_containers()
    print(f"  Containers: shipped={containers.get('shipped',0)} arrivals={containers.get('arrivals',0)}")

    clima = fetch_clima()
    print(f"  Clima: {len(clima)} cidades")

    print("📄 Gerando PDF...")
    pdf_bytes = build_pdf(
        brasil_data, chile_data, europa_data,
        (share_rows, ano_share), containers, clima,
        ano_brasil, ano_chile, mercado_chile, ano_europa
    )
    print(f"  PDF: {len(pdf_bytes):,} bytes")

    print("📧 Enviando via Resend...")
    result = send_email(pdf_bytes)
    print(f"  ✅ Enviado! ID: {result.get('id','—')}")
    print(f"  Para: {', '.join(TO_EMAILS)}")
