"""
Relatório Semanal BI Limão (TFruits)
Captura screenshots de cada aba do dashboard via Playwright,
monta PDF paginado com capa e envia via Resend API.
"""

import os
import io
import base64
import requests
from datetime import datetime, timezone

# ── Configuração ────────────────────────────────────────────────────────────
RESEND_API_KEY = os.environ["RESEND_API_KEY"]

DASHBOARD_URL = "https://bilimao.tfruits.com.br"
FROM_EMAIL    = "reports@tradeconnex.com"
TO_EMAILS = [
    "felipe@tfruits.com.br",
    "luan.santos@tfruits.com.br",
    "caroline@tfruits.com.br",
]
CC_EMAILS = ["felipe.passos@tradeconnex.com"]

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

MESES_PT = ['janeiro','fevereiro','março','abril','maio','junho',
            'julho','agosto','setembro','outubro','novembro','dezembro']

hoje = datetime.now()
DATA_PT = f"{hoje.day} de {MESES_PT[hoje.month-1]} de {hoje.year}"

from logo_b64 import LOGO_B64

# ── Fetch última semana com dados ────────────────────────────────────────────
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
SEMANA_TXT = f"Semana {semana_num} de {ano_num}" if semana_num else DATA_PT
SUBJECT = f"Relatório Semanal BI Limão {semana_label}"

# Abas a capturar: (data-page, label). TODAS as abas do dashboard.
ABAS = [
    ("brasil",        "Preços Brasil"),
    ("chile",         "Preços Chile"),
    ("europa",        "Preços Europa"),
    ("share",         "Share Brasil"),
    ("containers",    "Containers"),
    ("clima-local",   "Clima Local"),
    ("clima-global",  "Clima Global"),
    ("status",        "Status ETLs"),
]

# ── Screenshot via Playwright ────────────────────────────────────────────────
def capturar_screenshots():
    from playwright.sync_api import sync_playwright

    screenshots = []  # lista de (label, bytes PNG, boxes dos cards)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            args=["--no-sandbox", "--disable-setuid-sandbox"]
        )
        page = browser.new_page(viewport={"width": 1440, "height": 900})

        print(f"  Abrindo {DASHBOARD_URL}...")
        page.goto(DASHBOARD_URL, wait_until="networkidle", timeout=60000)

        # Aguarda o dashboard carregar (KPIs visíveis)
        page.wait_for_selector(".kpi-card, .kpi-value, canvas", timeout=30000)
        page.wait_for_timeout(3000)

        # Modo relatório: esconde navegação e acelera animações.
        # NAO usar `animation: none`: cards e page-header têm opacity:0
        # e dependem do fadeUp (forwards) para ficarem visíveis.
        # html/body têm height:100% e .main overflow-y:auto no dashboard:
        # sem liberar isso, o full_page só captura a viewport (conteúdo
        # rolando escondido dentro do .main). Descoberto em 31/07/2026.
        page.add_style_tag(content="""
            .sidebar, .mobile-bar { display: none !important; }
            html, body {
                height: auto !important;
                overflow: visible !important;
            }
            .app {
                display: block !important;
                height: auto !important;
                min-height: 0 !important;
            }
            .main {
                overflow: visible !important;
                height: auto !important;
                padding: 1.5rem 2rem 2rem !important;
            }
            * {
                animation-duration: 0.01s !important;
                animation-delay: 0s !important;
                transition: none !important;
            }
            .card-shell, .page-header {
                opacity: 1 !important;
                transform: none !important;
            }
        """)

        # Espera por aba (ms). Chile demora mais por buscar câmbio externo.
        WAIT = {
            "chile": 8000,
        }
        DEFAULT_WAIT = 3500

        for data_page, label in ABAS:
            print(f"  Capturando: {label}...")

            # Sidebar oculta no modo relatório: navega via JS
            page.evaluate(
                f'document.querySelector(\'.nav-item[data-page="{data_page}"]\').click()'
            )
            page.wait_for_timeout(WAIT.get(data_page, DEFAULT_WAIT))

            try:
                page.wait_for_selector(".loading", state="hidden", timeout=10000)
            except Exception:
                pass

            # Posição dos cards: usada para paginar sem cortar card no meio
            boxes = page.evaluate("""
                () => Array.from(
                    document.querySelectorAll('.card-shell, .page-header')
                ).map(e => {
                    const r = e.getBoundingClientRect();
                    return { top:    r.top    + window.scrollY,
                             bottom: r.bottom + window.scrollY };
                })
            """)

            # Página inteira: nada fica abaixo da dobra
            altura = page.evaluate(
                "() => Math.max(document.body.scrollHeight,"
                "               document.documentElement.scrollHeight)"
            )
            png = page.screenshot(full_page=True)
            screenshots.append((label, png, boxes))
            print(f"    ok {len(png):,} bytes, {len(boxes)} cards, {altura}px de altura")

        browser.close()

    return screenshots

