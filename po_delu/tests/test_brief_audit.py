import asyncio
import json
import os
import unittest
from copy import deepcopy
from unittest.mock import patch

import httpx

from engine import audit_findings, verify_candidates
from provider import digest, family
from report import review_view


class Audit(unittest.TestCase):
    models = ("qwen/qwen3-235b-a22b-2507", "google/gemma-4-31b-it", "anthropic/claude-sonnet-4.6")
    text = "На витрине надпись «Переход». Теперь наш путь завершён."
    brief = "Название «Переход» требуется сохранить дословно. Витрина ещё не готова."

    def env(self, **extra):
        return patch.dict(os.environ, dict(
            LLM_MODEL=self.models[0], LLM_API_KEY="mock", PO_DELU_BUDGET_DB="",
            EDITOR_JUDGES_JSON=json.dumps([{"model": m} for m in self.models[:2]]),
            EDITOR_AUDIT_MODEL=self.models[2], EDITOR_AUDIT_FALLBACK_MODEL="",
            EDITOR_VERIFICATION="evidence_v2", EDITOR_BRIEF_AUDIT="0",
        ) | extra)

    def verdict(self, ident, quote, decision="flag"):
        votes = [dict(model=m, served_model=m, family=family(m), status="ok", decision=decision,
                      pattern="Редактор сам добавил название.", effect="Авторское название теряется.")
                 for m in self.models[:2]]
        return dict(id=ident, block_id="0", start=self.text.index(quote),
                    end=self.text.index(quote) + len(quote), quote=quote, decision=decision,
                    quorum_met=True, votes=votes)

    def replay(self, verdicts, handler, *, revision=None, brief=None):
        return asyncio.run(audit_findings("x.txt", self.text.encode(), verdicts,
                            revision_hash=revision or digest(self.text.encode()),
                            context=self.brief if brief is None else brief, transport=httpx.MockTransport(handler)))

    def response(self, model, out):
        return httpx.Response(200, json={
            "model": model, "usage": {"cost": 0.0001},
            "choices": [{"message": {"content": json.dumps(out)}}],
        })

    def test_anchored_replay_calls_only_auditor_and_preserves_original(self):
        rows = [self.verdict("a", "Переход"), self.verdict("b", "путь"),
                self.verdict("c", "витрине", "keep")]
        original = deepcopy(rows)
        calls = []

        def handle(req):
            packet = json.loads(req.content)
            data = json.loads(packet["messages"][1]["content"])
            calls.append(data)
            self.assertEqual(packet["model"], self.models[2])
            self.assertEqual([c["id"] for c in data["candidates"]], ["a", "b"])
            self.assertFalse(any(m in packet["messages"][1]["content"] for m in self.models))
            self.assertNotIn("votes", packet["messages"][1]["content"])
            return self.response(self.models[2], {"audits": [
                dict(id="a", kind="retention", support="direct", premise="p1",
                     ref="brief.s0", reason="Владелец требует сохранить это название."),
                dict(id="b", kind="contradiction", support="inferred", premise="p1",
                     ref="brief.s1", reason="В этом жанре возможна метафора."),
            ]})

        with self.env():
            out = self.replay(rows, handle)
            replay = self.replay(out["verdicts"], self.fail)
        self.assertEqual(len(calls), 1)
        self.assertEqual(out["stages"][0]["stage"], "brief_audit")
        self.assertEqual(rows, original)
        self.assertEqual([v["decision"] for v in out["verdicts"]], ["uncertain", "flag", "keep"])
        self.assertEqual(out["verdicts"][0]["votes"], rows[0]["votes"])
        witness = out["verdicts"][0]["audit"]["evidence"]
        self.assertEqual(witness["quote"], self.brief[witness["start"]:witness["end"]])
        self.assertEqual(replay["stages"], [])
        self.assertEqual(out["brief_audit"], dict(upheld=1, withdrawn=1, unavailable=0))

    def test_bad_evidence_provider_and_preflight(self):
        rows = [self.verdict("a", "Переход")]
        result = dict(id="a", kind="contradiction", support="direct", premise="p1", ref="brief.s888", reason="X")
        with self.env():
            for handler in (
                lambda r: self.response(self.models[2], {"audits": [result]}),
                lambda r: self.response(self.models[0], {"audits": [result]}),
                lambda r: httpx.Response(503),
            ):
                out = self.replay(rows, handler)
                self.assertEqual(out["verdicts"][0]["decision"], "flag")
                self.assertEqual(out["brief_audit"]["unavailable"], 1)
                self.assertEqual(len(out["errors"]), 1)
            with self.assertRaises(ValueError):
                self.replay(rows, self.fail, revision="wrong")
            with self.assertRaises(ValueError):
                self.replay([dict(rows[0], end=999)], self.fail)
        with self.env(EDITOR_AUDIT_MODEL=self.models[0]):
            out = self.replay(rows, self.fail)
        self.assertEqual(out["stages"], [])
        self.assertEqual(out["errors"][0]["error"], "no_independent_auditor")
        with self.env(EDITOR_AUDIT_MODEL=self.models[0], EDITOR_AUDIT_FALLBACK_MODEL=self.models[2]):
            out = self.replay(rows, lambda req: self.response(self.models[2], {"audits": [
                dict(id="a", kind="none", reason="Прямого опровержения нет.")
            ]}))
        self.assertEqual(out["stages"][0]["model"], self.models[2])
        self.assertEqual(out["verdicts"][0]["decision"], "flag")

    def test_normal_entry_point(self):
        def handle(req):
            packet = json.loads(req.content)
            if packet["model"] == self.models[2]:
                return self.response(self.models[2], {"audits": [
                    dict(id="a", kind="retention", support="direct", premise="p1",
                         ref="brief.s0", reason="Название задано владельцем.")
                ]})
            return self.response(packet["model"], {"votes": [
                dict(id="a", task=["brief", "Витрина"], fact=["0", "Переход"], role="authored",
                     link="adds", use="Изложение", risk="Шаблон заменил название", why="Приписано редактору")
            ]})
        with self.env(EDITOR_BRIEF_AUDIT="1"):
            job = asyncio.run(verify_candidates("x.txt", self.text.encode(),
                              [{"id": "a", "block_id": "0", "quote": "Переход"}],
                              context=self.brief, transport=httpx.MockTransport(handle)))
        self.assertEqual([s["stage"] for s in job["stages"]], ["vote_evidence", "vote_evidence", "brief_audit"])
        self.assertEqual(job["consensus"]["brief_audit"]["withdrawn"], 1)
        self.assertFalse(job["findings"])
        case = review_view(job)["cases"][0]
        self.assertIn("Бриф · «Название «Переход» требуется сохранить дословно.»", case["note"])
        self.assertNotIn("Голоса разделились", case["note"])
