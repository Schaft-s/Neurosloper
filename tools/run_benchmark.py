"""Benchmark normal application entry points. No evaluator annotations enter search.

WildChat and actual user memory are never routed to OpenRouter by this command.
Each output is append-only and can resume. Start a different directory to change
configuration or source. Cached results belong to one explicit method and revision.
"""

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "po_delu"))
from engine import (
    analyze,
    init_job,
    rewrite,
    settings,
    verify_candidates,
    verify_replacements,
)
from formats import load
from spending import LARGE_PANEL, MODELS, BudgetTransport


def rows(p):
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def digest(x):
    return hashlib.sha256(x).hexdigest()


def code_hash():
    return digest(
        b"".join(x.read_bytes() for x in sorted((ROOT / "po_delu").glob("*.py")))
    )


class LocalTransport(httpx.AsyncBaseTransport):
    async def handle_async_request(self, req):
        if req.url.host not in {"127.0.0.1", "localhost"}:
            raise ValueError("Local benchmark requires loopback tunnel")
        body = json.loads(req.content)
        body.update(max_tokens=1800, chat_template_kwargs={"enable_thinking": False})
        async with httpx.AsyncClient(timeout=80, trust_env=False) as c:
            r = await c.post(
                str(req.url),
                json=body,
                headers={"Authorization": req.headers["Authorization"]},
            )
        return httpx.Response(r.status_code, content=r.content, request=req)