# ── Montagem do PDF ──────────────────────────────────────────────────────────
# Todas as páginas com o mesmo tamanho (proporção A4 retrato).
PAGE_W, PAGE_H = 1440, 2036
BG      = "#090C09"
GREEN   = "#4CAE4F"
TEXT    = "#F0F4F0"
TEXT2   = "#8A9A8A"
TEXT3   = "#5A6A5A"
BORDER  = "#1E241E"

# Plus Jakarta Sans (tipografia do design system), baixada em runtime
# com fallback para DejaVu se o download falhar.
_PJS_BASE = "https://raw.githubusercontent.com/tokotype/PlusJakartaSans/master/fonts/ttf"
_PJS = {
    "regular":         "PlusJakartaSans-Regular.ttf",
    "medium":          "PlusJakartaSans-Medium.ttf",
    "semibold":        "PlusJakartaSans-SemiBold.ttf",
    "bold":            "PlusJakartaSans-Bold.ttf",
    "extrabold":       "PlusJakartaSans-ExtraBold.ttf",
    "extrabolditalic": "PlusJakartaSans-ExtraBoldItalic.ttf",
}
_FALLBACK = {"regular": "DejaVuSans.ttf", "medium": "DejaVuSans.ttf",
             "semibold": "DejaVuSans-Bold.ttf", "bold": "DejaVuSans-Bold.ttf",
             "extrabold": "DejaVuSans-Bold.ttf",
             "extrabolditalic": "DejaVuSans-BoldOblique.ttf"}

def _font(size, weight="regular"):
    from PIL import ImageFont
    path = f"/tmp/pjs_{weight}.ttf"
    if not os.path.exists(path):
        try:
            r = requests.get(f"{_PJS_BASE}/{_PJS[weight]}", timeout=20)
            r.raise_for_status()
            # grava em .part e renomeia: nunca deixa cache corrompido
            with open(path + ".part", "wb") as f:
                f.write(r.content)
            os.replace(path + ".part", path)
        except Exception:
            pass
    for candidato in (path, _FALLBACK[weight]):
        try:
            return ImageFont.truetype(candidato, size)
        except Exception:
            continue
    return ImageFont.load_default()

def _tracked(draw, xy, txt, font, fill, tracking):
    """Texto com letter-spacing (PIL não tem nativo)."""
    x, y = xy
    for ch in txt:
        draw.text((x, y), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font) + tracking
    return x

ORANGE = "#F15A22"

