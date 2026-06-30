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

MESES_PT = ['janeiro','fevereiro','março','abril','maio','junho',
            'julho','agosto','setembro','outubro','novembro','dezembro']

hoje     = datetime.now()
DATA_PT  = f"{hoje.day} de {MESES_PT[hoje.month-1]} de {hoje.year}"
SUBJECT  = f"📊 Relatório Semanal BI Limão — {hoje.strftime('%d/%m/%Y')}"

# Abas a capturar: (data-page, label)
ABAS = [
    ("brasil",       "Preços Brasil"),
    ("chile",        "Preços Chile"),
    ("europa",       "Preços Europa"),
    ("share",        "Share Brasil"),
    ("containers",   "Containers"),
    ("clima-local",  "Clima Local"),
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

        for data_page, label in ABAS:
            print(f"  Capturando: {label}...")

            # Clica na nav-item correspondente
            page.click(f'[data-page="{data_page}"]')
            page.wait_for_timeout(2500)  # aguarda renderização dos gráficos

            # Screenshot full-page da área de conteúdo
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
          <div style="background:#4CAE4F;padding:24px;border-radius:8px 8px 0 0">
            <h1 style="color:white;margin:0;font-size:22px">🍋 BI Limão — Relatório Semanal</h1>
            <p style="color:rgba(255,255,255,.85);margin:6px 0 0">{DATA_PT}</p>
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
        "attachments": [{
            "filename": nome_arquivo,
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
