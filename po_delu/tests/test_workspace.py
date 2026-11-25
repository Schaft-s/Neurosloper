import asyncio
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from zipfile import ZipFile

import httpx
from fastapi.testclient import TestClient
from pptx import Presentation
from pptx.util import Inches, Pt

import preferences
from personal_search import search
from bot import Bot
from engine import DEMO, analyze, finish, rewrite
from formats import export, load
from web import app
from workspace import preserves_exceptions, propose


class Workspace(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(
            os.environ,
            {
                "PO_DELU_MEMORY_DB": str(Path(temp.name) / "preferences.db"),
                "EDITOR_DETECTOR": "offline",
                "LLM_API_KEY": "",
            },
        )
        env.start()
        self.addCleanup(env.stop)

    def test_html_passive_and_byte_preserving(self):
        source = """<!DOCTYPE html>\r
<html lang='ru'><head><title>не читать</title></head><body><!-- заметка -->
<script>privateFunction()</script><style>p { color: red; }</style><div hidden><p>скрыто</p></div>
<template>черновик</template><code>пример</code><p class = "x">😀&nbsp;фраза &amp; знак &#33; и фраза&nbsp;🦉</p><b>Сосед</b></body></html>"""
        data = source.encode("utf-8-sig")
        _, blocks = load("test.html", data)
        self.assertEqual([b["text"] for b in blocks], ["😀\xa0фраза & знак ! и фраза\xa0🦉", "Сосед"])
        current = blocks[0]["text"]
        spans = {"0": [dict(start=m, end=m + 5) for m in (current.index("фраза"), current.rindex("фраза"))]}
        edited = current.replace("фраза", "<i>&")
        name, raw = export("test.html", data, blocks, {"0": edited}, spans=spans)
        self.assertEqual(name, "edited.html")
        self.assertEqual(raw, source.replace("фраза", "&lt;i&gt;&amp;").encode("utf-8-sig"))
        self.assertEqual(export("test.html", data, blocks, {})[1], data)
        with self.assertRaises(ValueError):
            export("test.html", data, blocks, {"0": "совсем другой текст"}, spans=spans)

    def test_html_web_selection_and_version(self):
        client = TestClient(app)
        html = "<p>Документ &amp; " + DEMO + " &#33;</p>\r\n<!-- как было -->\r\n"
        review = client.post("/review", data={"text": html, "format": "html"})
        self.assertEqual(review.status_code, 200, review.text[:200])
        j = review.json()
        self.assertEqual(len(j["findings"]), 1)
        route = "/rewrite/" + j["token"]
        j = client.post(route, json={"ids": [j["findings"][0]["id"]]}).json()
        self.assertEqual(len(j["changes"]), 1)
        export_url = "/export/" + j["token"]
        accepted = client.post(export_url, json={"ids": ["0"]})
        self.assertEqual(accepted.content.decode(), html.replace(DEMO, "На мероприятии покажут станок."))
        self.assertEqual(client.post(export_url, json={"ids": []}).content, html.encode())
        with self.assertRaises(ValueError):
            finish(j, html.replace("Документ", "Файл").encode(), ["0"])

    def test_memory_durability_and_isolation(self):
        a, b = TestClient(app), TestClient(app)
        item = dict(kind="example", before="Нажмите кнопку", after="Нажмите", note="Кнопка подписана.")
        self.assertEqual(a.get("/preferences").json(), [])
        self.assertEqual(a.post("/preferences", json=item).status_code, 200)
        saved = a.get("/preferences").json()[0]
        reloaded = TestClient(app)
        reloaded.cookies.update(a.cookies)
        self.assertEqual(reloaded.get("/preferences").json()[0], saved)
        self.assertEqual(b.get("/preferences").json(), [])
        self.assertEqual(b.delete(f"/preferences/{saved['id']}").status_code, 404)
        self.assertEqual(preferences.entries("telegram:42"), [])
        j = a.post("/review", data={"text": "Нажмите кнопку справа."}).json()
        self.assertEqual(j["findings"], [])
        self.assertEqual(j["personal_total"], 1)
        token = j["token"]
        for path in ["/rewrite/" + token, "/export/" + token, "/preview/" + token + "/0"]:
            self.assertEqual(b.post(path, json={"ids": []}).status_code, 404)
        self.assertEqual(b.get("/report/" + token + "/docx").status_code, 404)
        proposal = a.post("/rewrite/" + token, json={"ids": [j["personal_matches"][0]["id"]]}).json()
        self.assertEqual(proposal["changes"][0]["after"], "Нажмите справа.")
        self.assertTrue(proposal["changes"][0]["personal"])
        self.assertEqual(
            a.post("/export/" + proposal["token"], json={"ids": ["0"]}).content.decode(), "Нажмите справа."
        )
        self.assertEqual(a.delete(f"/preferences/{saved['id']}").json(), [])
        self.assertEqual(
            a.post("/rewrite/" + token, json={"ids": [j["personal_matches"][0]["id"]]}).status_code, 422
        )

    def test_unchanged_exception_inside_larger_selection(self):
        self.assertTrue(preserves_exceptions("Просим открыть Обзор сейчас.", "Откройте Обзор.", ["Обзор"]))
        self.assertFalse(
            preserves_exceptions("Сначала АБ, затем ВГ.", "Сначала ВГ, затем АБ.", ["АБ", "ВГ"])
        )
        self.assertFalse(
            preserves_exceptions("Это точная цитата", "Это короткая цитата", ["точная цитата"])
        )

    def test_consent_and_exceptions(self):
        owner = "telegram:420"
        preferences.add(owner, "example", "пусто", "чисто", "раздел про помещение")
        base = asyncio.run(analyze("draft.txt", DEMO.encode()))
        expected = asyncio.run(rewrite(base, DEMO.encode(), ["0"]))
        for share in (False, True):
            with patch("workspace.rewrite", new=AsyncMock(return_value=expected)) as spy:
                asyncio.run(propose(base, DEMO.encode(), ["0"], owner, share))
                examples = spy.await_args.args[0]["preference_examples"]
                self.assertEqual([r["note"] for r in examples], ["раздел про помещение"] if share else [])
        preferences.add(owner, "exception", "демонстрации станка")
        j = asyncio.run(propose(base, DEMO.encode(), ["0"], owner))
        self.assertEqual(len(j["findings"]), 1)
        self.assertEqual(j["changes"], [])
        self.assertTrue(j["refusals"])
        self.assertEqual(j["personal_matches"][0]["kind"], "exception")

    def test_opt_in_model_similarity_and_anchor(self):
        owner = "telegram:777"
        preferences.add(
            owner, "example", "перейдите на главный экран", "откройте обзор", note="навигация в справке"
        )
        preferences.add("telegram:123", "example", "Чужой пример", "Другая запись")
        rule = preferences.entries(owner)[0]
        data = "Перейдите в начальное меню.".encode()
        base = asyncio.run(analyze("draft.txt", data))
        requests = []

        def model(request):
            requests.append(json.loads(request.content))
            picks = [
                {"rule_id": rule["id"], "block_id": "0", "quote": q, "reason": "Та же навигация."}
                for q in ["Перейдите в начальное меню", "Несуществующая цитата"]
            ]
            return httpx.Response(
                200, json={"choices": [{"message": {"content": json.dumps({"matches": picks})}}]}
            )

        mock = httpx.MockTransport(model)
        with patch.dict(
            os.environ,
            {"EDITOR_REWRITER_MODEL": "fixture", "EDITOR_REWRITER_BASE_URL": "http://127.0.0.1:1/v1"},
        ):
            with self.assertRaises(ValueError):
                asyncio.run(search(base, owner, consent=True, transport=mock))
            base = dict(base, detector="union")
            with self.assertRaises(ValueError):
                asyncio.run(search(base, owner, consent=False, transport=mock))
            self.assertFalse(requests)
            job = asyncio.run(search(base, owner, consent=True, transport=mock))
        self.assertEqual(job["personal_search"]["errors"], 1)
        self.assertEqual(len(job["personal_variants"]), 1)
        self.assertEqual(job["findings"], [])
        payload = json.loads(requests[0]["messages"][1]["content"])
        self.assertEqual([e["id"] for e in payload["examples"]], [rule["id"]])
        self.assertEqual(payload["examples"][0]["note"], "навигация в справке")
        match = job["personal_variants"][0]
        with self.assertRaises(ValueError):
            asyncio.run(propose(job, data, [match["id"]], owner, False))

        async def rewrite_variant(settings, raw, ids):
            self.assertEqual([e["id"] for e in settings["findings"]], ids)
            self.assertTrue(settings["preference_examples"])
            return dict(
                settings,
                changes=[dict(id="0", after="Откройте обзор.", reason="Предпочтение")],
                refusals=[],
                selected_spans={"0": [{"start": match["start"], "end": match["end"]}]},
            )

        with patch("workspace.rewrite", new=rewrite_variant):
            changed = asyncio.run(propose(job, data, [match["id"]], owner, True))
        self.assertEqual(changed["findings"], [])
        self.assertEqual(finish(changed, data, ["0"])[1].decode(), "Откройте обзор.")

    def test_document_model_consent_with_real_orchestrator(self):
        client = TestClient(app)
        calls = []

        def model(req):
            calls.append(json.loads(req.content))
            if len(calls) % 2:
                message = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "read",
                            "type": "function",
                            "function": {"name": "read_blocks", "arguments": "{}"},
                        }
                    ],
                }
            else:
                message = {"role": "assistant", "content": "Проверка завершена."}
            return httpx.Response(200, json={"model": "fixture", "choices": [{"message": message}]})

        transport = httpx.MockTransport(model)

        async def diagnosis(*args, **kwargs):
            return await analyze(*args, transport=transport, **kwargs)

        async def edit(*args, **kwargs):
            return await rewrite(*args, transport=transport, **kwargs)

        configured = {
            "PO_DELU_REQUIRE_MODEL_CONSENT": "1",
            "LLM_MODEL": "fixture",
            "LLM_API_KEY": "fixture",
            "LLM_BASE_URL": "https://consent-fixture.invalid/v1",
            "EDITOR_DETECTOR": "baseline",
        }
        with (
            patch.dict(os.environ, configured),
            patch("web.analyze", new=diagnosis),
            patch("workspace.rewrite", new=edit),
        ):
            self.assertNotIn('id="consent-panel" hidden disabled', client.get("/").text)
            client.post(
                "/preferences", json=dict(kind="example", before="СекретПамяти", after="ЛичнаяЗапись")
            )
            local = client.post("/review", data={"text": DEMO}).json()
            self.assertEqual(local["detector"], "offline")
            self.assertFalse(local["document_model_consent"])
            self.assertTrue(
                client.post(
                    "/rewrite/" + local["token"], json={"ids": ["0"], "share_examples": True}
                ).json()["changes"]
            )
            self.assertEqual(
                client.post("/personal-check/" + local["token"], json={"share_examples": True}).status_code,
                422,
            )
            self.assertEqual(
                client.post("/review", data={"text": DEMO, "analysis_mode": "models"}).status_code, 422
            )
            self.assertEqual(calls, [])
            allowed = client.post(
                "/review", data={"text": DEMO, "analysis_mode": "models", "share_document": "true"}
            )
            self.assertEqual(allowed.status_code, 200, allowed.text[:120])
            self.assertTrue(allowed.json()["document_model_consent"])
            self.assertEqual(len(calls), 2)
            seen = json.dumps(calls, ensure_ascii=False)
            self.assertIn(DEMO, seen)
            self.assertNotIn("СекретПамяти", seen)
            other = client.post("/review", data={"text": "Второй документ."}).json()
            self.assertEqual(other["detector"], "offline")
            self.assertEqual(len(calls), 2)
            with patch.dict(os.environ, {"PO_DELU_REQUIRE_MODEL_CONSENT": "0"}):
                old = client.post("/review", data={"text": "Прежний клиент."}).json()
                self.assertEqual(old["detector"], "baseline")
                self.assertEqual(len(calls), 4)

    def test_private_bot_local_html(self):
        calls = []

        async def run():
            def sink(req):
                calls.append(req)
                return httpx.Response(200, json={"ok": True, "result": True})

            async with httpx.AsyncClient(transport=httpx.MockTransport(sink)) as client:
                bot = Bot("fixture", client)

                async def message(chat, text):
                    await bot.handle(dict(chat=dict(id=chat, type="private"), text=text))

                for line in [
                    "/example На экране | Здесь | В справке",
                    "/format html",
                    "<p>На экране &amp; рядом</p>",
                    "/rewrite p1",
                    "/show 1",
                    "/save 1",
                ]:
                    await message(42, line)
                self.assertIn("<p>Здесь &amp; рядом</p>".encode(), calls[-1].content)
                await message(43, "/memory")
                self.assertIn("Личных правил пока нет", calls[-1].read().decode())
                bot.sessions.clear()
                await message(42, "/memory")
                self.assertIn("На экране", calls[-1].read().decode())
                await bot.handle(dict(chat=dict(id=42, type="group"), text="/clear"))
                self.assertIn(42, bot.sessions)

        asyncio.run(run())

    def test_pptx_multiple_accepted_in_frame(self):
        deck = Presentation()
        slide = deck.slides.add_slide(deck.slide_layouts[6])
        frame = slide.shapes.add_textbox(Inches(0.4), Inches(0.4), Inches(8.8), Inches(2)).text_frame
        for n in range(2):
            p = frame.paragraphs[0] if n == 0 else frame.add_paragraph()
            run = p.add_run()
            run.text = DEMO
            run.font.size = Pt(12)
            run.font.name = "DejaVu Sans"
        slide.shapes.add_textbox(
            Inches(0.4), Inches(3.2), Inches(4), Inches(1)
        ).text_frame.text = "Соседний блок"
        stream = io.BytesIO()
        deck.save(stream)
        data = stream.getvalue()
        j = asyncio.run(analyze("slides.pptx", data, length="fit"))
        j = asyncio.run(rewrite(j, data, [f["id"] for f in j["findings"]]))
        self.assertEqual(len(j["changes"]), 2, j["refusals"])
        name, raw = finish(j, data, ["0", "1"])
        self.assertEqual(
            [b["text"] for b in load(name, raw)[1]],
            ["На мероприятии покажут станок."] * 2 + ["Соседний блок"],
        )
        self.assertEqual(finish(j, data, ["0"])[1] != raw, True)
        with ZipFile(io.BytesIO(raw)) as changed, ZipFile(io.BytesIO(data)) as original:
            for member in original.namelist():
                if member != "ppt/slides/slide1.xml":
                    self.assertEqual(original.read(member), changed.read(member))
