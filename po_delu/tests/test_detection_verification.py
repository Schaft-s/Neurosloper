"""Independent evidence ballots and the optional held-out family, through the fixed-target API."""

import asyncio
import json
import os
import unittest
from unittest.mock import patch

import httpx

from engine import verify_candidates


class Verification(unittest.TestCase):
    models = ["qwen/qwen3-235b-a22b-2507", "google/gemma-4-31b-it", "deepseek/deepseek-v3.2"]
    lines = ["Расписание", "Группа отправлена.", "Заявка передана.", "Сбор вчера.", "Решение завтра."]
    brief = "Это сводка для принимающего смену."

    def run_case(self, answers, *, mode="evidence_arbiter", models=None, context=None):
        calls = []
        context = self.brief if context is None else context

        def reply(request):
            packet = json.loads(request.content)
            model = packet["model"]
            system, message = packet["messages"]
            self.assertEqual([m["role"] for m in packet["messages"]], ["system", "user"])
            payload = json.loads(message["content"])
            self.assertEqual(list(payload), ["brief", "source", "blocks", "candidates"])
            self.assertEqual([b["text"] for b in payload["blocks"]], self.lines)
            self.assertNotIn("PRIVATE", message["content"])
            refs = [candidate["id"] for candidate in payload["candidates"]]
            calls.append((model, refs, system["content"]))
            votes = []
            for ident in refs:
                reply = answers[model][ident]
                evidence = {"block_id": "0", "quote": self.lines[0]}
                task_anchor = {"block_id": "brief", "quote": context} if context else evidence
                row = dict(
                    id=ident,
                    basis=reply if isinstance(reply, str) else "fit",
                    voice="author",
                    pattern="PRIVATE",
                    normal="защита",
                    challenge="возражение",
                    test="роль взята из брифа",
                    effect="последствие",
                    task_anchor=task_anchor,
                    fact_anchor=evidence,
                )
                if isinstance(reply, dict):
                    row.update(reply)
                votes.append(row)
            return httpx.Response(
                200,
                json={"model": model, "choices": [{"message": {"content": json.dumps({"votes": votes})}}]},
            )

        candidates = [
            dict(id="a", block_id="2", quote=self.lines[2]),
            dict(id="b", block_id="4", quote=self.lines[4]),
        ]
        with patch.dict(
            os.environ,
            {
                "EDITOR_VERIFICATION": mode,
                "EDITOR_JUDGES_JSON": json.dumps([{"model": m} for m in models or self.models]),
                "LLM_API_KEY": "fixture",
                "PO_DELU_BUDGET_DB": "",
            },
        ):
            job = asyncio.run(
                verify_candidates(
                    "x.txt", "\n".join(self.lines).encode(), candidates, context=context,
                    transport=httpx.MockTransport(reply),
                )
            )
        return job, calls

    def test_arbiter_only_gets_unsettled_locations_never_arguments(self):
        first, second, third = self.models
        answers = {
            first: {"a": "displaced", "b": "fit"},
            second: {"a": "displaced", "b": "displaced"},
            third: {"b": "displaced"},
        }
        job, calls = self.run_case(answers)
        self.assertEqual([(m, ids) for m, ids, _ in calls], [
            (first, ["a", "b"]), (second, ["a", "b"]), (third, ["b"]),
        ])
        self.assertEqual(len({prompt for _, _, prompt in calls}), 3)
        a, b = job["verdicts"]
        self.assertEqual([x["id"] for x in job["findings"]], ["a", "b"])
        self.assertEqual((a["without_arbiter"], a["arbiter_called"]), ("flag", False))
        self.assertEqual((b["without_arbiter"], b["arbiter_called"]), ("uncertain", True))
        self.assertEqual(b["votes"][-1]["role"], "arbiter")
        pair, _ = self.run_case(answers, mode="evidence")
        self.assertEqual([v["decision"] for v in pair["verdicts"]], [v["without_arbiter"] for v in (a, b)])
        self.assertNotEqual(pair["config_hash"], job["config_hash"])

    def test_bad_anchors_do_not_vote_and_repeated_family_is_skipped(self):
        first, second, third = self.models
        answers = {
            first: {"a": {"basis": "displaced", "fact_anchor": {"block_id": "0", "quote": "вымысел"}},
                    "b": "missing"},
            second: {"a": {"basis": "displaced", "task_anchor": {"block_id": "0", "quote": self.lines[0]}},
                     "b": "displaced"},
            third: {"b": "fit"},
        }
        panel = [first, "another/qwen3-30b", second, third]
        job, calls = self.run_case(answers, models=panel)
        a, b = job["verdicts"]
        self.assertFalse(a["quorum_met"])
        self.assertTrue(b["quorum_met"])
        self.assertEqual([v["decision"] for v in (a, b)], ["uncertain", "uncertain"])
        self.assertFalse(job["findings"])
        self.assertEqual(job["consensus"]["skipped"][0]["reason"], "duplicate_family")
        self.assertEqual(calls[-1][1], ["b"])

    def test_unknown_role_can_remain_unresolved_without_brief(self):
        answers = {m: {"a": "missing", "b": "fit"} for m in self.models}
        job, calls = self.run_case(answers, context="")
        self.assertEqual(len(calls), 2)
        self.assertEqual([v["decision"] for v in job["verdicts"]], ["uncertain", "keep"])
        self.assertEqual(job["consensus"]["status"], "complete")
        self.assertFalse(job["findings"])

    def test_literal_mandate_source_conflict_and_irrelevant_uncertainty(self):
        text = "На афише напечатано «Вода слушает»."
        quote = "Вода слушает"
        brief = "В каталоге сохраните дословное название афиши."
        source = "На подлиннике заголовок «Слушать воду»."

        def judge(role, link, *, flip=None, exact_source=""):
            def handle(req):
                packet = json.loads(req.content)
                row = dict(
                    id="t", role=role, link=link,
                    task=["source", "Слушать воду"] if exact_source else ["brief", "сохраните дословное название"],
                    fact=[0, quote],
                    use="Под названием можно найти работу.",
                    risk="Голос автора смешивается с голосом составителя.",
                    why="Сверка названия важнее догадки о художественном образе.",
                )
                if flip is not None:
                    row["flip"] = flip
                return httpx.Response(200, json={
                    "model": packet["model"],
                    "choices": [{"message": {"content": json.dumps({"votes": [row]})}}],
                })

            with patch.dict(os.environ, {
                "EDITOR_VERIFICATION": "evidence_arbiter_v2",
                "EDITOR_JUDGES_JSON": json.dumps([{"model": m} for m in self.models]),
                "LLM_API_KEY": "fixture",
                "PO_DELU_BUDGET_DB": "",
            }):
                return asyncio.run(verify_candidates(
                    "x.txt", text.encode(), [{"id": "t", "block_id": "0", "quote": quote}],
                    context=brief, source=exact_source, transport=httpx.MockTransport(handle),
                ))
        kept = judge("fixed", "hinges")
        self.assertEqual(kept["verdicts"][0]["decision"], "keep")
        self.assertEqual(len(kept["stages"]), 2)
        self.assertEqual(judge("fixed", "mismatch", exact_source=source)["verdicts"][0]["decision"], "flag")
        two_cases = judge("unknown", "hinges", flip=["название из каталога", "новое название составителя"])
        self.assertTrue(two_cases["verdicts"][0]["quorum_met"])
        self.assertEqual(two_cases["verdicts"][0]["decision"], "uncertain")
        for flip in (None, ["неясен мотив", "неясен мотив"]):
            self.assertFalse(judge("unknown", "hinges", flip=flip)["verdicts"][0]["quorum_met"])
