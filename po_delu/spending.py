"""Budgeted OpenRouter calls for an explicitly enabled local installation or benchmark.

SQLite transactions reserve an upper bound before leaving the host. Unknown charges
retain their reservation across retries and restarts. The ledger stores no credentials.
"""

import json
import sqlite3
import time
from pathlib import Path

import httpx

MODELS = (
    "qwen/qwen3-30b-a3b-instruct-2507",
    "google/gemma-3-27b-it",
    "deepseek/deepseek-v3.2",
)
LARGE_PANEL = (
    "qwen/qwen3-235b-a22b-2507",
    "google/gemma-4-31b-it",
    "deepseek/deepseek-v3.2",
)
# These per-model route ceilings apply only when that exact optional model is selected.
EXPERIMENTAL_RATES = {
    "anthropic/claude-sonnet-4.6": (3.1, 15.5),
    "openai/gpt-5.4-mini": (0.8, 4.7),
}
CEILING = 9.90
MAX_INPUT_RATE, MAX_OUTPUT_RATE = 0.6, 1.5  # USD per million tokens
ORIGIN = "https://openrouter.ai/api/v1/chat/completions"


def ledger_balance(connection):
    # Settlements never override a positive reported charge or an unresolved timeout.
    return connection.execute("""
        SELECT coalesce(sum(coalesce(c.actual,
            CASE WHEN r.call_id IS NOT NULL THEN 0 ELSE c.reserve END)), 0)
        FROM calls c LEFT JOIN routing_releases r ON r.call_id = c.id
    """).fetchone()[0]


class BudgetTransport(httpx.AsyncBaseTransport):
    def __init__(self, ledger, tag):
        self.ledger = Path(ledger).resolve()
        self.tag = tag
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.ledger) as con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS calls (id INTEGER PRIMARY KEY, tag TEXT, model TEXT, started REAL, reserve REAL, actual REAL, status TEXT, prompt_tokens INTEGER, completion_tokens INTEGER, generation_id TEXT)"
            )

            con.execute("CREATE TABLE IF NOT EXISTS routing_releases(call_id INTEGER PRIMARY KEY, basis TEXT NOT NULL)")

    async def handle_async_request(self, request):
        if str(request.url) != ORIGIN:
            raise ValueError("Unexpected upstream")
        body = json.loads(request.content)
        model = body.get("model")
        if (
            model not in set(MODELS + LARGE_PANEL) | EXPERIMENTAL_RATES.keys()
            or body.get("stream")
            or "plugins" in body
        ):
            raise ValueError("Model or route outside the configured budget policy")
        body["max_tokens"] = min(int(body.get("max_tokens", 1800)), 1800)
        # A UTF-8 byte per token plus generous chat/tool overhead. The provider
        # rejects routes exceeding these per-million-token rates.
        prompt_rate, completion_rate = EXPERIMENTAL_RATES.get(model, (MAX_INPUT_RATE, MAX_OUTPUT_RATE))
        input_limit = len(json.dumps(body, ensure_ascii=False).encode()) + 4096
        reserve = (
            input_limit * prompt_rate + body["max_tokens"] * completion_rate
        ) / 1_000_000
        body["provider"] = {
            "max_price": {"prompt": prompt_rate, "completion": completion_rate},
            "require_parameters": True,
        }
        if "tools" not in body and model.startswith(("deepseek/", "google/gemma-4")):
            body["reasoning"] = {"enabled": False}
        body["usage"] = {"include": True}
        with sqlite3.connect(self.ledger, timeout=45) as con:
            con.execute("BEGIN IMMEDIATE")
            spent = ledger_balance(con)
            if spent + reserve > CEILING:
                raise ValueError("Project spending limit reached")
            row = con.execute(
                "INSERT INTO calls(tag,model,started,reserve,status) VALUES(?,?,?,?,?)",
                (self.tag, model, time.time(), reserve, "reserved"),
            ).lastrowid
        async with httpx.AsyncClient(timeout=65) as client:
            res = await client.post(
                ORIGIN,
                json=body,
                headers={"Authorization": request.headers["Authorization"]},
            )
        try:
            payload = res.json()
            usage = payload.get("usage") or {}
            cost = usage.get("cost")
            if not isinstance(cost, (float, int)) or cost < 0:
                cost = None
            with sqlite3.connect(self.ledger, timeout=45) as con:
                con.execute(
                    "UPDATE calls SET actual=?,status=?,prompt_tokens=?,completion_tokens=?,generation_id=? WHERE id=?",
                    (
                        cost,
                        str(res.status_code),
                        usage.get("prompt_tokens"),
                        usage.get("completion_tokens"),
                        payload.get("id"),
                        row,
                    ),
                )
                # Only known pre-routing failures: no generation, tokens or auxiliary tools.
                error = payload.get("error")
                if (res.status_code == 404 and not payload.get("id") and not usage
                        and isinstance(error, dict) and "endpoint" in str(error.get("message", "")).lower()):
                    con.execute("INSERT OR IGNORE INTO routing_releases VALUES(?,?)",
                                (row, "openrouter_no_endpoint; https://openrouter.ai/pricing"))
        except (ValueError, TypeError, AttributeError):
            pass
        return httpx.Response(
            res.status_code,
            content=res.content,
            headers={"content-type": "application/json"},
            request=request,
        )
