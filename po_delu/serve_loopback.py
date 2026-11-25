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
    os.environ.update(
        LLM_BASE_URL="https://openrouter.ai/api/v1",
        LLM_MODEL=MODELS[0],
        LLM_API_KEY=key,
        EDITOR_JUDGES_JSON=json.dumps([{"model": m} for m in MODELS]),
        EDITOR_DETECTOR="union",
        EDITOR_FIDELITY_MODEL=MODELS[2],
        EDITOR_FIDELITY_CONSENSUS="1",
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
