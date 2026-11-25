"""Legal effect and record identity are compared even if every general vote says pass."""

import asyncio
import json
import os
import unittest
from unittest.mock import patch

import httpx

from contract_relations import check_frames, compare
from engine import verify_replacements
from fidelity import confirmed


class ContractRelations(unittest.TestCase):
    panel = ["qwen/qwen3-30b", "google/gemma-3-27b"]

    def run_pair(self, before, after, axis, s_args, t_args, *, omit=False):
        def respond(req):
            request = json.loads(req.content)
            payload = json.loads(request["messages"][1]["content"])
            checks = {"authority": [], "identity": []}

            def frame(unit, text, args):
                return dict(unit=unit, quote=text, **args)

            if not omit:
                checks[axis] = [dict(s=frame("s1", before, s_args), t=frame("t1", after, t_args))]
            body = dict(
                status="pass",
                forward="yes",
                reverse="yes",
                counterexample="",
                reason="Совпадает.",
                source_conflicts=[],
                relations=[dict(s=["s1"], t=["t1"], fact="Сопоставлено.")],
                contract_checks=checks,
            )
            return httpx.Response(
                200,
                json={
                    "model": request["model"],
                    "choices": [{"message": {"content": json.dumps(body)}}],
                },
            )

        with patch.dict(
            os.environ,
            {
                "EDITOR_FIDELITY_POLICY": "relations_v3",
                "EDITOR_FIDELITY_JUDGES_JSON": json.dumps([{"model": m} for m in self.panel]),
                "LLM_MODEL": self.panel[0],
                "LLM_API_KEY": "fixture",
                "LLM_BASE_URL": "https://mock.invalid/v1",
                "PO_DELU_BUDGET_DB": "",
            },
        ):
            result = asyncio.run(
                verify_replacements(
                    "акт.txt",
                    before.encode(),
                    [dict(id="x", block_id="0", after=after)],
                    transport=httpx.MockTransport(respond),
                )
            )
            row = result["results"][0]
            self.assertEqual(
                confirmed(
                    row, result["revision_hash"], "0", before, after, [dict(start=0, end=len(before))]
                ),
                row["status"] == "pass",
            )
            return row

    def test_request_decision_witness_both_directions_and_incomplete_evidence(self):
        old = "Звукорежиссёр вправе предложить изменить темп."
        bad = "Звукорежиссёр вправе изменить темп."
        good = "Звукорежиссёр может попросить об изменении темпа."
        request = dict(
            actor="Звукорежиссёр", action="изменение темпа", power="request", denied="not_effective"
        )
        for s, t in ((old, bad), (bad, old)):
            with self.subTest(pair=(s, t)):
                # Both models mislabel direct authority as the right merely to suggest.
                r = self.run_pair(s, t, "authority", request, request)
                self.assertEqual(r["local_contracts"]["authority"]["status"], "reject")
                self.assertEqual((r["status"], r["consensus"]["status"]), ("reject", "veto"))
        self.assertEqual(self.run_pair(old, good, "authority", request, request)["status"], "pass")
        self.assertEqual(
            self.run_pair(old, good, "authority", request, request, omit=True)["status"], "uncertain"
        )

    def test_independent_model_frames_override_general_vote(self):
        old = "Руководитель вносит вопрос на рассмотрение."
        new = "Последнее слово остаётся за руководителем."
        x = dict(actor="Руководитель", action="решение вопроса", power="request", denied="not_effective")
        y = dict(x, power="decide", denied="effective")
        r = self.run_pair(old, new, "authority", x, y)
        self.assertEqual(r["semantic_status"], "reject")
        self.assertTrue(
            all(v["model_status"] == "pass" and v["contract_status"] == "reject" for v in r["votes"])
        )

    def test_grouping_unit_retains_key_and_secondary_circumstances(self):
        old = "Каталог группирует повторы по экспонату."
        bad = "Каталог группирует повторы по посетителю."
        good = "Повторные записи об одном экспонате каталог объединяет."
        obj = dict(entity="экспонаты", keys=["object"], two_objects="two", two_channels="one")
        person = dict(entity="экспонаты", keys=["person"], two_objects="one", two_channels="one")
        for s, t in ((old, bad), (bad, old)):
            with self.subTest(pair=(s, t)):
                r = self.run_pair(s, t, "identity", obj, obj)
                self.assertEqual(
                    (r["status"], r["local_contracts"]["identity"]["status"]), ("reject", "reject")
                )
        self.assertEqual(self.run_pair(old, good, "identity", obj, obj)["status"], "pass")
        hidden = "Для посетителя записи в каталоге сливаются в одну."
        self.assertEqual(self.run_pair(old, hidden, "identity", obj, person)["status"], "reject")
        benign = "Если номер экспоната совпадает, повторную запись объединяют с предыдущей."
        self.assertEqual(self.run_pair(old, benign, "identity", obj, obj)["status"], "pass")
        self.assertNotIn("identity", compare("Повторите опыт.", "Проведите опыт ещё раз."))

    def test_effort_is_specific_to_generation(self):
        s = "Реставратор обязан попытаться восстановить краску."
        t = "Реставратор обязан восстановить краску."
        self.assertNotIn("effort", compare(s, t))
        self.assertEqual(compare(s, t, generated=True)["effort"]["status"], "reject")
        sample = dict(
            authority=[],
            identity=[],
            effort=[
                dict(
                    s=dict(unit="s1", quote=s, undertaking="attempt", failed_after_attempt="complies"),
                    t=dict(unit="t1", quote=t, undertaking="result", failed_after_attempt="breach"),
                )
            ],
        )
        _, status, _ = check_frames(
            dict(contract_checks=sample), {"s1": s}, {"t1": t}, {}, generated=True
        )
        self.assertEqual(status, "reject")
