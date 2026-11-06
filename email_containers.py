"""
E-mail semanal de Containers (BI Limão / TFruits)

Chegadas de containers de limão do Brasil na Europa e UK, semana fechada vs
mesma semana do ano anterior. Substitui a página "Containers Limão" do Power BI
(que era só um print estático da fonte).

Layout v4 (aprovado por Felipe em 06/11/2026, depois de rejeitar a v3 escura):
número da semana em destaque, linha com últimas 4 semanas e acumulado, gráfico
de colunas 2026 x 2025 das últimas 15 semanas e totais do ano. Sem link para o
BI (pedido do Felipe). HTML em tabela com estilos inline (Outlook não entende
flex); colunas são divs com altura fixa dentro de células alinhadas na base.

Variáveis de ambiente:
  SUPABASE_URL, SUPABASE_KEY      leitura das views
  RESEND_API_KEY_TFRUITS          chave da conta Resend onde o domínio
                                  tfruits.com.br está verificado
  EMAIL_TO                        opcional, sobrescreve destinatários (teste)
  DRY_RUN=1                       só gera email_containers.html, não envia
  ENVIO_EM                        opcional, ISO 8601 (ex. 2026-11-09T07:30:00-03:00).
                                  O e-mail é montado como se fosse esse momento e
                                  fica AGENDADO no Resend para ele. Usado pelo run
                                  de domingo para entregar segunda 07:30 sem
                                  depender do cron do GitHub.
"""

import os
import sys
import requests
from datetime import datetime, timedelta, timezone

# ── Configuração ────────────────────────────────────────────────────────────
FROM_EMAIL = "TFruits BI Limão <reports@tfruits.com.br>"
TO_EMAILS = ["luan.santos@tfruits.com.br"]
CC_EMAILS = ["felipe.passos@tradeconnex.com"]

DASHBOARD_URL = "https://bilimao.tfruits.com.br"
LOGO_URL = f"{DASHBOARD_URL}/logo_tfruits.png"
# Logo vai ANEXADO inline (cid). Imagem por URL externa o Outlook bloqueia até o
# usuário clicar em "baixar imagens" (visto no teste de 06/11/2026).
LOGO_ARQUIVO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo_tfruits.png")
SITE_URL = "https://www.tfruits.com.br"

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
DRY_RUN = os.environ.get("DRY_RUN") == "1"

BRT = timezone(timedelta(hours=-3))
SEMANAS_ANTES = 11    # semanas fechadas no gráfico antes da última fechada
SEMANAS_DEPOIS = 3    # em curso + previstas (total 15 colunas)
ALTURA_GRAFICO = 150  # px da coluna mais alta

# Paleta TFruits clara
INK, MUT, HAIR, FUNDO, CAIXA = "#141A14", "#6B746B", "#E8EAE6", "#F2F3EF", "#F6F7F4"
G, GPREV, GRY, O = "#3E9B43", "#BFE0C1", "#D5D9D2", "#F15A22"
FONT = "'Plus Jakarta Sans','Helvetica Neue',Helvetica,Arial,sans-serif"
NUM = "font-variant-numeric:tabular-nums;"


# ── Dados ───────────────────────────────────────────────────────────────────
def sb(tabela, params):
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/{tabela}",
        params=params,
        headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def carregar():
    # Poucas centenas de linhas; filtra o flow no servidor e pagina por segurança.
    semanal, offset = {}, 0
    while True:
        rows = sb("v_containers_semanal", {
            "select": "year,week,total_containers",
            "flow": "eq.Arrivals",
            "order": "year,week",
            "limit": 1000, "offset": offset,
        })
        for r in rows:
            if r["total_containers"] is not None:
                semanal[(int(r["year"]), int(r["week"]))] = float(r["total_containers"])
        if len(rows) < 1000:
            break
        offset += 1000

    totais = {}
    for r in sb("containers_totais", {
        "select": "year,total_containers,extracted_at",
        "flow": "eq.Arrivals",
        "order": "extracted_at.desc",
    }):
        totais.setdefault(int(r["year"]), float(r["total_containers"]))
    return semanal, totais