async def run(a):
    v = ROOT / "evaluation/v1"
    panel = LARGE_PANEL if a.panel == "large" else MODELS
    if a.suite == "lockbox":
        docs = rows(v.parent / "lockbox" / "inputs.jsonl")
        if a.mode == "fixed":
            locations = {
                x["id"]: x for x in rows(v.parent / "lockbox" / "targets.jsonl")
            }
            for doc in docs:
                t = locations[doc["id"]]
                doc["target"] = {
                    "block_id": "0",
                    "quote": t["quote"],
                    "start": t["start"],
                    "end": t["end"],
                }
    elif a.suite == "rewrite":
        docs = rows(v / "rewrite_inputs.jsonl")
    elif a.suite == "genre":
        original = rows(ROOT / "data/genre_blind.jsonl")
        excluded = {"report-27", "mail-05"}
        docs = [x for x in original if x["id"] not in excluded]
        if a.backend == "router":
            raise ValueError("Only synthetic inputs allowed for OpenRouter")
    elif a.suite == "external":
        if a.backend == "router":
            raise ValueError("External source excerpts use only the local endpoint")
        docs = rows(v / "external_inputs.jsonl")
    elif a.suite == "contrast":
        docs = rows(v / "contrast_inputs.jsonl")
    elif a.suite == "rewrite-lockbox":
        docs = rows(v.parent / "rewrite_lockbox" / "inputs.jsonl")
    elif a.suite == "style":
        docs = rows(v / "style_inputs.jsonl")
    else:
        docs = rows(v / "fidelity_inputs.jsonl")
    if a.suite == "contrast" and a.mode in ("fixed", "rewrite"):
        targets = {x["id"]: x["target"] for x in rows(v / "contrast_targets.jsonl")}
        for d in docs:
            d["target"] = targets[d["id"]]
    if a.backend == "router":
        os.environ.update(
            LLM_MODEL=panel[0],
            LLM_BASE_URL="https://openrouter.ai/api/v1",
            EDITOR_JUDGES_JSON=json.dumps([{"model": x} for x in panel]),
            EDITOR_FIDELITY_MODEL=panel[2],
        )
        if not os.environ.get("LLM_API_KEY"):
            raise ValueError("LLM_API_KEY required")
    elif a.backend == "local":
        if not os.getenv("LLM_API_KEY") or not os.getenv("LLM_BASE_URL", "").startswith(
            "http://127.0.0.1:"
        ):
            raise ValueError("Configure the approved loopback model")
        os.environ["EDITOR_JUDGES_JSON"] = "[]"
    elif a.mode != "offline":
        raise ValueError("Offline backend requires offline detector")
    if a.mode == "skill":
        if a.backend != "local":
            raise ValueError("Private skill runs on loopback only")
        import consensus

        consensus.SEMANTIC = (ROOT / "SKILL.md").read_text()
    folder = v / "runs" / a.run
    folder.mkdir(parents=True, exist_ok=True)
    if a.id:
        choice=set(a.id); docs=[d for d in docs if d["id"] in choice]
        if len(docs)!=len(choice):raise ValueError("Unknown document id")
    pick = docs[: a.limit] if a.limit else docs
    config = {
        "backend": a.backend,
        "suite": a.suite,
        "mode": a.mode,
        "model": None if a.backend == "offline" else os.getenv("LLM_MODEL"),
        "panel": panel if a.backend == "router" else [],
        "ids": [r["id"] for r in pick],
        "input_hashes": [digest(r["text"].encode()) for r in pick],
        "request_hashes": [digest(r["request"].encode()) for r in pick],
        "rewriter": os.getenv("EDITOR_REWRITER", "standard"),
        "protocol": os.getenv("EDITOR_SEARCH_PROTOCOL", "json"),
        "rewrite_consensus": os.getenv("EDITOR_FIDELITY_CONSENSUS", "0"),
        "detector_variant": os.getenv("EDITOR_DETECTOR_VARIANT", "blinded"),
        "verification": os.getenv("EDITOR_VERIFICATION", "legacy"),
        "code_hash": code_hash(),
        "revision": a.run,
    }
    meta = folder / "config.json"
    if meta.exists():
        if json.loads(meta.read_text()) != json.loads(json.dumps(config)):
            raise ValueError("Run configuration changed; choose a new run id")
    else:
        meta.write_text(json.dumps(config, ensure_ascii=False, indent=2))
    out = folder / "predictions.jsonl"
    done = {r["id"] for r in rows(out)} if out.exists() else set()
    gate = asyncio.Semaphore(a.parallel)

    async def task(d):
        if d["id"] in done:
            return
        async with gate:
            transport = (
                BudgetTransport(v / "runs" / "budget.sqlite", a.run + "/" + d["id"])
                if a.backend == "router"
                else LocalTransport()
                if a.backend == "local"
                else None
            )
            name = "document.txt"
            data = d["text"].encode()
            start = time.monotonic()
            try:
                if a.mode == "fixed":
                    fixed = [{"id": "location", **d["target"]}]
                    j = await verify_candidates(
                        name, data, fixed, context=d["request"], transport=transport
                    )
                elif a.mode == "fidelity":
                    j = await verify_replacements(
                        name,
                        data,
                        d["candidates"],
                        terms=d["locked"],
                        context=d["request"],
                        transport=transport,
                    )
                elif a.mode == "rewrite":
                    # A user-selected region; references and expected rewrites are not provided.
                    _, blocks = load(name, data)
                    target = d["target"]
                    x = blocks[int(target["block_id"])]["text"]
                    q = target["quote"]
                    offset = x.index(q)
                    j = init_job(
                        name,
                        data,
                        d["request"],
                        "",
                        "manual-selection",
                        settings(
                            d.get("style", "voice"),
                            d.get("length", "free"),
                            "minimal",
                            d.get("locked", []),
                        ),
                    )
                    j.update(
                        seen=len(blocks),
                        findings=[
                            {
                                "id": "0",
                                "decision": "flag",
                                "block_id": target["block_id"],
                                "start": offset,
                                "end": offset + len(q),
                                "quote": q,
                                "revision_hash": digest(data),
                                "effect": "Автор выбрал этот участок.",
                                "pattern": "Ручной выбор",
                                "normal": "",
                            }
                        ],
                    )
                    j = await rewrite(j, data, ["0"], transport)
                else:
                    j = await analyze(
                        name,
                        data,
                        context=d["request"],
                        transport=transport,
                        detector="semantic" if a.mode == "skill" else a.mode,
                    )
                result = {
                    k: j[k]
                    for k in (
                        "revision_hash",
                        "stages",
                        "errors",
                        "consensus",
                        "candidates",
                        "verdicts",
                        "findings",
                        "coverage",
                        "seen",
                        "total",
                        "changes",
                        "refusals",
                        "results",
                        "backend",
                    )
                    if k in j
                }
                if a.mode not in ("fixed", "fidelity") and "blocks" in j:
                    cursor = 0
                    offsets = {}
                    for b in j["blocks"]:
                        pos = d["text"].find(b["text"], cursor)
                        if pos < 0:
                            raise ValueError("Block offset cannot be reconstructed")
                        offsets[b["id"]] = pos
                        cursor = pos + len(b["text"])
                    result["block_offsets"] = offsets
            except Exception as e:
                result = {"failure": type(e).__name__ + ": " + str(e)[:160]}
            result.update(
                id=d["id"],
                input_sha256=digest(data),
                elapsed=round(time.monotonic() - start, 3),
            )
            with out.open("a") as f:
                f.write(json.dumps(result, ensure_ascii=False) + "\n")

    await asyncio.gather(*(task(d) for d in pick))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("run")
    p.add_argument(
        "suite",
        choices=[
            "genre",
            "contrast",
            "fidelity",
            "style",
            "external",
            "rewrite",
            "lockbox",
            "rewrite-lockbox",
        ],
    )
    p.add_argument("backend", choices=["router", "local", "offline"])
    p.add_argument(
        "mode",
        choices=[
            "offline",
            "baseline",
            "semantic",
            "slopotron",
            "union",
            "fixed",
            "fidelity",
            "rewrite",
            "skill",
        ],
    )
    p.add_argument("--panel", choices=["base", "large"], default="base")
    p.add_argument("--id",action="append",help="Only selected input ID (repeatable)")
    p.add_argument("--limit", type=int)
    p.add_argument("--parallel", type=int, default=3)
    asyncio.run(run(p.parse_args()))
