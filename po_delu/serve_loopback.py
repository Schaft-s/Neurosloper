"""Start the budgeted review service with an OpenRouter key mounted as a secret."""

import json
import os
from pathlib import Path

import uvicorn
from spending import LARGE_PANEL as MODELS

if __name__ == "__main__":
    key = (
        Path(os.getenv("PO_DELU_KEY_FILE", "/run/secrets/openrouter"))
        .read_text()
        .strip()
    )
    if not key:
        raise SystemExit("Secret file is empty")
    gpt = "openai/gpt-5.4-mini"
    sonnet = "anthropic/claude-sonnet-4.6"
    qwen, gemma, deepseek = MODELS
    os.environ.update(
        LLM_BASE_URL="https://openrouter.ai/api/v1",
        LLM_MODEL=qwen,
        LLM_API_KEY=key,
        EDITOR_DETECTOR="union",
        EDITOR_SEARCH_RUBRIC="reader_task",
        EDITOR_SEARCH_MODEL=sonnet,
        EDITOR_SLOPOTRON_MODEL=qwen,
        EDITOR_JUDGES_JSON=json.dumps([{"model": m} for m in (gpt, gemma, qwen)]),
        EDITOR_VERIFICATION="reader_committee",
        EDITOR_ALL_REVIEWERS="1",
        EDITOR_FINDINGS_POLICY="corroborated",
        EDITOR_BRIEF_AUDIT="0",
        EDITOR_PARALLEL_VOTES="1",
        EDITOR_API_JSON="1",
        EDITOR_REWRITER="minimal_edit",
        EDITOR_REWRITER_MODEL=qwen,
        EDITOR_FIDELITY_MODEL=deepseek,
        EDITOR_FIDELITY_CONSENSUS="1",
        EDITOR_FIDELITY_JUDGES_JSON=json.dumps([{"model":m} for m in (sonnet, qwen, deepseek)]),
        EDITOR_FIDELITY_POLICY="relations_v4",
        PO_DELU_REQUIRE_MODEL_CONSENT="1",
    )
    if not os.getenv("PO_DELU_BUDGET_DB"):
        raise SystemExit("Specify PO_DELU_BUDGET_DB for a persistent spending ledger")
    uvicorn.run(
        "web:app",
        host=os.getenv("PO_DELU_BIND", "127.0.0.1"),
        port=int(os.getenv("PO_DELU_PORT", "18476")),
        access_log=False,
    )
