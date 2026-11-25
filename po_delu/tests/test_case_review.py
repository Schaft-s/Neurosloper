import asyncio
import json
import os
import unittest
from unittest.mock import patch

import httpx
from engine import verify_candidates


class CaseFiles(unittest.TestCase):
    panel = ("qwen/qwen3-235b-a22b-2507", "google/gemma-4-31b-it", "deepseek/deepseek-v3.2")
    builder = "anthropic/claude-sonnet-4.6"

    def run_case(self, long=False, failed=False, wrong_anchor=False):
        brief = "Составитель дословно приводит надпись. Куратор проверяет этикетку."
        middle = " Бумага оставалась в фонде." * 650 if long else ""
        text = f"Прикрепили этикетку.{middle} Открыто для чтения. Верно сверено."
        quotes = ("Прикрепили этикетку", "Открыто для чтения", "Верно сверено")
        calls = []

        def handle(request):
            packet = json.loads(request.content)
            model = packet["model"]
            data = json.loads(packet["messages"][-1]["content"])
            candidates = data["candidates"]
            stage_type = "review" if "cases" in data else "builder"
            calls.append((stage_type, model, data))
            refs = data["blocks"][0]["spans"]
            if stage_type == "builder":
                self.assertEqual(model, self.builder)
                if failed:
                    return httpx.Response(503)
                out = {"cases": []}
                for candidate in candidates:
                    ident = candidate["id"]
                    visible_ref = next(ref for ref, value in refs.items() if candidate["quote"] in value)
                    out["cases"].append(dict(
                        id=ident,
                        harm=["Вопреки исходнику читателю поручают уничтожить этикетку.", visible_ref],
                        use=["Название передано для сверки.", "brief.s0"],
                        test=["Куратору требуется сверить название.", "brief.s1"],
                    ))
                    if wrong_anchor and ident == "b":
                        # Visible for the other location in this batch, never for b.
                        self.assertIn("b0.s2", refs)
                        self.assertNotIn("b0.s2", candidate["allowed_evidence_ids"])
                        out["cases"][-1]["test"][1] = "b0.s2"
            else:
                self.assertNotIn(self.builder, packet["messages"][-1]["content"])
                self.assertNotIn("decision", data["cases"])
                out = {"votes": []}
                for c in candidates:
                    ref = next(k for k, value in refs.items() if c["quote"] in value)
                    row = dict(id=c["id"], role="authored", link="serves", task="brief.s0", fact=ref,
                               use="Функция следует из брифа.", risk="Добавление обязанности.",
                               why="Ни одна исходная фраза не требует уничтожения этикетки.")
                    if wrong_anchor and model == self.panel[0] and c["id"] == "b":
                        row["task"] = ref
                    if wrong_anchor and model == self.panel[1] and c["id"] == "b":
                        row["fact"] = "b0.s200"
                    out["votes"].append(row)
            return httpx.Response(200, json={
                "model": model, "usage": {"cost": 0.0002},
                "choices": [{"message": {"content": json.dumps(out)}}],
            })

        with patch.dict(os.environ, {
            "EDITOR_VERIFICATION": "case_arbiter", "LLM_API_KEY": "mock", "PO_DELU_BUDGET_DB": "",
            "EDITOR_CASE_MODEL": self.builder, "EDITOR_JUDGES_JSON": json.dumps([{"model": m} for m in self.panel]),
        }):
            job = asyncio.run(verify_candidates(
                "x.txt", text.encode(),
                [dict(id=i, block_id="0", quote=q) for i, q in zip("abc", quotes)],
                context=brief, transport=httpx.MockTransport(handle),
            ))
        return job, calls, text, brief

    def test_false_dossier_cannot_cast_vote(self):
        job, calls, text, brief = self.run_case()
        self.assertFalse(job["findings"])
        self.assertEqual([s["stage"] for s in job["stages"]].count("case_builder"), 2)
        self.assertAlmostEqual(sum(s["cost_usd"] for s in job["stages"]), 0.0002 * len(calls))
        for verdict in job["verdicts"]:
            self.assertEqual((verdict["decision"], verdict["case_status"]), ("keep", "ok"))
            self.assertEqual([v["model"] for v in verdict["votes"]], list(self.panel[:2]))
            for field, original in (("task_anchor", brief), ("fact_anchor", text)):
                anchor = verdict[field]
                self.assertEqual(original[anchor["start"]:anchor["end"]], anchor["quote"])
        for kind, _, packet in calls:
            self.assertEqual("".join(packet["blocks"][0]["spans"].values()), text)
            self.assertEqual(packet["coverage"]["chars_shown"], len(text))
            if kind == "review":
                self.assertTrue(all("уничтожить" in case["harm"][0] for case in packet["cases"].values()))

    def test_large_block_ids_are_bound_to_visible_scope_and_source_role(self):
        job, calls, text, _ = self.run_case(long=True, wrong_anchor=True)
        self.assertEqual([v["case_status"] for v in job["verdicts"]], ["ok", "unavailable", "ok"])
        self.assertEqual([v["quorum_met"] for v in job["verdicts"]], [True, False, True])
        for kind, _, packet in calls:
            self.assertNotIn("b0.s200", packet["blocks"][0]["spans"])
            spans = packet["coverage"]["block_ranges"]["0"]
            emitted = "".join(packet["blocks"][0]["spans"].values())
            self.assertEqual(emitted, "".join(text[start:end] for start, end in spans))
            self.assertEqual(packet["coverage"]["chars_shown"], len(emitted))
            self.assertLess(len(emitted), packet["coverage"]["chars_total"])
            if kind == "review":
                self.assertNotIn("b", packet["cases"])

    def test_builder_failure_degrades_to_grounded_source_only_votes(self):
        job, calls, _, _ = self.run_case(failed=True)
        self.assertEqual(job["consensus"]["status"], "complete")
        for verdict in job["verdicts"]:
            self.assertEqual(verdict["case_file"]["error"], "http_503")
            self.assertEqual(verdict["decision"], "keep")
        self.assertTrue(all(not packet["cases"] for kind, _, packet in calls if kind == "review"))
