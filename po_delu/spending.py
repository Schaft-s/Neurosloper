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
CEILING = 9.50
MAX_INPUT_RATE, MAX_OUTPUT_RATE = 0.6, 1.5  # USD per million tokens
ORIGIN = "https://openrouter.ai/api/v1/chat/completions"


class BudgetTransport(httpx.AsyncBaseTransport):
    def __init__(self, ledger, tag):
        self.ledger = Path(ledger).resolve()
        self.tag = tag
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.ledger) as con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS calls (id INTEGER PRIMARY KEY, tag TEXT, model TEXT, started REAL, reserve REAL, actual REAL, status TEXT, prompt_tokens INTEGER, completion_tokens INTEGER, generation_id TEXT)"
            )

    async def handle_async_request(self, request):
        if str(request.url) != ORIGIN:
            raise ValueError("Unexpected upstream")
        body = json.loads(request.content)
        model = body.get("model")
        if (
            model not in set(MODELS + LARGE_PANEL)
            or body.get("stream")
            or "plugins" in body
        ):
            raise ValueError("Model or route outside the configured budget policy")
        body["max_tokens"] = min(int(body.get("max_tokens", 1800)), 1800)
        # A UTF-8 byte per token plus generous chat/tool overhead. The provider
        # rejects routes exceeding these per-million-token rates.
        input_limit = len(json.dumps(body, ensure_ascii=False).encode()) + 4096
        reserve = (
            input_limit * MAX_INPUT_RATE + body["max_tokens"] * MAX_OUTPUT_RATE
        ) / 1_000_000
        body["provider"] = {
            "max_price": {"prompt": MAX_INPUT_RATE, "completion": MAX_OUTPUT_RATE},
            "require_parameters": True,
        }
        if "tools" not in body and model.startswith(("deepseek/", "google/gemma-4")):
            body["reasoning"] = {"enabled": False}
        body["usage"] = {"include": True}
        with sqlite3.connect(self.ledger, timeout=45) as con:
            con.execute("BEGIN IMMEDIATE")
            spent = con.execute(
                "SELECT coalesce(sum(coalesce(actual,reserve)),0) FROM calls"
            ).fetchone()[0]
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
        except (ValueError, TypeError, AttributeError):
            pass
        return httpx.Response(
            res.status_code,
            content=res.content,
            headers={"content-type": "application/json"},
            request=request,
        )
