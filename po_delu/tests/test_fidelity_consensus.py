"""Scripted providers, synthetic Russian paragraphs and a real MCP preflight."""

import asyncio
import json
import os
import unittest
from unittest.mock import patch

import httpx
from engine import DEMO, analyze, finish, rewrite, verify_replacements
from workspace import document_detector


class FidelityPanel(unittest.TestCase):
    models = ("Qwen/Qwen3.5-122B-A10B", "google/gemma-3-27b-it", "deepseek/deepseek-v3.2")
    old = "Электрику запрещается включать щиток."
    new = "Электрику запрещено включать щиток."

    def config(self, panel, dual=True):
        return patch.dict(
            os.environ,
            {
                "EDITOR_FIDELITY_CONSENSUS": "1" if dual else "0",
                "EDITOR_JUDGES_JSON": json.dumps([{"model": m} for m in panel]),
                "LLM_API_KEY": "fixture",
                "LLM_MODEL": self.models[0],
                "LLM_BASE_URL": "https://fixture.invalid/v1",
                "PO_DELU_BUDGET_DB": "",
            },
        )

    def reply(self, model, body, status="pass", partial=False, conflicts=None):
        return self.message(
            model,
            dict(
                status=status,
                reason="independent private " + model,
                source_conflicts=conflicts or [],
                claims=[
                    dict(
                        before="щиток" if partial else body["original"],
                        after="щиток" if partial else body["proposal"],
                        status="preserved",
                    )
                ],
            ),
        )

    def message(self, model, out):
        return httpx.Response(
            200,
            json={
                "model": model,
                "usage": {"total_tokens": 10},
                "choices": [
                    {"message": {"role": "assistant", "content": json.dumps(out, ensure_ascii=False)}}
                ],
            },
        )

    def audit(self, handler, panel=None, dual=True, before=None, after=None, **kw):
        before = before or self.old
        after = after or self.new
        with self.config(self.models[:2] if panel is None else panel, dual):
            result = asyncio.run(
                verify_replacements(
                    "x.txt",
                    before.encode(),
                    [dict(id="x", block_id="0", after=after, **kw.pop("interval", {}))],
                    transport=httpx.MockTransport(handler),
                    **kw,
                )
            )
        return result["results"][0]

    @staticmethod
    def unpack(req):
        data = json.loads(req.content)
        return data["model"], json.loads(data["messages"][1]["content"]), data["messages"]

    def test_two_blind_votes_and_single_baseline(self):
        transcripts = []

        def handler(req):
            model, body, messages = self.unpack(req)
            transcripts.append(messages)
            return self.reply(model, body)

        result = self.audit(handler)
        self.assertEqual(
            (result["literal_status"], result["status"]), ("equivalent_phrasing_confirmed", "pass")
        )
        self.assertEqual(len(result["consensus"]["pass_families"]), 2)
        self.assertEqual(transcripts[0], transcripts[1])
        self.assertNotIn("independent private", str(transcripts))
        baseline = self.audit(handler, dual=False)
        self.assertEqual(
            (baseline["literal_status"], baseline["semantic_status"], baseline["status"]),
            ("reject", "pass", "reject"),
        )

    def test_veto_failure_alias_identity_and_claim_evidence(self):
        def respond(failure):
            def call(req):
                model, body, _ = self.unpack(req)
                if model == self.models[1]:
                    if failure == "offline":
                        return httpx.Response(503)
                    if failure == "different":
                        return self.reply("google/gemma-3-4b-it", body)
                    if failure == "partial":
                        return self.reply(model, body, partial=True)
                    if failure == "reject":
                        return self.message(
                            model, dict(status="reject", reason="область действия утеряна")
                        )
                    if failure == "source":
                        return self.reply(model, body, conflicts=["источник противоречит"])
                return self.reply(model, body)

            return call

        for case in ["offline", "different", "partial"]:
            with self.subTest(case=case):
                result = self.audit(respond(case))
                self.assertEqual(result["status"], "uncertain")
                self.assertEqual(len(result["consensus"]["pass_families"]), 1)
        for case in ["reject", "source"]:
            result = self.audit(respond(case), panel=self.models)
            self.assertEqual(result["status"], "reject")
            self.assertEqual(result["consensus"]["status"], "veto")
        duplicates = self.audit(
            lambda r: self.fail("Insufficient configured families should not be called"),
            panel=[self.models[0], "mirror/DeepSeek-R1-Distill-Qwen-7B"],
        )
        self.assertEqual(duplicates["status"], "uncertain")
        self.assertTrue(duplicates["consensus"]["skipped"])

    def test_hard_risks_survive_two_passes(self):
        def yes(req):
            model, body, _ = self.unpack(req)
            return self.reply(model, body)

        examples = [
            (self.old, "Электрику разрешено включать щиток.", {}),
            (
                "Архивист вправе передать секретарю ключ.",
                "Секретарь имеет право передать архивисту ключ.",
                {},
            ),
            ("Для регистратора разрешено 15 минут.", "Для регистратора разрешается 50 минут.", {}),
            ("Для регистратора разрешено 15 минут.", "Для регистратора разрешается 15 секунд.", {}),
            (self.old, self.new, {"terms": ["запрещается"]}),
            (self.old, self.new, {"interval": {"start": 0, "end": 9}}),
        ]
        for before, after, extra in examples:
            with self.subTest(after=after):
                self.assertEqual(
                    self.audit(yes, before=before, after=after, **extra)["status"], "reject"
                )

    def test_forced_offline_with_configured_panel_and_consent_required(self):
        data = DEMO.encode()

        def forbidden(req):
            self.fail("Локальный черновик был передан в модельный транспорт")

        transport = httpx.MockTransport(forbidden)

        async def scenario():
            detector = document_detector("local", False)
            self.assertEqual(detector, "offline")
            diagnosis = await analyze("local.txt", data, detector=detector, transport=transport)
            result = await rewrite(diagnosis, data, ["0"], transport)
            self.assertEqual(result["fidelity_mode"], "local_rule")
            proof = result["changes"][0]["verification"]
            self.assertEqual(
                (proof["mode"], proof["semantic_status"], proof["status"]),
                ("local_rule", "local_rule", "pass"),
            )
            self.assertEqual(proof["votes"], [])
            self.assertEqual(result["stages"], [])
            self.assertEqual(finish(result, data, ["0"])[1].decode(), "На мероприятии покажут станок.")
            for constraint in ({"source": "Дополнительный источник."}, {"terms": [DEMO]}):
                declined = await rewrite(dict(diagnosis, **constraint), data, ["0"], transport)
                self.assertEqual(declined["changes"], [])
                self.assertTrue(declined["refusals"])
                self.assertEqual(declined["stages"], [])
            result["changes"][0]["after"] = "Другой текст."
            with self.assertRaises(ValueError):
                finish(result, data, ["0"])

        with (
            self.config(self.models[:2]),
            patch.dict(os.environ, {"PO_DELU_REQUIRE_MODEL_CONSENT": "1"}),
            patch.object(
                httpx.AsyncHTTPTransport,
                "handle_async_request",
                side_effect=AssertionError("external request"),
            ),
            patch.object(
                httpx.HTTPTransport, "handle_request", side_effect=AssertionError("external request")
            ),
        ):
            asyncio.run(scenario())

    def test_generated_synonym_roundtrip(self):
        data = self.old.encode()

        def handle(req):
            model, body, _ = self.unpack(req)
            if "targets" in body:
                out = dict(
                    candidates=[
                        dict(
                            block_id="0", quote=self.old, pattern="p", effect="e", normal_explanation="n"
                        )
                    ]
                )
            elif "candidates" in body:
                out = dict(
                    votes=[
                        dict(
                            id="0",
                            decision="flag",
                            pattern="p",
                            effect="e",
                            normal="n",
                            normal_test="n2",
                            counterfactual="c",
                            voice="author",
                            evidence={"block_id": "0", "quote": "щиток"},
                        )
                    ]
                )
            elif "block" in body:
                out = dict(
                    replacements=[dict(id="0", after=self.new)],
                    reason="Генератор не участвует в голосовании.",
                )
            else:
                return self.reply(model, body)
            return self.message(model, out)

        async def main():
            transport = httpx.MockTransport(handle)
            job = await analyze("x.txt", data, detector="semantic", transport=transport)
            job = await rewrite(job, data, ["0"], transport)
            self.assertEqual(job["changes"][0]["verification"]["status"], "pass")
            self.assertEqual(len(job["fidelity_checks"]), 1)
            self.assertEqual(finish(job, data, ["0"])[1].decode(), self.new)
            job["changes"][0]["after"] = self.old.replace("запрещается", "разрешается")
            with self.assertRaises(ValueError):
                finish(job, data, ["0"])

        with self.config(self.models[:2]):
            asyncio.run(main())
