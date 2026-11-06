"""
E-mail semanal de Containers (BI Limão / TFruits)

Chegadas de containers de limão do Brasil na Europa e UK, semana fechada vs
mesma semana do ano anterior. Substitui a página "Containers Limão" do Power BI
(que era só um print estático da fonte).

Layout v3 escuro (aprovado por Felipe em 06/11/2026), mesmo padrão de cores do
bilimao. HTML em tabela com estilos inline (Outlook não entende flex).

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

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
DRY_RUN = os.environ.get("DRY_RUN") == "1"

BRT = timezone(timedelta(hours=-3))
SEMANAS_ANTES = 7     # semanas fechadas exibidas antes da última fechada
SEMANAS_DEPOIS = 3    # em curso + previstas

# Paleta do bilimao (index.html)
BG, SF, TX, MU, HL = "#090C09", "#121712", "#F0F4F0", "#8A9A8A", "#1F261F"
G, GL, O, G25 = "#4CAE4F", "#24402A", "#F15A22", "#3A443A"
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
    acc_label = f"Acum. WK {sem_acum[0]} a {W}" if sem_acum else "Acumulado"

    mx = max([val(a, w) or 0 for a, w in janela] + [val(a - 1, w) or 0 for a, w in janela]) or 1

    def barra(v, cor, h):
        pc = max(1, round((v or 0) / mx * 100))
        return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
                f'<td width="{pc}%" bgcolor="{cor}" style="height:{h}px;line-height:{h}px;font-size:1px;border-radius:3px;">&nbsp;</td>'
                f'<td style="font-size:1px;line-height:{h}px;">&nbsp;</td></tr></table>')

    linhas = ""
    for a, w in janela:
        futuro = (a, w) > fechada
        destaque = (a, w) == fechada
        if (a, w) == em_curso:
            nota = f'<span style="font-size:11px;color:{O};"> em curso</span>'
        elif futuro:
            nota = f'<span style="font-size:11px;color:{MU};"> prev.</span>'
        else:
            nota = ""
        cor = O if destaque else (GL if futuro else G)
        txt = MU if futuro else TX
        peso = 700 if destaque else 500
        borda = f"border-top:1px solid {HL};"
        linhas += (
            f'<tr><td width="92" style="padding:8px 0;{borda}font-size:13px;color:{txt};font-weight:{peso};">WK {w}{nota}</td>'
            f'<td style="padding:8px 14px 8px 0;{borda}">{barra(val(a, w), cor, 8)}'
            f'<div style="height:4px;line-height:4px;font-size:1px;">&nbsp;</div>{barra(val(a - 1, w), G25, 3)}</td>'
            f'<td width="52" align="right" style="padding:8px 0;{borda}font-size:14px;font-weight:{700 if destaque else 600};color:{txt};{NUM}">{fmt(val(a, w))}</td>'
            f'<td width="52" align="right" style="padding:8px 0;{borda}font-size:13px;color:{MU};{NUM}">{fmt(val(a - 1, w))}</td>'
            f'<td width="52" align="right" style="padding:8px 0;{borda}font-size:13px;color:{MU};{NUM}">{fmt(val(a - 2, w))}</td></tr>'
        )

    def kpi(rot, v, comp, p):
        return (
            f'<td width="33%" valign="top" style="padding:0 5px;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
            f'<td bgcolor="{SF}" style="padding:16px;border-radius:10px;">'
            f'<div style="font-size:12px;color:{MU};">{rot}</div>'
            f'<div style="font-size:26px;font-weight:700;color:{TX};letter-spacing:-0.02em;padding-top:6px;{NUM}">{fmt(v)}</div>'
            f'<div style="font-size:12px;color:{MU};padding-top:2px;{NUM}"><span style="color:{cor_var(p)};font-weight:700;">{sinal(p)}</span> vs {fmt(comp)}</div>'
            f'</td></tr></table></td>'
        )

    p_sem = pct(w_atual, w_ant)
    if p_sem is None:
        manchete = f"Semana {W} fechou com {fmt(w_atual)} containers."
    else:
        rel = "acima" if p_sem >= 0 else "abaixo"
        manchete = (f'Semana {W} fechou com {fmt(w_atual)} containers, '
                    f'<span style="color:{cor_var(p_sem)};">{abs(p_sem)}% {rel}</span> de {ano - 1}.')

    t = lambda y: fmt(totais.get(y))
    rodape_borda = f"border-top:1px solid {MU};"

    html = f'''<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="dark"><meta name="supported-color-schemes" content="dark"><link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap" rel="stylesheet"><style>body,table,td,div,a{{font-family:{FONT};}}</style></head>
<body style="margin:0;padding:0;background:{BG};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="{BG}" style="background:{BG};"><tr><td align="center" style="padding:28px 14px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;color:{TX};font-family:{FONT};">
<tr><td style="padding:0 5px 26px;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
<td><img src="{LOGO_URL}" width="100" alt="TFruits" style="display:block;border:0;"></td>
<td align="right" style="font-size:12px;color:{MU};">BI Limão &middot; Semana {W}</td></tr></table></td></tr>
<tr><td style="padding:0 5px 4px;font-size:12px;font-weight:600;color:{G};">Containers Brasil para Europa e UK</td></tr>
<tr><td style="padding:0 5px 24px;font-size:24px;line-height:1.3;font-weight:700;letter-spacing:-0.02em;color:{TX};">{manchete}</td></tr>
<tr><td style="padding-bottom:10px;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
{kpi(f"Semana {W}", w_atual, w_ant, p_sem)}{kpi(f"WK {ult4[-1][1]} a {W}", l4_atual, l4_ant, pct(l4_atual, l4_ant))}{kpi(acc_label, acc_atual, acc_ant, pct(acc_atual, acc_ant))}
</tr></table></td></tr>
<tr><td style="padding:0 5px;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td bgcolor="{SF}" style="padding:20px 20px 16px;border-radius:10px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0">
<tr><td colspan="2" style="padding-bottom:14px;font-size:14px;font-weight:600;color:{TX};">Chegadas por semana</td>
<td width="52" align="right" style="padding-bottom:14px;font-size:11px;color:{G};font-weight:700;">{ano}</td>
<td width="52" align="right" style="padding-bottom:14px;font-size:11px;color:{MU};">{ano - 1}</td>
<td width="52" align="right" style="padding-bottom:14px;font-size:11px;color:{MU};">{ano - 2}</td></tr>
{linhas}
<tr><td colspan="2" style="padding:12px 0 0;{rodape_borda}font-size:13px;font-weight:600;color:{TX};">Total do ano</td>
<td align="right" style="padding:12px 0 0;{rodape_borda}font-size:14px;font-weight:700;color:{TX};{NUM}">{t(ano)}</td>
<td align="right" style="padding:12px 0 0;{rodape_borda}font-size:13px;color:{MU};{NUM}">{t(ano - 1)}</td>
<td align="right" style="padding:12px 0 0;{rodape_borda}font-size:13px;color:{MU};{NUM}">{t(ano - 2)}</td></tr>
</table></td></tr></table></td></tr>
</table></td></tr></table></body></html>'''

    def txt_var(p):
        return "" if p is None else f" ({'+' if p >= 0 else '-'}{abs(p)}% vs {ano - 1})"

    texto = (
        f"Containers Brasil para Europa e UK | Semana {W}/{ano}\n\n"
        f"Semana {W}: {fmt(w_atual)}{txt_var(p_sem)}\n"
        f"WK {ult4[-1][1]} a {W}: {fmt(l4_atual)}{txt_var(pct(l4_atual, l4_ant))}\n"
        f"{acc_label}: {fmt(acc_atual)}{txt_var(pct(acc_atual, acc_ant))}\n"
        f"Total do ano {ano}: {t(ano)} | {ano - 1}: {t(ano - 1)} | {ano - 2}: {t(ano - 2)}\n"
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
            f.write(html)
        print("DRY_RUN: email_containers.html gerado, nada enviado.")
    else:
        enviar(assunto, html, texto, agendar)
