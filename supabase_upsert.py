"""
supabase_upsert.py — Helper de upsert para o Supabase
======================================================
Usado por todos os scripts ETL para enviar dados ao Supabase
via REST API (sem SDK, só requests).

Configuração:
    Defina as variáveis de ambiente antes de rodar:
        set SUPABASE_URL=https://xxxxxxxxxxx.supabase.co
        set SUPABASE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...

    Ou crie um arquivo .env na mesma pasta:
        SUPABASE_URL=https://xxxxxxxxxxx.supabase.co
        SUPABASE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
"""

import os
import json
import requests
from pathlib import Path

# ── CARREGAR CONFIG ────────────────────────────────────────────────────────────
def _load_env():
    """Carrega .env local se existir (sem depender de python-dotenv)."""
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ[k.strip()] = v.strip()  # força sobrescrita

_load_env()

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")  # service_role key


def _base_headers(prefer: str = "resolution=merge-duplicates") -> dict:
    return {
        "apikey":        SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type":  "application/json",
        "Prefer":        prefer,
    }


def _safe_batch(batch: list[dict]) -> list[dict]:
    """Serializa valores não-JSON-nativos (datetime, NaN)."""
    result = []
    for row in batch:
        safe_row = {}
        for k, v in row.items():
            if hasattr(v, "isoformat"):
                safe_row[k] = v.isoformat()
            elif v != v:  # NaN
                safe_row[k] = None
            else:
                safe_row[k] = v
        result.append(safe_row)
    return result


# ── UPSERT ─────────────────────────────────────────────────────────────────────
def upsert(table: str, records: list[dict], batch_size: int = 500,
           on_conflict: str | None = None) -> dict:
    """
    Faz upsert de registros no Supabase (INSERT ... ON CONFLICT DO UPDATE).
    Depende da constraint UNIQUE definida no schema.

    Args:
        table:       nome da tabela (ex: "brasil_precos")
        records:     lista de dicts com os dados
        batch_size:  registros por request (padrão 500)
        on_conflict: colunas de conflito separadas por vírgula (ex: "semana,ano,regiao")
                     Se None, usa a constraint UNIQUE padrão da tabela.

    Returns:
        {"inserted": N, "errors": [...]}
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise EnvironmentError(
            "SUPABASE_URL e SUPABASE_KEY não configurados.\n"
            "Crie um arquivo .env na pasta do projeto com:\n"
            "  SUPABASE_URL=https://xxxx.supabase.co\n"
            "  SUPABASE_KEY=eyJ..."
        )

    base_url = f"{SUPABASE_URL}/rest/v1/{table}"
    params   = {"on_conflict": on_conflict} if on_conflict else {}
    headers  = _base_headers("resolution=merge-duplicates")
    total    = 0
    errors   = []

    for i in range(0, len(records), batch_size):
        batch = _safe_batch(records[i : i + batch_size])
        r = requests.post(base_url, headers=headers, params=params,
                          data=json.dumps(batch), timeout=30)
        if r.status_code in (200, 201):
            total += len(batch)
        else:
            errors.append({
                "batch_start": i,
                "status":      r.status_code,
                "detail":      r.text[:300],
            })

    return {"inserted": total, "errors": errors}


def insert(table: str, records: list[dict], batch_size: int = 500) -> dict:
    """
    Insert simples (sem upsert) — para tabelas sem UNIQUE constraint.
    Usa Prefer: return=minimal para não retornar dados.
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise EnvironmentError("SUPABASE_URL e SUPABASE_KEY não configurados.")

    url     = f"{SUPABASE_URL}/rest/v1/{table}"
    headers = _base_headers("return=minimal")
    total   = 0
    errors  = []

    for i in range(0, len(records), batch_size):
        batch = _safe_batch(records[i : i + batch_size])
        r = requests.post(url, headers=headers, data=json.dumps(batch), timeout=30)
        if r.status_code in (200, 201):
            total += len(batch)
        else:
            errors.append({"batch_start": i, "status": r.status_code, "detail": r.text[:300]})

    return {"inserted": total, "errors": errors}


def delete_old(table: str, column: str, keep_latest_n_days: int = 30):
    """
    Remove registros mais antigos que N dias (útil para clima/forecast).
    Apenas para tabelas sem UNIQUE por data (clima_brasil_atual, clima_forecast).
    """
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    headers = {
        "apikey":        SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type":  "application/json",
    }
    cutoff = f"now() - interval '{keep_latest_n_days} days'"
    params = {column: f"lt.{cutoff}"}
    r = requests.delete(url, headers=headers, params=params, timeout=30)
    return r.status_code


# ── TESTE RÁPIDO ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not SUPABASE_URL:
        print("⚠️  Configure SUPABASE_URL e SUPABASE_KEY no arquivo .env")
    else:
        print(f"✅ Supabase configurado: {SUPABASE_URL}")
        print(f"   Key (primeiros 20 chars): {SUPABASE_KEY[:20]}...")
        # Testar conexão lendo a view de status
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/v_ultima_atualizacao",
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
            },
            timeout=10,
        )
        if r.status_code == 200:
            print("✅ Conexão OK. Status das tabelas:")
            for row in r.json():
                print(f"   {row['tabela']:30s} | {row['total_registros']:6} registros | {row['ultima_atualizacao']}")
        else:
            print(f"❌ Erro: {r.status_code} — {r.text[:200]}")
