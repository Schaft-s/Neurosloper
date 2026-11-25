"""Presentation shared by the web review and the DOCX export."""

import io
import unittest
from unittest.mock import patch

from docx import Document
from fastapi.testclient import TestClient

from report import diagnostic_report, review_view
from web import app


class ReviewDisplayTests(unittest.TestCase):
    def setUp(self):
        self.job = {
            "name": "notes.md",
            "backend": "Проверяющие",
            "context": "Для читателя",
            "total": 5,
            "editorial_total": 4,
            "seen": 4,
            "blocks": [
                {"id": str(i), "label": f"Строка {i + 1}", "text": value}
                for i, value in enumerate(("Первое", "Второе", "Третье", "Четвёртое"))
            ]
            + [{"id": "4", "label": "Код", "text": "code", "editorial": False}],
            "consensus": {"quorum": 2, "status": "complete"},
            "verdicts": [],
            "findings": [],
            "coverage_summary": {"semantic": ["0", "1", "2", "3"]},
        }

    def test_scope_and_no_findings(self):
        full = review_view(self.job)
        self.assertEqual(full["state"], "В проверенном тексте замечаний нет")
        self.assertIn("4 из 4", full["coverage"])
        self.assertIn("Исключено при разборе: 1.", full["notes"])
        self.job["coverage_summary"]["slopotron"] = ["2", "3"]
        partial = review_view(self.job)
        self.assertEqual(partial["state"], "Проверка выполнена частично")
        self.assertIn("Слопотрон — 2 из 4", partial["channels"])
        self.job.update(coverage_summary={}, seen=0, consensus={"status": "unavailable"})
        report = Document(io.BytesIO(diagnostic_report(self.job)[1]))
        paragraphs = [p.text for p in report.paragraphs]
        self.assertIn("Проверка выполнена частично", paragraphs)
        self.assertIn("Кворум проверяющих недоступен.", paragraphs)
        self.assertNotIn(full["state"], paragraphs)
        self.job.update(detector="offline", consensus={"status": "offline"}, seen=5)
        local = review_view(self.job)
        self.assertIn("4 из 4", local["coverage"])
        self.assertEqual(local["state"], "Локальные правила не нашли совпадений")

    def test_report_has_one_case_per_span_and_records_judgments(self):
        decisions = ["flag", "uncertain", "keep", "keep"]
        for i, decision in enumerate(decisions):
            b = self.job["blocks"][i]
            vote = dict(
                status="ok",
                role="reviewer",
                model="reviewer-a",
                decision=decision,
                task_anchor={"block_id": "brief", "quote": "Для читателя"},
                fact_anchor={"block_id": str(i), "quote": b["text"]},
                normal="Защита",
                challenge="Возражение",
                normal_test="Проверенное основание",
            )
            item = dict(
                id=str(i),
                block_id=str(i),
                start=0,
                end=len(b["text"]),
                quote=b["text"],
                pattern="Наблюдение",
                effect="Эффект",
                normal="Объяснение",
                decision=decision,
                votes=[vote],
            )
            if i == 2:
                item.update(arbiter_called=True, without_arbiter="uncertain")
                item["votes"] += [
                    dict(vote, decision="flag", model="reviewer-b"),
                    dict(vote, role="arbiter", model="reviewer-c"),
                ]
            if i == 1:
                vote["question"] = "Какой срок действует?"
                item["votes"].append(dict(status="error", role="reviewer", model="reviewer-b"))
            if i == 0:
                item["case_file"] = {
                    "status": "ok",
                    "dossier": {key: [key, "b0.s0"] for key in ("harm", "use", "test")},
                    "anchors": {
                        key: {"block_id": "0", "quote": "Первое"} for key in ("harm", "use", "test")
                    },
                    "coverage": {"chars_shown": 12, "chars_total": 24},
                }
            self.job["verdicts"].append(item)
        self.job["findings"].append(self.job["verdicts"][0].copy())
        view = review_view(self.job)
        self.assertEqual([case["group"] for case in view["cases"]], ["flag", "context", "dissent"])
        doc = Document(io.BytesIO(diagnostic_report(self.job)[1]))
        heads = [p.text for p in doc.paragraphs if p.style.name == "Heading 2"]
        self.assertEqual(heads, ["Строка 1", "Строка 2", "Строка 3"])
        text = "\n".join(p.text for p in doc.paragraphs)
        for phrase in (
            "Проверено текстовых блоков: 4 из 4",
            "Нужен контекст автора (1)",
            "Арбитр · reviewer-c · Оставить",
            "До арбитража — решение открыто",
            "Бриф · «Для читателя»",
            "Строка 3 · «Третье»",
            "Проверенное основание",
            "Оценка недоступна",
            "Что уточнить у автора · Какой срок действует?",
            "Составителю передано символов документа: 12 из 24.",
        ):
            self.assertIn(phrase, text)

    def test_editorial_priority_disagreement_context_and_advisory_objection(self):
        def case(i, decision, **details):
            quote = self.job["blocks"][i]["text"]
            return dict(
                id=str(i),
                block_id=str(i),
                start=0,
                end=len(quote),
                quote=quote,
                decision=decision,
                **details,
            )

        support = dict(role="reviewer", model="A", status="ok", decision="flag")
        opposed = dict(support, model="B", decision="keep")
        arbiter = dict(support, role="arbiter", model="C")
        audit = dict(
            status="withdrawn",
            served_model="D",
            reason="Название закреплено в брифе.",
            evidence=dict(block_id="brief", quote="Для читателя"),
        )
        confirmed = case(
            0,
            "flag",
            panel_decision="flag",
            without_arbiter="flag",
            priority="confirmed",
            priority_reason="Два рецензента подтвердили замечание.",
            votes=[support, dict(support, model="B")],
            audit=audit,
        )
        manual = case(
            1,
            "uncertain",
            panel_decision="flag",
            without_arbiter="uncertain",
            priority="author_review",
            priority_reason="Проверьте формулировку по задаче автора.",
            arbiter_called=True,
            quorum_met=True,
            votes=[support, opposed, arbiter],
        )
        question = case(2, "uncertain", question="Для кого написан текст?", quorum_met=True)
        unavailable = case(
            3, "uncertain", votes=[support, dict(status="error", model="B")], quorum_met=False
        )
        self.job.update(verdicts=[confirmed, manual, question, unavailable], findings=[confirmed.copy()])
        view = review_view(self.job)
        self.assertEqual(
            [c["group"] for c in view["cases"]], ["flag", "author_review", "context", "no_quorum"]
        )
        self.assertTrue(view["incomplete"])
        self.assertIn("Итог голосования — есть замечание", view["cases"][1]["arbitration"])
        self.assertNotIn("note", view["cases"][1])
        paragraphs = [p.text for p in Document(io.BytesIO(diagnostic_report(self.job)[1])).paragraphs]
        for caption in (
            "Разобрать автору (1)",
            "Нужен контекст автора (1)",
            "Недостаточно оценок (1)",
            confirmed["priority_reason"],
            manual["priority_reason"],
            "Строка 1",
            "Строка 2",
        ):
            self.assertEqual(paragraphs.count(caption), 1)
        objection = view["cases"][0]["note"]
        self.assertIn(objection, paragraphs)
        self.assertIn("Возражение дополнительной модели (D)", objection)
        self.assertIn("Название закреплено в брифе.", objection)
        self.assertIn("Бриф · «Для читателя»", objection)
        self.assertNotIn("снято", "\n".join(paragraphs))

    def test_fixed_intervals_do_not_imply_document_search(self):
        self.job.update(
            detector="fixed",
            seen=1,
            coverage_summary={},
            verdicts=[dict(block_id="0", start=0, end=6, decision="keep")],
        )
        view = review_view(self.job)
        self.assertFalse(view["incomplete"])
        self.assertEqual(view["state"], "В заданных интервалах замечаний нет")
        self.assertIn("Блоков с интервалами: 1 из 4", view["coverage"])
        self.assertEqual(view["channels"], [])

    def test_showcase_routes_do_not_serve_active_files(self):
        client = TestClient(app)
        example = client.get("/examples/fonoteka.html")
        self.assertEqual(example.headers["content-type"], "application/json")
        self.assertTrue(example.json()["blocks"])
        attachment = client.get("/examples/fonoteka.html/download")
        self.assertIn("attachment", attachment.headers["content-disposition"])
        self.assertEqual(attachment.headers["x-content-type-options"], "nosniff")
        self.assertEqual(client.get("/examples/example.md").status_code, 404)
        self.assertEqual(
            client.post(
                "/review", data={"showcase": "etiketki.md", "showcase_revision": "stale"}
            ).status_code,
            409,
        )
        self.assertEqual(client.post("/review", data={"showcase": "example.md"}).status_code, 404)
        self.assertEqual(
            client.post("/review", data={"showcase": "etiketki.md", "text": "другой текст"}).status_code,
            422,
        )

    def test_saved_showcase_records_match_sources_and_publish_only_review_fields(self):
        client = TestClient(app)
        for name in ("etiketki.md", "fonoteka.html", "korobka.pptx"):
            example = client.get("/examples/" + name).json()
            record = example["saved_review"]
            self.assertEqual(
                set(record),
                {"source_sha256", "date", "profile", "seen", "total", "search_errors", "cases", "edits"},
            )
            self.assertEqual(record["source_sha256"], example["revision"])
            blocks = {b["id"]: b["text"] for b in example["blocks"]}
            for c in record["cases"]:
                self.assertEqual(set(c), {"block_id", "start", "end", "quote", "decision", "votes"})
                self.assertIn(c["decision"], {"flag", "uncertain"})
                self.assertEqual(c["quote"], blocks[c["block_id"]][c["start"] : c["end"]])
                for vote in c["votes"]:
                    self.assertLessEqual(
                        set(vote), {"role", "family", "status", "decision", "reason", "evidence"}
                    )
                    if "evidence" in vote:
                        ref = vote["evidence"]
                        self.assertEqual(set(ref), {"block_id", "quote"})
                        self.assertIn(ref["quote"], blocks[ref["block_id"]])
            for edit in record["edits"]:
                if edit["status"] == "verified_native":
                    self.assertEqual(name, "etiketki.md")
                    self.assertEqual(
                        set(edit),
                        {"status", "block_id", "before", "after", "model", "profile", "editorial_note"},
                    )
                    self.assertEqual(edit["before"], blocks[edit["block_id"]])
                    self.assertNotEqual(edit["before"], edit["after"])
                else:
                    self.assertEqual(edit["status"], "refused")
                    self.assertEqual(set(edit), {"status", "block_id", "reason"})
        with patch("web.showcase_bytes", return_value="Новая редакция".encode()):
            changed = client.get("/examples/etiketki.md").json()
        self.assertIsNone(changed["saved_review"])
        self.assertEqual(changed["blocks"][0]["text"], "Новая редакция")
