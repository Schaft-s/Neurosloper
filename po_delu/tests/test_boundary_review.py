import asyncio
import json
import os
import unittest
from unittest.mock import patch

import httpx

from boundary_review import compare, replay
from engine import finish, init_job, rewrite, settings


class Boundaries(unittest.TestCase):
    original = (
        "За смену мы наблюдали печь только с десяти до двенадцати часов. "
        "В этот промежуток термометр не показал температуры выше 80 градусов. "
        "За остальную часть смены записей нет."
    )
    proposal = (
        "Термометр печи с 10 до 12 часов показывал менее 80 градусов. "
        "Других наблюдений за эту смену у нас нет."
    )

    def test_observation_window_regression_and_replay(self):
        previous = dict(
            status="pass", policy="relations_v3", votes=[{"family": "gemma", "status": "pass"}]
        )
        corrupt = replay(self.original, self.proposal, previous)
        clean = replay(self.original, self.proposal.replace("менее", "не более"), previous)
        self.assertEqual(
            (corrupt["status"], clean["status"], previous["status"]), ("reject", "pass", "pass")
        )
        self.assertEqual(corrupt["votes"], previous["votes"])
        self.assertEqual(
            replay(self.original, self.original, dict(previous, status="reject"))["status"], "reject"
        )

    def test_inclusion_both_directions_and_comparable_units(self):
        for a, b in [
            ("Датчик не показал давления выше 37 кПа.", "Давление было менее 37 кПа."),
            ("Уровень не опускался ниже 18 см.", "Уровень был строго выше 180 мм."),
            ("Масса до 1,7 кг включительно.", "Масса менее 1 700 г."),
            ("Масса от 3 кг включительно.", "Масса свыше 3 кг."),
            ("Скорость не превышала 12 км/ч.", "Скорость была менее 12 км/ч."),
            ("Напряжение не было ниже −9 В.", "Напряжение было больше -9В."),
            ("Усилие ≤ 26 ньютонов.", "Усилие < 26 ньютонов."),
        ]:
            for old, new in ((a, b), (b, a)):
                with self.subTest(pair=(old, new)):
                    self.assertEqual(compare(old, new)["status"], "reject")

    def test_controls_and_ambiguous_scopes(self):
        for a, b in [
            ("Заряд не более 47%.", "Заряд не превышал 47 процентов."),
            ("Давление не падало ниже 1,2 кПа.", "Давление было не менее 1200 Па."),
            ("Масса до 19 кг.", "Масса менее 19 кг."),
            ("Вход запрещён при влажности выше 70%.", "Вход разрешён при влажности не выше 70%."),
            ("Масса левого ящика не выше 17 кг.", "Масса правого ящика меньше 17 кг."),
            ("Температура в сосуде №1 не превышала 12 °C.", "Температура в сосуде №2 была менее 12 °C."),
            ("Масса не выше 29 кг. Противовес весит 29 кг.", "Масса менее 29 кг."),
            ("Давление не превышало 59 кПа.", "Давление было меньше 59 Па."),
            ("Нагрузка от 3 до 5 кг.", "Нагрузка менее 5 кг."),
        ]:
            with self.subTest(pair=(a, b)):
                self.assertEqual(compare(a, b)["status"], "no_veto")

    def test_composed_operator_matrix(self):
        # Cartesian product of written bounds and observed bounds. Independently label
        # every expression by the set it denotes, including equality and sign.
        forms = {
            ("upper", True): [
                "не превышала {}",
                "меньше либо равна {}",
                "до {} включительно",
                "{} или меньше",
                "≤ {}",
            ],
            ("upper", False): ["строго менее {}", "меньше {}", "< {}"],
            ("lower", True): [
                "была не меньше {}",
                "больше или равна {}",
                "{} либо выше",
                "от {} включительно",
                "≥ {}",
            ],
            ("lower", False): ["была выше {}", "строго больше {}", "> {}"],
        }
        observations = [
            (
                "масса пробы",
                ("семьдесят три грамма", "семидесяти трёх граммов", "семидесяти трём граммам"),
                "73 г",
            ),
            (
                "температура образца",
                (
                    "минус тридцать два градуса",
                    "минус тридцати двух градусов",
                    "минус тридцати двум градусам",
                ),
                "−32 °",
            ),
            ("влажность пробы", ("61,25 %", "61,25 %", "61,25 %"), "61.25 %"),
        ]
        for metric, original_unit, revised_unit in observations:
            for (side_a, inc_a), originals in forms.items():
                for (side_b, inc_b), revisions in forms.items():
                    for a in originals:
                        for b in revisions:
                            if "равна" in a:
                                unit = original_unit[2]
                            elif a.startswith(("не превышала", "{}", "≤", "<", "≥", ">")):
                                unit = original_unit[0]
                            else:
                                unit = original_unit[1]
                            old = "Если створка закрыта, " + metric + " " + a.format(unit) + "."
                            new = (
                                metric.capitalize()
                                + " "
                                + b.format(revised_unit)
                                + " при закрытой створке."
                            )
                            expected = "reject" if side_a == side_b and inc_a != inc_b else "no_veto"
                            with self.subTest(original=old, proposal=new):
                                self.assertEqual(compare(old, new)["status"], expected)

    def test_generation_preflight_and_export_of_historical_proof(self):
        before = "Манометр не зарегистрировал давления выше 48 кПа."
        after = "Давление было менее 48 кПа."
        data = before.encode()
        j = init_job(
            "lab.txt", data, "", "", "manual-selection", settings("voice", "free", "minimal", [])
        )
        j.update(
            seen=1,
            findings=[
                dict(
                    id="f",
                    block_id="0",
                    quote=before,
                    start=0,
                    end=len(before),
                    decision="flag",
                    revision_hash=j["revision_hash"],
                    effect="Сократить фразу.",
                )
            ],
        )
        models = ("qwen/qwen3-30b", "google/gemma-3-27b")

        def fake(req):
            request = json.loads(req.content)
            body = json.loads(request["messages"][1]["content"])
            if "block" in body:
                value = dict(replacements=[dict(id="f", after=after)], reason="Короче.")
            else:
                value = dict(
                    status="pass",
                    forward="yes",
                    reverse="yes",
                    reason="Общий голос за.",
                    counterexample="",
                    source_conflicts=[],
                    relations=[dict(s=["s1"], t=["t1"], fact="Оценка давления.")],
                    edits=[dict(id="f", status="fixed", reason="Сокращено.")],
                    contract_checks=dict(authority=[], identity=[], effort=[]),
                )
            return httpx.Response(
                200,
                json={
                    "model": request["model"],
                    "choices": [{"message": {"content": json.dumps(value)}}],
                },
            )

        transport = httpx.MockTransport(fake)
        with patch.dict(
            os.environ,
            dict(
                EDITOR_REWRITER="targeted",
                LLM_MODEL=models[0],
                LLM_API_KEY="fixture",
                LLM_BASE_URL="https://mock.invalid",
                PO_DELU_BUDGET_DB="",
                EDITOR_FIDELITY_JUDGES_JSON=json.dumps([{"model": m} for m in models]),
                EDITOR_FIDELITY_POLICY="relations_v3",
            ),
        ):
            old = asyncio.run(rewrite(j, data, ["f"], transport))
            self.assertEqual(finish(old, data, ["0"])[1], after.encode())
            with patch.dict(os.environ, {"EDITOR_FIDELITY_POLICY": "relations_v4"}):
                with self.assertRaisesRegex(ValueError, "равенстве"):
                    finish(old, data, ["0"])
                current = asyncio.run(rewrite(j, data, ["f"], transport))
                self.assertEqual(current["changes"], [])
                self.assertIn("равенстве", current["refusals"][0]["reason"])
                self.assertFalse(any(s["stage"] == "fidelity_vote" for s in current["stages"]))