def montar_capa():
    """Capa: dark, tipografia grande, marca TFruits tipográfica."""
    from PIL import Image, ImageDraw

    capa = Image.new("RGB", (PAGE_W, PAGE_H), BG)
    d = ImageDraw.Draw(capa)
    M = 130  # margem lateral

    # Marca tipográfica: quadrado verde com T branco + Fruits laranja
    # (mesmo desenho do logo, renderizado vetorialmente: nunca quebra)
    sq = 88
    d.rounded_rectangle([M, 150, M + sq, 150 + sq], radius=20, fill=GREEN)
    d.text((M + sq / 2, 150 + sq / 2 - 2), "T",
           font=_font(60, "extrabold"), fill="#FFFFFF", anchor="mm")
    d.text((M + sq + 22, 150 + sq / 2), "Fruits",
           font=_font(68, "extrabolditalic"), fill=ORANGE, anchor="lm")

    # Bloco de título
    y = 660
    _tracked(d, (M, y), "BI LIMÃO", _font(30, "semibold"), GREEN, 14)
    y += 76
    d.text((M, y), "Relatório Semanal", font=_font(92, "extrabold"), fill=TEXT)
    y += 150
    d.rectangle([M, y, M + 190, y + 8], fill=GREEN)

    y += 74
    d.text((M, y), SEMANA_TXT, font=_font(42, "medium"), fill=TEXT)
    y += 70
    d.text((M, y), DATA_PT, font=_font(28, "regular"), fill=TEXT2)

    # Conteúdo em duas colunas
    y = 1280
    d.line([(M, y), (PAGE_W - M, y)], fill=BORDER, width=2)
    y += 46
    _tracked(d, (M, y), "CONTEÚDO", _font(22, "semibold"), TEXT3, 10)
    y += 66

    col_x = [M, PAGE_W // 2 + 40]
    metade = (len(ABAS) + 1) // 2
    for i, (_, label) in enumerate(ABAS):
        cx = col_x[i // metade]
        cy = y + (i % metade) * 64
        d.rectangle([cx, cy + 12, cx + 10, cy + 22], fill=GREEN)
        d.text((cx + 32, cy), label, font=_font(30, "regular"), fill=TEXT2)

    # Rodapé
    fy = PAGE_H - 120
    d.line([(M, fy), (PAGE_W - M, fy)], fill=BORDER, width=2)
    d.text((M, fy + 30), "TFruits", font=_font(24, "semibold"), fill=TEXT2)
    site = DASHBOARD_URL.replace("https://", "")
    w = d.textlength(site, font=_font(24, "regular"))
    d.text((PAGE_W - M - w, fy + 30), site, font=_font(24, "regular"), fill=TEXT3)

    return capa

def _paginar(png_bytes, boxes=None):
    """Fatia um screenshot em páginas PAGE_W x PAGE_H, quebrando ENTRE
    cards (nunca no meio de um) quando as posições são conhecidas."""
    from PIL import Image

    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    ratio = 1.0
    if img.width != PAGE_W:
        ratio = PAGE_W / img.width
        img = img.resize((PAGE_W, int(img.height * ratio)))

    fundos = sorted(set(
        int(b["bottom"] * ratio) for b in (boxes or [])
    ))

    RESPIRO = 28   # espaço extra depois do último card da página
    MINIMO  = 300  # avanço mínimo por página (evita loop com card gigante)

    paginas = []
    y = 0
    while y < img.height:
        limite = y + PAGE_H
        if limite >= img.height:
            corte = img.height
        else:
            # último card que fecha dentro da página
            candidatos = [f for f in fundos if y + MINIMO < f <= limite - RESPIRO]
            corte = (max(candidatos) + RESPIRO) if candidatos else limite

        chunk = img.crop((0, y, PAGE_W, min(corte, img.height)))
        canvas = Image.new("RGB", (PAGE_W, PAGE_H), BG)
        canvas.paste(chunk, (0, 0))
        paginas.append(canvas)
        y = corte
    return paginas

def montar_pdf(screenshots):
    pages = [montar_capa()]
    for label, png_bytes, boxes in screenshots:
        pages.extend(_paginar(png_bytes, boxes))

    if len(pages) < 2:
        raise ValueError("Nenhum screenshot capturado")

    buf = io.BytesIO()
    pages[0].save(
        buf,
        format="PDF",
        save_all=True,
        append_images=pages[1:],
        # 96 DPI: zoom 100% dos leitores = 1:1 com os pixels capturados
        resolution=96,
    )
    buf.seek(0)
    return buf.read()

# ── Envio Resend ─────────────────────────────────────────────────────────────
def send_email(pdf_bytes):
    nome_arquivo = f"relatorio_bi_limao_{hoje.strftime('%Y_%m_%d')}.pdf"

    secoes = ", ".join(label for _, label in ABAS[:-1]) + f" e {ABAS[-1][1]}"

    payload = {
        "from": FROM_EMAIL,
        "to": TO_EMAILS,
        "cc": CC_EMAILS,
        "subject": SUBJECT,
        "html": f"""
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
               style="max-width:600px;margin:auto;font-family:Arial,Helvetica,sans-serif;border-collapse:collapse">
          <tr>
            <td align="center" style="background:#ffffff;padding:22px 24px;border:1px solid #e8e8e8;border-bottom:none">
              <img src="cid:logo-tfruits" width="150" alt="TFruits"
                   style="display:block;width:150px;height:auto;border:0"/>
            </td>
          </tr>
          <tr>
            <td align="center" style="background:#4CAE4F;padding:22px 24px">
              <h1 style="color:#ffffff;margin:0;font-size:21px;line-height:1.3">Relatório Semanal BI Limão</h1>
              <p style="color:#eaf6ea;margin:6px 0 0;font-size:14px">{SEMANA_TXT}</p>
            </td>
          </tr>
          <tr>
            <td style="background:#fafafa;padding:26px 28px;border:1px solid #e8e8e8;border-top:none">
              <p style="color:#333;margin:0 0 14px;font-size:15px">Olá,</p>
              <p style="color:#333;margin:0 0 14px;font-size:15px;line-height:1.55">
                Segue em anexo o relatório desta semana, com as seções
                {secoes}.
              </p>
              <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:22px 0 0">
                <tr>
                  <td align="center" bgcolor="#4CAE4F" style="border-radius:6px">
                    <a href="{DASHBOARD_URL}"
                       style="display:inline-block;padding:12px 24px;color:#ffffff;
                              text-decoration:none;font-weight:bold;font-size:14px">
                      Abrir o dashboard
                    </a>
                  </td>
                </tr>
              </table>
              <p style="color:#adadad;font-size:12px;margin:26px 0 0">
                Enviado automaticamente toda segunda-feira pela TFruits
              </p>
            </td>
          </tr>
        </table>
        """,
        "attachments": [
            {
                "filename": nome_arquivo,
                "content": base64.b64encode(pdf_bytes).decode(),
            },
            {
                "filename": "logo_tfruits.png",
                "content": LOGO_B64,
                "content_type": "image/png",
                "content_id": "logo-tfruits",
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
    print("Capturando screenshots do dashboard...")
    screenshots = capturar_screenshots()
    print(f"  {len(screenshots)} abas capturadas")

    print("Montando PDF...")
    pdf_bytes = montar_pdf(screenshots)
    print(f"  PDF: {len(pdf_bytes):,} bytes")

    print("Enviando via Resend...")
    result = send_email(pdf_bytes)
    print(f"  Enviado. ID: {result.get('id','')}")
    print(f"  Para: {', '.join(TO_EMAILS)} (cc: {', '.join(CC_EMAILS)})")