# ── Formatação ──────────────────────────────────────────────────────────────
def fmt(x):
    if x is None:
        return "&ndash;"
    s = f"{x:,.1f}" if x % 1 else f"{int(x):,}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def pct(a, b):
    return round((a - b) / b * 100) if b else None


def sinal(p):
    if p is None:
        return ""
    return ("+" if p >= 0 else "&minus;") + f"{abs(p)}%"


def cor_var(p):
    return O if (p is not None and p < 0) else G


# ── Montagem ────────────────────────────────────────────────────────────────
def montar(semanal, totais, hoje):
    seg_atual = hoje.date() - timedelta(days=hoje.weekday())
    fechada = (seg_atual - timedelta(weeks=1)).isocalendar()[:2]   # (ano, semana)
    em_curso = seg_atual.isocalendar()[:2]
    ano, W = fechada

    if fechada not in semanal:
        raise SystemExit(f"Sem dado de chegadas para a semana fechada {W}/{ano}. "
                         "E-mail não enviado para não sair com dado incompleto.")

    def val(a, w):
        return semanal.get((a, w))

    janela = [(seg_atual + timedelta(weeks=i)).isocalendar()[:2]
              for i in range(-1 - SEMANAS_ANTES, SEMANAS_DEPOIS)]

    w_atual, w_ant = val(ano, W), val(ano - 1, W)

    ult4 = [(seg_atual - timedelta(weeks=i)).isocalendar()[:2] for i in range(1, 5)]
    l4_atual = sum(val(a, w) or 0 for a, w in ult4)
    l4_ant = sum(val(a - 1, w) or 0 for a, w in ult4)

    # Acumulado comparável: semanas do ano corrente até a fechada que existem
    # nos DOIS anos (a fonte publica só uma janela móvel).
    sem_acum = sorted(w for (a, w) in semanal if a == ano and w <= W and (ano - 1, w) in semanal)
    acc_atual = sum(val(ano, w) for w in sem_acum)
    acc_ant = sum(val(ano - 1, w) for w in sem_acum)
    acc_label = f"Acumulado, semanas {sem_acum[0]} a {W}" if sem_acum else "Acumulado no ano"

    janela_vals = [val(a, w) or 0 for a, w in janela] + [val(a - 1, w) or 0 for a, w in janela]
    mx = max(janela_vals) or 1

    colunas, rotulos = "", ""
    for a, w in janela:
        futuro = (a, w) > fechada
        destaque = (a, w) == fechada
        v, v_ant = val(a, w), val(a - 1, w)
        h = max(2, round((v or 0) / mx * ALTURA_GRAFICO))
        h_ant = max(2, round((v_ant or 0) / mx * ALTURA_GRAFICO))
        cor = O if destaque else (GPREV if futuro else G)
        def barra(altura, largura, cor):
            # Outlook (motor do Word) ignora height de <div>; respeita height+bgcolor em <td>.
            return (f'<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>'
                    f'<td width="{largura}" height="{altura}" bgcolor="{cor}" style="width:{largura}px;height:{altura}px;'
                    f'background:{cor};font-size:1px;line-height:1px;mso-line-height-rule:exactly;">&nbsp;</td></tr></table>')
        rotulo = fmt(v) if v is not None else "&nbsp;"
        colunas += (
            f'<td valign="bottom" align="center" style="padding:0 1px;">'
            f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" align="center"><tr>'
            f'<td valign="bottom" align="center">'
            f'<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr><td align="center" style="font-size:10px;line-height:12px;mso-line-height-rule:exactly;font-weight:700;color:{MUT if futuro else INK};padding-bottom:3px;white-space:nowrap;{NUM}">{rotulo}</td></tr>'
            f'<tr><td align="center" valign="bottom">{barra(h, 11, cor)}</td></tr></table></td>'
            f'<td width="2" style="width:2px;font-size:1px;line-height:1px;">&nbsp;</td>'
            f'<td valign="bottom">{barra(h_ant, 4, GRY)}</td>'
            f'</tr></table></td>'
        )
        rotulos += (f'<td align="center" style="padding-top:8px;font-size:10px;border-top:1px solid {INK};'
                    f'color:{O if destaque else MUT};font-weight:{700 if destaque else 500};">{w}</td>')

    p_sem, p_l4, p_acc = pct(w_atual, w_ant), pct(l4_atual, l4_ant), pct(acc_atual, acc_ant)
    t = lambda y: fmt(totais.get(y))

    def leg(cor, largura, texto):
        return (f'<td valign="middle" style="padding-left:10px;"><table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>'
                f'<td width="{largura}" height="9" bgcolor="{cor}" style="width:{largura}px;height:9px;font-size:1px;line-height:1px;">&nbsp;</td></tr></table></td>'
                f'<td valign="middle" style="padding-left:4px;font-size:11px;color:{MUT};white-space:nowrap;">{texto}</td>')

    def var(p):
        return f'<span style="font-weight:700;color:{cor_var(p)};">{sinal(p)}</span>'

    html = f'''<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light"><meta name="supported-color-schemes" content="light"><link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet"><style>body,table,td,div,a{{font-family:{FONT};}}
@media only screen and (max-width:480px){{
.px{{padding-left:18px !important;padding-right:18px !important;}}
.hn,.ht{{display:block !important;width:auto !important;padding:0 !important;}}
.ht{{padding-top:10px !important;}}
.hn{{font-size:60px !important;line-height:62px !important;}}
}}</style></head>
<body style="margin:0;padding:0;background:{FUNDO};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="{FUNDO}" style="background:{FUNDO};"><tr><td align="center" style="padding:28px 14px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" bgcolor="#FFFFFF" style="max-width:600px;width:100%;background:#FFFFFF;border-radius:14px;color:{INK};font-family:{FONT};">
<tr><td class="px" style="padding:26px 30px 0;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
<td><img src="cid:logo-tfruits" width="96" height="33" alt="TFruits" style="display:block;border:0;"></td>
<td align="right" style="font-size:12px;color:{MUT};">Semana {W} &middot; {ano}</td></tr></table></td></tr>
<tr><td class="px" style="padding:30px 30px 0;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
<td class="hn" valign="bottom" width="170" style="padding-right:14px;font-size:68px;font-weight:800;letter-spacing:-0.04em;line-height:72px;mso-line-height-rule:exactly;color:{INK};{NUM}">{fmt(w_atual)}</td>
<td class="ht" valign="bottom" style="padding-bottom:6px;font-size:15px;line-height:1.45;color:{INK};">Containers de limão do Brasil que chegaram à Europa e ao Reino Unido na semana {W}.<br>{var(p_sem)} <span style="color:{MUT};">vs mesma semana de {ano - 1} ({fmt(w_ant)})</span></td></tr></table></td></tr>
<tr><td class="px" style="padding:26px 30px 0;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-top:1px solid {HAIR};border-bottom:1px solid {HAIR};"><tr>
<td width="50%" style="padding:14px 0;font-size:13px;color:{MUT};">Últimas 4 semanas ({ult4[-1][1]} a {W})<br><span style="font-size:20px;font-weight:700;color:{INK};{NUM}">{fmt(l4_atual)}</span> {var(p_l4)}<br><span style="font-size:12px;color:{MUT};{NUM}">{ano - 1}: {fmt(l4_ant)}</span></td>
<td width="50%" style="padding:14px 0 14px 18px;border-left:1px solid {HAIR};font-size:13px;color:{MUT};">{acc_label}<br><span style="font-size:20px;font-weight:700;color:{INK};{NUM}">{fmt(acc_atual)}</span> {var(p_acc)}<br><span style="font-size:12px;color:{MUT};{NUM}">{ano - 1}: {fmt(acc_ant)}</span></td></tr></table></td></tr>
<tr><td class="px" style="padding:24px 30px 0;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
<td style="font-size:14px;font-weight:700;color:{INK};">Chegadas por semana</td>
<td align="right"><table role="presentation" cellpadding="0" cellspacing="0" border="0" align="right"><tr>{leg(G, 9, ano)}{leg(GRY, 4, ano - 1)}{leg(GPREV, 9, "previsto")}</tr></table></td></tr></table></td></tr>
<tr><td class="px" style="padding:18px 30px 0;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="table-layout:fixed;"><tr>{colunas}</tr><tr>{rotulos}</tr></table></td></tr>
<tr><td class="px" style="padding:22px 30px 30px;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="{CAIXA}" style="background:{CAIXA};border-radius:10px;"><tr>
<td style="padding:14px 16px;font-size:12px;color:{MUT};">Total do ano</td>
<td align="right" style="padding:14px 8px;font-size:12px;color:{MUT};">{ano} <b style="font-size:15px;color:{INK};{NUM}">{t(ano)}</b></td>
<td align="right" style="padding:14px 8px;font-size:12px;color:{MUT};">{ano - 1} <b style="font-size:15px;color:{INK};{NUM}">{t(ano - 1)}</b></td>
<td align="right" style="padding:14px 16px 14px 8px;font-size:12px;color:{MUT};">{ano - 2} <b style="font-size:15px;color:{INK};{NUM}">{t(ano - 2)}</b></td></tr></table></td></tr>
</table></td></tr>
<tr><td align="center" style="padding:18px 30px 0;font-size:12px;line-height:1.6;color:{MUT};font-family:{FONT};">Semanas em curso e previstas ainda podem ser revisadas pela fonte.<br>Gerado pelo time de BI da TFruits<br><a href="{SITE_URL}" style="color:{G};font-weight:600;text-decoration:none;">tfruits.com.br</a></td></tr>
</table></td></tr></table></body></html>'''

    def txt_var(p):
        return "" if p is None else f" ({'+' if p >= 0 else '-'}{abs(p)}% vs {ano - 1})"

    texto = (
        f"Containers de limão do Brasil na Europa e Reino Unido | Semana {W}/{ano}\n\n"
        f"Semana {W}: {fmt(w_atual)}{txt_var(p_sem)}\n"
        f"Últimas 4 semanas ({ult4[-1][1]} a {W}): {fmt(l4_atual)}{txt_var(pct(l4_atual, l4_ant))}\n"
        f"{acc_label}: {fmt(acc_atual)}{txt_var(pct(acc_atual, acc_ant))}\n"
        f"Total do ano {ano}: {t(ano)} | {ano - 1}: {t(ano - 1)} | {ano - 2}: {t(ano - 2)}\n\n"
        f"Semanas em curso e previstas ainda podem ser revisadas pela fonte.\n"
        f"Gerado pelo time de BI da TFruits | {SITE_URL}\n"
    ).replace("&ndash;", "-")

    assunto = f"Containers Limão | Semana {W}/{ano}"
    return assunto, html, texto


