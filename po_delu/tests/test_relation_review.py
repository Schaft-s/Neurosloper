"""Grounded forward/reverse reviews, independent families and actionable editing."""

import asyncio
import json
import os
import unittest
from unittest.mock import patch

import httpx

from engine import finish, init_job, rewrite, settings, verify_replacements
from fidelity import confirmed
from policy import check


class RelationReview(unittest.TestCase):
    models = ("qwen/qwen3-30b", "google/gemma-3-27b")
    old = "Сотрудник должен выполнить проведение сверки показаний."
    new = "Сотрудник обязан сверить показания."

    def setUp(self):
        config = patch.dict(
            os.environ,
            {
                "EDITOR_FIDELITY_POLICY": "relations_v2",
                "EDITOR_FIDELITY_CONSENSUS": "0",
                "EDITOR_JUDGES_JSON": json.dumps([{"model": m} for m in self.models]),
                "LLM_MODEL": self.models[0],
                "LLM_BASE_URL": "https://mock.invalid/v1",
                "LLM_API_KEY": "fixture",
                "PO_DELU_BUDGET_DB": "",
                "EDITOR_REWRITER": "targeted",
            },
        )
        config.start()
        self.addCleanup(config.stop)

    @staticmethod
    def verdict(body):
        return {
            "status": "pass",
            "forward": "yes",
            "reverse": "yes",
            "counterexample": "",
            "reason": "Обязанность, исполнитель и объект совпадают.",
            "source_conflicts": [],
            "relations": [
                {
                    "s": list(body["original"]),
                    "t": list(body["proposal"]),
                    "fact": "Сотруднику поручена сверка показаний.",
                }
            ],
            "edits": [
                {"id": issue["id"], "status": "fixed", "reason": "Цепочка заменена глаголом."}
                for issue in body["editorial_issues"]
            ],
        }

    def transport(self, callback):
        def handle(req):
            request = json.loads(req.content)
            model = request["model"]
            body = json.loads(request["messages"][1]["content"])
            return httpx.Response(
                200,
                json={
                    "model": model,
                    "choices": [{"message": {"content": json.dumps(callback(model, body))}}],
                },
            )

        return httpx.MockTransport(handle)

    def audit(self, callback, original=None, proposal=None, **options):
        before, after = original or self.old, proposal or self.new
        return asyncio.run(
            verify_replacements(
                "ручная-сверка.txt",
                before.encode(),
                [{"id": "pair", "block_id": "0", "after": after}],
                transport=self.transport(callback),
                **options,
            )
        )

    def test_literal_gate_and_equivalent_russian_realizations(self):
        def pre(old, new, terms=()):
            return check({"text": old, "edit": True}, new, "free", terms, relation_review=True)

        for old, new in (
            ("Участник обязан ответить.", "Ответьте."),
            ("Лимит — 1 час.", "Лимит — 60 минут."),
            ("Не меньше трёх попыток.", "Допускаются три и более попыток."),
            ("Запись 5-минутная.", "Запись длится пять минут."),
            ("До пятницы окно не открывают.", "До пятницы окно остаётся закрытым."),
        ):
            with self.subTest(pair=(old, new)):
                self.assertEqual(pre(old, new), "")
        for old, new, terms in (
            ("Лимит — 1 час.", "Лимит — 60 секунд.", []),
            ("Не больше 3 кг.", "Ровно 3 кг.", []),
            ("Курьер обязан ждать.", "Курьер вправе ждать.", []),
            ("Код ТН-204.", "Код ТН-203.", []),
            ("Откройте https://site.invalid/a", "Откройте https://site.invalid/b", []),
            ("Надпись «Плато».", "Надпись «Плато!».", []),
            ("Лист первый", "Лист первый готов", ["Лист первый"]),
        ):
            with self.subTest(pair=(old, new)):
                self.assertTrue(pre(old, new, terms))

    def test_distinct_families_two_directions_and_coverage(self):
        orig = self.old + " Результат печатают."
        prop = self.new + " Результат печатают."
        for fault, expected in (
            ("valid", "pass"),
            ("reverse", "reject"),
            ("coverage", "uncertain"),
            ("conflict", "reject"),
            ("fabricated_id", "uncertain"),
        ):

            def review(model, body):
                v = self.verdict(body)
                if model == self.models[1]:
                    if fault == "reverse":
                        v.update(
                            reverse="no", counterexample="Печать потребовалась от нового исполнителя."
                        )
                    if fault == "coverage":
                        v["relations"][0]["t"].pop()
                    if fault == "fabricated_id":
                        v["relations"][0]["s"].append("s999")
                    if fault == "conflict":
                        v["source_conflicts"] = ["Иной срок в ТЗ."]
                return v

            with self.subTest(fault=fault):
                result = self.audit(review, orig, prop)
                r = result["results"][0]
                self.assertEqual(r["status"], expected)
                self.assertEqual(
                    confirmed(
                        r, result["revision_hash"], "0", orig, prop, [{"start": 0, "end": len(orig)}]
                    ),
                    expected == "pass",
                )

    def test_targeted_retry_then_export(self):
        data = self.old.encode()
        job = init_job(
            "test.txt", data, "", "", "manual-selection", settings("voice", "free", "minimal", [])
        )
        finding = dict(
            id="q",
            block_id="0",
            quote=self.old,
            start=0,
            end=len(self.old),
            effect="Действие спрятано в существительных.",
            pattern="Цепочка существительных",
            normal="Термин не определён.",
            decision="flag",
            revision_hash=job["revision_hash"],
        )
        job.update(findings=[finding], seen=1)
        weak = self.old.replace("выполнить", "осуществить")
        attempts = []

        def respond(model, body):
            if "block" in body:
                self.assertEqual(body["editorial_issues"][0]["effect"], finding["effect"])
                attempts.append(body["previous_proposal"])
                return dict(
                    replacements=[dict(id="q", after=self.new if attempts[-1] else weak)],
                    reason="Упрощена конструкция.",
                )
            v = self.verdict(body)
            if weak in body["proposal"].values():
                v["edits"][0].update(status="remains", reason="Цепочка существительных осталась.")
            return v

        j = asyncio.run(rewrite(job, data, ["q"], self.transport(respond)))
        self.assertEqual(attempts, ["", weak])
        self.assertEqual([a["semantic_status"] for a in j["fidelity_checks"]], ["pass", "pass"])
        self.assertEqual([a["editorial_status"] for a in j["fidelity_checks"]], ["remains", "fixed"])
        self.assertEqual(finish(j, data, ["0"])[1], self.new.encode())
        j["changes"][0]["after"] = weak
        with self.assertRaises(ValueError):
            finish(j, data, ["0"])
