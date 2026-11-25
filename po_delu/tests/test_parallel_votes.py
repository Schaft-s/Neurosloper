"""Compare the same panel with sequential and bounded concurrent delivery."""

import asyncio
import json
import os
import time
import unittest
from unittest.mock import patch

import httpx

from engine import verify_candidates, verify_replacements


PANEL = ("qwen/qwen3-235b-a22b-2507", "google/gemma-4-31b-it", "deepseek/deepseek-v3.2")
TEXT = "Архив открывают завтра."
BRIEF = "Сводка для редактора."


async def trial(task, parallel, fail=False, delays=(0, 0, 0), force_order=True):
    active = peak = 0
    events = []
    release_first = asyncio.Event()

    async def handle(request):
        nonlocal active, peak
        packet = json.loads(request.content)
        index = PANEL.index(packet["model"])
        body = json.loads(packet["messages"][1]["content"])
        active += 1
        peak = max(peak, active)
        events.append(("start", index))
        if force_order and parallel and index == 0:
            await release_first.wait()
        await asyncio.sleep(delays[index])
        events.append(("end", index))
        active -= 1
        if index == (1 if task == "detection" else 2):
            release_first.set()
        if fail and index == 1:
            return httpx.Response(503)
        if task == "detection":
            out = {"votes": [
                dict(
                    id=c["id"], role="authored", link="adds" if c["id"] == "a" or index == 1 else "serves",
                    use="форма полезна", risk="готовая формула", why=f"решение {index}",
                    fact=["0", "Архив"], task=["brief", BRIEF],
                )
                for c in body["candidates"]
            ]}
        else:
            out = dict(
                status="pass", forward="yes", reverse="yes", reason=f"решение {index}",
                counterexample="", source_conflicts=[],
                relations=[{"s": ["s1"], "t": ["t1"], "fact": TEXT}], edits=[],
            )
        return httpx.Response(200, json={
            "model": PANEL[index],
            "choices": [{"message": {"content": json.dumps(out)}}],
        })

    env = {
        "EDITOR_PARALLEL_VOTES": str(int(parallel)),
        "EDITOR_VERIFICATION": "evidence_arbiter_v2",
        "EDITOR_FIDELITY_POLICY": "relations_v2",
        "EDITOR_JUDGES_JSON": json.dumps([{"model": m} for m in PANEL]),
        "LLM_MODEL": PANEL[0], "LLM_API_KEY": "mock", "PO_DELU_BUDGET_DB": "",
    }
    with patch.dict(os.environ, env):
        transport = httpx.MockTransport(handle)
        start = time.monotonic()
        if task == "detection":
            raw = await verify_candidates(
                "x.txt", TEXT.encode(),
                [{"id": "a", "block_id": "0", "quote": "Архив"},
                 {"id": "b", "block_id": "0", "quote": "завтра"}],
                context=BRIEF, transport=transport,
            )
            decisions = {k: raw[k] for k in ("findings", "verdicts", "consensus", "errors")}
        else:
            raw = await verify_replacements(
                "x.txt", TEXT.encode(), [{"id": "x", "block_id": "0", "after": TEXT}],
                context=BRIEF, transport=transport,
            )
            decisions = raw["results"]
        elapsed = time.monotonic() - start
    stages = [(s["stage"], s["model"], s["region"], s["input_hash"], s["status"], s.get("error"))
              for s in raw["stages"]]
    return decisions, sorted(stages), events, peak, elapsed


class ParallelVotes(unittest.IsolatedAsyncioTestCase):
    async def test_stable_output_and_arbiter_barrier(self):
        for fail in (False, True):
            with self.subTest(fail=fail):
                sequential = await trial("detection", False, fail)
                result, stages, events, peak, _ = await asyncio.wait_for(trial("detection", True, fail), 2)
                self.assertEqual((result, stages), sequential[:2])
                self.assertEqual((sequential[3], peak), (1, 2))
                self.assertEqual([s[2] for s in stages if s[0] == "arbiter"], [["a", "b"] if fail else ["b"]])
                self.assertLess(events.index(("end", 1)), events.index(("end", 0)))
                self.assertLess(events.index(("end", 0)), events.index(("start", 2)))
                self.assertEqual([v["id"] for v in result["findings"]], ["a"])
                self.assertTrue(all(v["quorum_met"] for v in result["verdicts"]))

    async def test_fidelity_limit_family_order_and_representative(self):
        for fail in (False, True):
            with self.subTest(fail=fail):
                sequential = await trial("fidelity", False, fail)
                result, stages, events, peak, _ = await asyncio.wait_for(trial("fidelity", True, fail), 2)
                self.assertEqual((result, stages), sequential[:2])
                self.assertEqual((sequential[3], peak), (1, 2))
                self.assertEqual([i for event, i in events if event == "end"], [1, 2, 0])
                decision = result[0]
                self.assertEqual([v["model"] for v in decision["votes"]], list(PANEL))
                self.assertEqual((decision["status"], decision["reason"]), ("pass", "решение 0"))
                self.assertEqual(decision["votes"][1]["status"], "unavailable" if fail else "pass")