def enviar(assunto, html, texto, agendar=None):
    to = [e.strip() for e in os.environ.get("EMAIL_TO", "").split(",") if e.strip()]
    payload = {
        "from": FROM_EMAIL,
        "to": to or TO_EMAILS,
        "subject": assunto,
        "html": html,
        "text": texto,
    }
    if not to:
        payload["cc"] = CC_EMAILS
    import base64
    with open(LOGO_ARQUIVO, "rb") as f:
        payload["attachments"] = [{
            "filename": "logo_tfruits.png",
            "content": base64.b64encode(f.read()).decode(),
            "content_type": "image/png",
            "content_id": "logo-tfruits",
        }]
    if agendar:
        payload["scheduled_at"] = agendar.isoformat()
    r = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {os.environ['RESEND_API_KEY_TFRUITS']}"},
        json=payload,
        timeout=30,
    )
    if r.status_code >= 300:
        sys.exit(f"Resend recusou o envio: {r.status_code} {r.text}")
    quando = f"agendado para {agendar.isoformat()}" if agendar else "enviado agora"
    print(f"{quando}: {payload['to']} (cc {payload.get('cc', [])}): {r.json().get('id')}")


if __name__ == "__main__":
    envio_em = os.environ.get("ENVIO_EM", "").strip()
    agendar = datetime.fromisoformat(envio_em) if envio_em else None
    hoje = agendar.astimezone(BRT) if agendar else datetime.now(BRT)
    semanal, totais = carregar()
    assunto, html, texto = montar(semanal, totais, hoje)
    print(assunto)
    print(texto)
    if DRY_RUN:
        with open("email_containers.html", "w", encoding="utf-8") as f:
            f.write(html.replace("cid:logo-tfruits", LOGO_URL))
        print("DRY_RUN: email_containers.html gerado, nada enviado.")
    else:
        enviar(assunto, html, texto, agendar)
