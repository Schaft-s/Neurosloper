import asyncio
import json
import os
import unittest
from unittest.mock import patch

import httpx
from consensus import verify
from provider import Client
from spending import LARGE_PANEL


class ReleasePriority(unittest.TestCase):
    def verdict(self, decisions, channels):
        stage_names = []
        panel = ["openai/gpt-5.4-mini", LARGE_PANEL[1], LARGE_PANEL[0]]
        def respond(request):
            payload = json.loads(request.content)
            model = payload["model"]
            stage_names.append(model)
            action = decisions[panel.index(model)]
            vote = {"id":"a","decision":action,"why":"Сопоставлено с поручением.",
                    "normal":"Проверена функция строки.","ref":"b0.s0","question":"Чья реплика?"}
            return httpx.Response(200,json={"model":model,"choices":[{"message":{"content":json.dumps({"votes":[vote]})}}]})
        async def trial():
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
                rows,_ = await verify(Client(http,[],"test"),[dict(id="0",text="Подпишите акт.",label="Документ")],
                    [dict(id="a",block_id="0",quote="Подпишите акт.",start=0,end=14,channels=channels)],
                    "Внутренняя инструкция.","")
                return rows[0]
        env={"EDITOR_ALL_REVIEWERS":"1","EDITOR_FINDINGS_POLICY":"corroborated","EDITOR_VERIFICATION":"reader_committee",
             "EDITOR_JUDGES_JSON":json.dumps([{"model":m} for m in panel]),"LLM_BASE_URL":"http://localhost",
             "LLM_API_KEY":"fixture","PO_DELU_BUDGET_DB":"","EDITOR_BRIEF_AUDIT":"0"}
        with patch.dict(os.environ,env):
            row=asyncio.run(trial())
        self.assertEqual(set(stage_names),set(panel))
        self.assertTrue(row['architecture_diversity_confirmed'])
        return row

    def test_unsupported_disagreement_is_for_author(self):
        row=self.verdict(['flag','keep','flag'],['semantic'])
        self.assertEqual((row['panel_decision'],row['decision'],row['priority']),('flag','uncertain','author_review'))

    def test_two_sources_can_support_split_votes(self):
        row=self.verdict(['flag','keep','flag'],['semantic','slopotron'])
        self.assertEqual((row['priority'],row['decision']),('confirmed','flag'))

    def test_agreement_is_enough_with_one_source(self):
        row=self.verdict(['flag','flag','keep'],['semantic'])
        self.assertEqual(row['decision'],'flag')
