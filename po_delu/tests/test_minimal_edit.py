import asyncio
import json
import os
import unittest
from unittest.mock import patch

import httpx

from engine import finish, init_job, rewrite, settings, verify_replacements
from fidelity import RELATIONS
from minimal_edit import preflight


class MinimalEditing(unittest.TestCase):
    models = ["qwen/qwen3-30b", "google/gemma-3-27b"]
    before = "Принятие решения осуществляет совет. Секретарь ведёт запись."
    quote = "Принятие решения осуществляет совет."
    first = "Совет выносит решение."
    second = "Решение принимает совет."

    def setUp(self):
        guard = patch.dict(
            os.environ,
            {
                "EDITOR_REWRITER": "minimal_edit",
                "EDITOR_FIDELITY_POLICY": "relations_v2",
                "LLM_MODEL": self.models[0],
                "LLM_API_KEY": "fixture",
                "LLM_BASE_URL": "https://mock.invalid/v1",
                "EDITOR_FIDELITY_JUDGES_JSON": json.dumps([dict(model=m) for m in self.models]),
                "EDITOR_JUDGES_JSON": json.dumps([dict(model=m) for m in self.models]),
                "PO_DELU_BUDGET_DB": "",
            },
        )
        guard.start()
        self.addCleanup(guard.stop)

    def test_known_presuppositions_and_clause_seams(self):
        items = [
            (
                "Напомни об организации встречи.",
                "об организации встречи",
                "что ты организуешь встречу",
                True,
            ),
            ("Напомни об организации встречи.", "об организации встречи", "о встрече", False),
            (
                "В списке сведения отсутствуют.",
                "сведения отсутствуют",
                "сведения пока не поступили",
                True,
            ),
            ("В списке сведения отсутствуют.", "сведения отсутствуют", "сведений нет", False),
            ("До проверки приступать к сборке нельзя.", "приступать к сборке", "выполнять сборку", True),
            ("До проверки приступать к сборке нельзя.", "приступать к сборке", "начинать сборку", False),
            ("Вход допускается при наличии билета.", "при наличии билета", "если есть билет", True),
            ("Вход допускается при наличии билета.", "при наличии билета", ", если есть билет", True),
            ("Вход допускается при наличии билета.", "при наличии билета", "по билету", False),
        ]
        for block, quote, replacement, reject in items:
            start = block.index(quote)
            with self.subTest(replacement=replacement):
                self.assertEqual(
                    bool(
                        preflight(
                            block,
                            block.replace(quote, replacement),
                            [dict(id="x", start=start, end=start + len(quote))],
                            [dict(id="x", after=replacement)],
                        )
                    ),
                    reject,
                )

    def scenario(self, fault):
        data = self.before.encode()
        job = init_job(
            "x.txt", data, "", "", "manual-selection", settings("voice", "free", "minimal", [])
        )
        job.update(
            seen=1,
            findings=[
                dict(
                    id="q",
                    block_id="0",
                    quote=self.quote,
                    start=0,
                    end=len(self.quote),
                    decision="flag",
                    revision_hash=job["revision_hash"],
                    effect="Цепочка скрывает действие.",
                )
            ],
        )
        generated, voted = [], []

        def handle(request):
            req = json.loads(request.content)
            body = json.loads(req["messages"][1]["content"])
            if "block" in body:
                generated.append(body)
                answer = dict(
                    alternatives=[
                        dict(
                            strategy=strategy,
                            reason="Названо действие.",
                            replacements=[dict(id="q", after=value)],
                        )
                        for strategy, value in (("rephrase", self.second), ("subtractive", self.first))
                    ]
                )
            else:
                voted.append(body)
                self.assertEqual(body["generation_review"], "minimal_edit")
                evidence = dict(
                    agent="same",
                    origin="same",
                    phase="same",
                    outcome="both",
                    syntax="natural",
                    scenario="Совет решает независимо от состава секретариата.",
                    reason="Роль совета сохранена.",
                )
                answer = dict(
                    status="pass",
                    reason="Факты сохранены.",
                    forward="yes",
                    reverse="yes",
                    counterexample="",
                    source_conflicts=[],
                    relations=[
                        dict(
                            s=list(body["original"]),
                            t=list(body["proposal"]),
                            fact="Решение принимает совет.",
                        )
                    ],
                    edits=[dict(id="q", status="fixed", reason="Убрана канцелярская цепочка.")],
                    minimal_check=evidence,
                )
                if self.first in body["proposal"].values() and req["model"] == self.models[1]:
                    if fault == "actor":
                        evidence.update(agent="changed", outcome="only_original")
                    elif fault == "syntax":
                        evidence.update(syntax="defect")
                    elif fault == "missing":
                        del answer["minimal_check"]
            return httpx.Response(
                200,
                json=dict(model=req["model"], choices=[dict(message=dict(content=json.dumps(answer)))]),
            )

        output = asyncio.run(rewrite(job, data, ["q"], httpx.MockTransport(handle)))
        return data, output, generated, voted

    def test_two_options_one_generator_and_fail_closed_fallback(self):
        for fault in ("none", "actor", "syntax", "missing"):
            with self.subTest(fault=fault):
                data, j, gen, votes = self.scenario(fault)
                self.assertEqual(len(gen), 1)
                self.assertEqual(len(votes), 2 if fault == "none" else 4)
                expected = self.first if fault == "none" else self.second
                self.assertEqual(
                    finish(j, data, ["0"])[1].decode(), self.before.replace(self.quote, expected)
                )
                if fault == "syntax":
                    self.assertEqual(j["fidelity_checks"][0]["semantic_status"], "pass")
                    self.assertEqual(j["fidelity_checks"][0]["syntax_status"], "defect")
                if fault == "actor":
                    self.assertEqual(j["fidelity_checks"][0]["semantic_status"], "reject")

    def test_fixed_pairs_keep_frozen_protocol(self):
        calls = []

        def handle(request):
            req = json.loads(request.content)
            system, user = req["messages"]
            calls.append(system["content"])
            body = json.loads(user["content"])
            self.assertNotIn("generation_review", body)
            out = dict(
                status="pass",
                forward="yes",
                reverse="yes",
                reason="Оба выражения верны.",
                counterexample="",
                source_conflicts=[],
                relations=[
                    dict(
                        s=list(body["original"]),
                        t=list(body["proposal"]),
                        fact="Совет принимает решение.",
                    )
                ],
            )
            return httpx.Response(
                200, json=dict(model=req["model"], choices=[dict(message=dict(content=json.dumps(out)))])
            )

        result = asyncio.run(
            verify_replacements(
                "x.txt",
                self.quote.encode(),
                [dict(id="a", block_id="0", after=self.second)],
                transport=httpx.MockTransport(handle),
            )
        )
        self.assertEqual(calls, [RELATIONS, RELATIONS])
        self.assertEqual(result["results"][0]["status"], "pass")
