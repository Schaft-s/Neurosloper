"""Contract tests. Model replies are scripted, real document MCP runs locally."""

import asyncio
import json
import os
import unittest
from unittest.mock import patch

import httpx

from consensus import architecture, locate, regions
from engine import analyze, finish, rewrite, verify_candidates, verify_replacements
from policy import check
from provider import SLOP_SHA, digest, family, slopotron


class Cascade(unittest.TestCase):
    text = "В письме появился оборот. Соседний текст."
    quote = "появился оборот"
    models = ["Qwen/Qwen3.5-122B-A10B", "google/gemma-3-12b-it"]

    def env(self, models=None):
        return patch.dict(
            os.environ,
            {
                "LLM_MODEL": self.models[0],
                "LLM_BASE_URL": "http://no-network.invalid/v1",
                "LLM_API_KEY": "fixture",
                "EDITOR_JUDGES_JSON": json.dumps([{"model": m} for m in models or self.models]),
            },
        )

    def test_search_vote_rewrite(self):
        calls = []

        def handler(req):
            request = json.loads(req.content)
            model = request["model"]
            sys, usr = [m["content"] for m in request["messages"]]
            body = json.loads(usr)
            calls.append((sys, body, model))
            if "targets" in body:
                self.assertNotIn("style", body)
                self.assertNotIn("friendly", usr)
                items = [
                    dict(
                        block_id="0",
                        quote=self.quote,
                        occurrence=1,
                        pattern="generator-private",
                        effect="сигнал",
                        normal_explanation="гипотеза",
                    )
                ]
                if "Слопотрон — детектор" in sys:
                    items.append(dict(items[0], quote="отсутствует"))
                out = {"candidates": items}
            elif "candidates" in body:
                self.assertNotIn("generator-private", usr)
                self.assertNotIn("channel", usr)
                out = dict(
                    votes=[
                        dict(
                            id="0",
                            decision="flag",
                            pattern="приём",
                            effect="эффект",
                            normal="объяснение",
                            normal_test="Проверено по письму",
                            counterfactual="Определение оборота сохраняется",
                            voice="author",
                            evidence={"block_id": "0", "quote": self.quote},
                        )
                    ]
                )
            elif "selected" in body and "block" in body:
                out = dict(
                    replacements=[dict(id="0", after="есть оборот")],
                    reason="private generator style rationale",
                )
            else:
                self.assertNotIn("private generator", usr)
                self.assertEqual(body["source"], "Справка автора")
                out = dict(
                    status="pass",
                    reason="Сопоставлены обе стороны",
                    source_conflicts=[],
                    claims=[dict(before="оборот", after="оборот", status="preserved")],
                )
            return httpx.Response(
                200,
                json=dict(
                    model=model,
                    usage=dict(prompt_tokens=20, completion_tokens=15, cost=0.0001),
                    choices=[dict(message=dict(content=json.dumps(out, ensure_ascii=False)))],
                ),
            )

        async def run():
            t = httpx.MockTransport(handler)
            data = self.text.encode()
            j = await analyze(
                "work.txt",
                data,
                style="friendly",
                source="Справка автора",
                detector="union",
                transport=t,
            )
            self.assertEqual(len(j["candidates"]), 2)
            self.assertEqual(len(j["findings"]), 1)
            self.assertTrue(j["errors"])
            self.assertEqual(set(j["findings"][0]["channels"]), {"semantic", "slopotron"})
            self.assertTrue(j["findings"][0]["architecture_diversity_confirmed"])
            self.assertEqual(j["consensus"]["status"], "complete")
            self.assertEqual(len(j["usage"]), 4)
            j = await rewrite(j, data, ["0"], t)
            self.assertEqual(j["changes"][0]["after"], self.text.replace(self.quote, "есть оборот"))
            self.assertEqual(finish(j, data, ["0"])[1].decode(), j["changes"][0]["after"])
            with self.assertRaises(ValueError):
                finish(j, data + b" ", [])
            with self.assertRaises(ValueError):
                await rewrite(j, data + b" ", ["0"], t)

        with self.env():
            asyncio.run(run())
        self.assertEqual(len(calls), 6)
        self.assertEqual(digest(slopotron().encode()), SLOP_SHA)

    def test_failed_vote_alias_and_identity(self):
        data = self.text.encode()
        fixed = [
            dict(id="alpha", block_id="0", quote=self.quote),
            dict(id="lost", block_id="0", quote="never existed"),
        ]

        def handler(req):
            m = json.loads(req.content)["model"]
            if m == self.models[1]:
                return httpx.Response(503)
            return httpx.Response(
                200,
                json=dict(
                    model=m,
                    usage={"total_tokens": 12},
                    choices=[
                        dict(
                            message=dict(
                                content=json.dumps(
                                    dict(
                                        votes=[
                                            dict(
                                                id="alpha",
                                                decision="flag",
                                                effect="e",
                                                normal="n",
                                                normal_test="checked",
                                                counterfactual="removed",
                                                voice="author",
                                                evidence={"block_id": "0", "quote": self.quote},
                                                pattern="p",
                                            )
                                        ]
                                    )
                                )
                            )
                        )
                    ],
                ),
            )

        panel = self.models + ["some-repo/DeepSeek-R1-Distill-Qwen-7B", "opaque"]
        with self.env(panel):
            job = asyncio.run(
                verify_candidates("work.txt", data, fixed, transport=httpx.MockTransport(handler))
            )
        self.assertEqual(job["consensus"]["status"], "unavailable")
        self.assertFalse(job["findings"])
        self.assertEqual(job["verdicts"][0]["decision"], "uncertain")
        self.assertEqual(len(job["stages"]), 2)
        self.assertEqual(job["errors"][0]["id"], "lost")
        self.assertEqual(len(job["consensus"]["skipped"]), 2)
        self.assertEqual(job["stages"][1]["error"], "http_503")
        self.assertEqual(job["usage"], [{"total_tokens": 12}])
        self.assertIsNone(job["stages"][1]["cost_usd"])
        self.assertEqual(family("DeepSeek-R1-Distill-Llama-70B"), "llama")
        self.assertIsNone(architecture("gpt-4o"))

        def mislabeled(req):
            return httpx.Response(
                200, json=dict(model="opaque", choices=[dict(message=dict(content='{"votes": []}'))])
            )

        with self.env():
            job = asyncio.run(
                verify_candidates("work.txt", data, fixed[:1], transport=httpx.MockTransport(mislabeled))
            )
        self.assertEqual(job["verdicts"][0]["votes"][0]["error"], "unconfirmed_model_identity")

    def test_mcp_search_model_receives_anchor_failure(self):
        observed = []

        def handler(req):
            p = json.loads(req.content)
            i = len(observed)
            observed.append(p)
            if i < 3:
                args = (
                    {}
                    if i == 0
                    else dict(
                        block_id="0",
                        quote="not there" if i == 1 else self.quote,
                        pattern="p",
                        effect="e",
                        normal_explanation="n",
                    )
                )
                name = "read_blocks" if i == 0 else "locate_finding"
                msg = dict(
                    role="assistant",
                    tool_calls=[
                        dict(
                            id=str(i),
                            type="function",
                            function=dict(name=name, arguments=json.dumps(args)),
                        )
                    ],
                )
            else:
                msg = dict(role="assistant", content="Готово.")
            return httpx.Response(200, json={"model": self.models[0], "choices": [{"message": msg}]})

        with patch.dict(
            os.environ,
            {
                "LLM_MODEL": self.models[0],
                "LLM_API_KEY": "fixture",
                "EDITOR_SEARCH_PROTOCOL": "mcp",
                "EDITOR_JUDGES_JSON": "[]",
            },
        ):
            j = asyncio.run(
                analyze(
                    "file.txt",
                    self.text.encode(),
                    detector="semantic",
                    transport=httpx.MockTransport(handler),
                )
            )
        self.assertEqual(len(observed), 4)
        self.assertFalse(json.loads(observed[2]["messages"][-1]["content"])["accepted"])
        self.assertTrue(j["coverage"]["semantic"])
        self.assertEqual(len(j["candidates"]), 1)
        self.assertEqual(j["consensus"]["status"], "unavailable")
        self.assertFalse(j["findings"])

    def test_fidelity_channels_and_blinding(self):
        text = "У печи 14 датчиков, у пресса 31 датчик."
        proposal = text.replace("14", "XX").replace("31", "14").replace("XX", "31")

        def handler(req):
            payload = json.loads(json.loads(req.content)["messages"][-1]["content"])
            self.assertNotIn("literal_status", payload)
            out = dict(
                status="pass",
                source_conflicts=[],
                claims=[{"before": "датчик", "after": "датчик", "status": "preserved"}],
                reason="model missed swap",
            )
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(out)}}]})

        with self.env():
            result = asyncio.run(
                verify_replacements(
                    "x.txt",
                    text.encode(),
                    [dict(id="c", block_id="0", after=proposal)],
                    transport=httpx.MockTransport(handler),
                )
            )["results"][0]
        self.assertEqual(
            (result["semantic_status"], result["literal_status"], result["status"]),
            ("pass", "reject", "reject"),
        )
        self.assertTrue(
            check(
                dict(text=text, edit=True),
                text.replace("печи", "XXX").replace("пресса", "печи").replace("XXX", "пресса"),
                "free",
                [],
            )
        )

    def test_repeated_quotes_and_long_windows(self):
        with self.assertRaises(ValueError):
            locate("я я", "я")
        self.assertEqual(locate("я я", "я", 2), 2)
        text = "блок " * 3000
        chunks = list(regions([dict(text=text, id="0", label="", edit=True)]))
        self.assertEqual(chunks[-1][-1]["offset"] + len(chunks[-1][-1]["text"]), len(text))
        self.assertGreater(len(chunks), 2)
        short_tail = list(regions([dict(text="а" * 4500, id="0", label="", edit=True)]))
        self.assertEqual(len(short_tail), 2)

    def test_local_rewrite_two_spans_source_refusal(self):
        text = "В течение периода времени окна открыты. На протяжении всего периода времени горит свет."

        async def run():
            j = await analyze("file.txt", text.encode(), detector="offline")
            ids = [f["id"] for f in j["findings"]]
            self.assertEqual(len(ids), 2)
            changed = await rewrite(j, text.encode(), ids)
            self.assertEqual(
                changed["changes"][0]["after"],
                "В течение периода окна открыты. На протяжении всего периода горит свет.",
            )
            j["source"] = "Уточнение автора."
            refused = await rewrite(j, text.encode(), ids)
            self.assertFalse(refused["changes"])
            self.assertTrue(refused["refusals"])

        asyncio.run(run())
