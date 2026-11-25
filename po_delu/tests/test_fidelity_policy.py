"""Synthetic Russian obligation examples and in-process budget routing tests (no external calls)."""

import asyncio
import json
import os
import unittest
from unittest.mock import patch

import httpx

from consensus import architecture
from engine import verify_replacements, verify_candidates
from policy import check
from provider import Client, Endpoint, ProviderError


class Obligations(unittest.TestCase):
    def test_force_and_binding(self):
        unsafe = [
            ("Мастер обязан закрыть заслонку.", "Мастеру следует закрыть заслонку."),
            ("Требуется регистрация кассеты.", "Кассету обычно регистрируют."),
            ("Необходимо настроить микрофон.", "Микрофон настроен."),
            ("Поверка обязательна.", "Поверка выполнена."),
            ("Пробирку надлежит маркировать.", "Пробирку маркируют."),
            ("Оператор не должен стирать запись.", "Оператор не обязан стирать запись."),
            ("Кладовщик должен опечатать дверь.", "Кладовщик обычно должен опечатать дверь."),
            (
                "Техник обязан проверить датчик, а архивист описывает папку.",
                "Техник проверяет датчик, а архивист обязан описать папку.",
            ),
        ]
        safe = [
            ("Мастер обязан закрыть заслонку.", "Мастер должен закрыть заслонку."),
            ("Требуется регистрация кассеты.", "Регистрация кассеты обязательна."),
            ("Инженеру необходимо настроить микрофон.", "Инженер обязан настроить микрофон."),
            ("Калибровку надлежит повторить.", "Калибровку требуется повторить."),
            ("Механик не обязан присутствовать.", "Механику не требуется присутствовать."),
            ("Подпись нужна для допуска.", "Подпись необходима для допуска."),
        ]
        safe.append(
            (
                "Нужно закрыть окно и выключить свет.",
                "Окно необходимо закрыть. Свет необходимо выключить.",
            )
        )
        for before, after in unsafe:
            with self.subTest(pair=(before, after)):
                self.assertTrue(check({"text": before, "edit": True}, after, "free", []))
        for before, after in safe:
            with self.subTest(pair=(before, after)):
                self.assertEqual(check({"text": before, "edit": True}, after, "free", []), "")

    def test_model_cannot_pass_using_only_object_or_obsolete_duty(self):
        before = "Секретарь обязан пронумеровать журнал."
        safe = "Секретарь должен пронумеровать журнал."
        weak = "Секретарю следует пронумеровать журнал."

        async def run(proposal, full):
            def handler(req):
                a, b = (before, proposal) if full else ("журнал", "журнал")
                out = dict(
                    status="pass",
                    reason="Сопоставлены",
                    source_conflicts=[],
                    claims=[dict(before=a, after=b, status="preserved")],
                )
                return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(out)}}]})

            r = await verify_replacements(
                "norm.txt",
                before.encode(),
                [dict(id="x", block_id="0", after=proposal)],
                transport=httpx.MockTransport(handler),
            )
            return r["results"][0]

        with patch.dict(
            os.environ, {"LLM_MODEL": "fixture", "LLM_API_KEY": "fixture", "PO_DELU_BUDGET_DB": ""}
        ):
            self.assertEqual(asyncio.run(run(safe, True))["status"], "pass")
            self.assertEqual(asyncio.run(run(safe, False))["status"], "uncertain")
            self.assertEqual(asyncio.run(run(weak, True))["status"], "reject")

    def test_unanchored_normal_explanation_cannot_cast_vote(self):
        model = "Qwen/Qwen3-30B-A3B-Instruct-2507"

        def judge(req):
            row = dict(
                id="a",
                decision="flag",
                pattern="x",
                effect="x",
                normal="x",
                normal_test="Проверено",
                counterfactual="x",
                voice="quoted",
                evidence={"block_id": "0", "quote": "выдуманная реплика"},
            )
            return httpx.Response(
                200,
                json={
                    "model": model,
                    "choices": [{"message": {"content": json.dumps({"votes": [row]})}}],
                },
            )

        with patch.dict(
            os.environ,
            {
                "LLM_MODEL": model,
                "LLM_API_KEY": "fixture",
                "EDITOR_JUDGES_JSON": json.dumps([{"model": model}]),
                "PO_DELU_BUDGET_DB": "",
            },
        ):
            r = asyncio.run(
                verify_candidates(
                    "x.txt",
                    "Я читаю письмо.".encode(),
                    [dict(id="a", block_id="0", quote="письмо")],
                    transport=httpx.MockTransport(judge),
                )
            )
        self.assertEqual(r["verdicts"][0]["votes"][0]["status"], "error")
        self.assertEqual(r["stages"][0]["status"], "partial")

    def test_budget_transport_and_architecture(self):
        calls = []

        def handler(req):
            calls.append(req.url.host)
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

        async def main():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
                c = Client(http, [], "sha")
                msg = [{"role": "system", "content": "x"}]
                with patch(
                    "spending.BudgetTransport", return_value=httpx.MockTransport(handler)
                ) as factory:
                    await c.chat(
                        Endpoint(
                            "qwen/qwen3-30b-a3b-instruct-2507", "https://openrouter.ai/api/v1", "unused"
                        ),
                        "personal_variants",
                        msg,
                        [],
                    )
                    factory.assert_called_once_with("/unused", "interactive/personal_variants")
                with self.assertRaisesRegex(ProviderError, "endpoint_outside_budget"):
                    await c.chat(Endpoint("x", "https://another.example/v1", "unused"), "vote", msg, [])
                await c.chat(Endpoint("x", "http://localhost:8080/v1", ""), "vote", msg, [])

        with patch.dict(os.environ, {"PO_DELU_BUDGET_DB": "/unused"}):
            asyncio.run(main())
        self.assertEqual(calls, ["openrouter.ai", "localhost"])
        self.assertEqual(
            architecture("qwen/qwen3-235b-a22b-2507"), architecture("qwen/qwen3-30b-a3b-instruct-2507")
        )
        self.assertEqual(architecture("google/gemma-4-31b-it"), "dense+sliding-and-global-attention")
