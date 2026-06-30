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
            <img src="data:image/png;base64,{LOGO_B64}" alt="TFruits" style="height:60px;width:auto;display:block"/>
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
