"""
Relatório Semanal BI Limão — TFruits
Captura screenshots de cada aba do dashboard via Playwright,
monta PDF e envia via Resend API.
"""

import os
import io
import base64
import requests
from datetime import datetime, timezone

# ── Configuração ────────────────────────────────────────────────────────────
RESEND_API_KEY = os.environ["RESEND_API_KEY"]

DASHBOARD_URL = "https://bi-limao.vercel.app"
FROM_EMAIL    = "reports@tradeconnex.com"
TO_EMAILS     = ["felipe.passos@tradeconnex.com"]

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

MESES_PT = ['janeiro','fevereiro','março','abril','maio','junho',
            'julho','agosto','setembro','outubro','novembro','dezembro']

hoje = datetime.now()
DATA_PT = f"{hoje.day} de {MESES_PT[hoje.month-1]} de {hoje.year}"

LOGO_B64 = "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"

# ── Fetch última semana com dados ─────────────────────────────────────────────
def fetch_ultima_semana():
    url = f"{SUPABASE_URL}/rest/v1/brasil_precos"
    params = "select=semana,ano&order=ano.desc,semana.desc&limit=1"
    r = requests.get(
        f"{url}?{params}",
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
        },
        timeout=15,
    )
    r.raise_for_status()
    rows = r.json()
    if rows:
        return int(rows[0]["semana"]), int(rows[0]["ano"])
    return None, None

semana_num, ano_num = fetch_ultima_semana()
semana_label = f"S{semana_num}/{ano_num}" if semana_num else hoje.strftime('%d/%m/%Y')
SUBJECT = f"📊 Relatório Semanal BI Limão — {semana_label}"

# Abas a capturar: (data-page, label)
ABAS = [
    ("brasil",        "Preços Brasil"),
    ("chile",         "Preços Chile"),
    ("europa",        "Preços Europa"),
    ("share",         "Share Brasil"),
    ("containers",    "Containers"),
    ("clima-local",   "Clima Local"),
    ("clima-global",  "Clima Global"),
]

# ── Screenshot via Playwright ────────────────────────────────────────────────
def capturar_screenshots():
    from playwright.sync_api import sync_playwright

    screenshots = []  # lista de bytes PNG

    with sync_playwright() as p:
        browser = p.chromium.launch(
            args=["--no-sandbox", "--disable-setuid-sandbox"]
        )
        page = browser.new_page(viewport={"width": 1440, "height": 900})

        print(f"  Abrindo {DASHBOARD_URL}...")
        page.goto(DASHBOARD_URL, wait_until="networkidle", timeout=60000)

        # Aguarda o dashboard carregar (KPIs visíveis)
        page.wait_for_selector(".kpi-card, .kpi-value, canvas", timeout=30000)
        page.wait_for_timeout(3000)  # aguarda animações

        # Tempo de espera por aba (ms) — Chile demora mais por buscar câmbio externo
        WAIT = {
            "chile": 8000,
        }
        DEFAULT_WAIT = 3500

        for data_page, label in ABAS:
            print(f"  Capturando: {label}...")

            # Clica na sidebar (nav-item), não na barra mobile
            page.click(f'.nav-item[data-page="{data_page}"]')
            wait_ms = WAIT.get(data_page, DEFAULT_WAIT)
            page.wait_for_timeout(wait_ms)

            # Aguarda spinner sumir se houver
            try:
                page.wait_for_selector(".loading", state="hidden", timeout=10000)
            except Exception:
                pass

            # Screenshot da viewport
            png = page.screenshot(full_page=False)
            screenshots.append((label, png))
            print(f"    ✓ {len(png):,} bytes")

        browser.close()

    return screenshots

# ── Montar PDF com Pillow ────────────────────────────────────────────────────
def montar_pdf(screenshots):
    from PIL import Image, ImageDraw, ImageFont

    pages = []
    for label, png_bytes in screenshots:
        img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
        pages.append(img)

    if not pages:
        raise ValueError("Nenhum screenshot capturado")

    buf = io.BytesIO()
    pages[0].save(
        buf,
        format="PDF",
        save_all=True,
        append_images=pages[1:],
        resolution=120,
    )
    buf.seek(0)
    return buf.read()

# ── Envio Resend ─────────────────────────────────────────────────────────────
def send_email(pdf_bytes):
    nome_arquivo = f"relatorio_bi_limao_{hoje.strftime('%Y_%m_%d')}.pdf"

    abas_html = "".join(
        f'<li style="margin:4px 0;color:#555">{label}</li>'
        for _, label in ABAS
    )

    payload = {
        "from": FROM_EMAIL,
        "to": TO_EMAILS,
        "subject": SUBJECT,
        "html": f"""
        <div style="font-family:Arial,sans-serif;max-width:600px;margin:auto">
          <div style="background:#4CAE4F;padding:24px;border-radius:8px 8px 0 0;display:flex;align-items:center;gap:16px">
            <img src="cid:logo_tfruits" alt="TFruits" style="height:60px;width:auto;display:block"/>
            <div>
              <h1 style="color:white;margin:0;font-size:22px">BI Limão — Relatório Semanal</h1>
              <p style="color:rgba(255,255,255,.85);margin:6px 0 0">{semana_label} · {DATA_PT}</p>
            </div>
          </div>
          <div style="padding:24px;background:#f9f9f9;border-radius:0 0 8px 8px">
            <p style="color:#333">Olá Felipe,</p>
            <p style="color:#333">Segue em anexo o relatório semanal do BI Limão com screenshots das abas:</p>
            <ul style="color:#333">{abas_html}</ul>
            <p style="margin-top:20px">
              <a href="{DASHBOARD_URL}"
                 style="background:#4CAE4F;color:white;padding:10px 20px;border-radius:6px;
                        text-decoration:none;font-weight:bold;display:inline-block">
                Abrir Dashboard ao vivo →
              </a>
            </p>
            <p style="color:#aaa;font-size:12px;margin-top:24px">
              Enviado automaticamente toda segunda-feira · TFruits ·
              <a href="{DASHBOARD_URL}" style="color:#4CAE4F">{DASHBOARD_URL.replace('https://','')}</a>
            </p>
          </div>
        </div>
        """,
        "attachments": [
            {
                "filename": nome_arquivo,
                "content": base64.b64encode(pdf_bytes).decode(),
            },
            {
                "filename": "logo_tfruits.png",
                "content": LOGO_B64,
                "content_id": "logo_tfruits",
                "inline": True,
            },
        ],
    }

    r = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=60,
    )
    r.raise_for_status()
    return r.json()

# ── Main ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("📸 Capturando screenshots do dashboard...")
    screenshots = capturar_screenshots()
    print(f"  {len(screenshots)} abas capturadas")

    print("📄 Montando PDF...")
    pdf_bytes = montar_pdf(screenshots)
    print(f"  PDF: {len(pdf_bytes):,} bytes")

    print("📧 Enviando via Resend...")
    result = send_email(pdf_bytes)
    print(f"  ✅ Enviado! ID: {result.get('id','—')}")
    print(f"  Para: {', '.join(TO_EMAILS)}")
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        