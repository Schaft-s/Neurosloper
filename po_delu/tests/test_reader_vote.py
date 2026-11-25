import asyncio
import json
import os
import unittest
from unittest.mock import patch
import httpx
from engine import verify_candidates


class ReaderVote(unittest.TestCase):
    def test_two_anchored_votes_work_for_confirmed_and_clean_text(self):
        judges=[{'model':'qwen/qwen3-235b-a22b-2507'}, {'model':'google/gemma-4-31b-it'}]
        env={'EDITOR_VERIFICATION':'reader_committee','EDITOR_JUDGES_JSON':json.dumps(judges),'LLM_API_KEY':'dummy','LLM_BASE_URL':'http://localhost','PO_DELU_BUDGET_DB':''}
        async def evaluate(decision):
            def handler(request):
                body=json.loads(request.content)
                value={'votes':[{'id':'a','decision':decision,'why':'Вывод проверен по тексту.','normal':'Функция фразы проверена.','ref':'b0.s0','question':'Где будет размещён текст?' if decision=='uncertain' else ''}]}
                return httpx.Response(200,json={'model':body['model'],'choices':[{'message':{'content':json.dumps(value)}}]})
            return await verify_candidates('x.txt','Оформите заявку.'.encode(),[{'id':'a','block_id':'0','quote':'Оформите заявку.'}],context='Памятка.',transport=httpx.MockTransport(handler))
        with patch.dict(os.environ,env):
            for decision in ['flag','keep','uncertain']:
                job=asyncio.run(evaluate(decision));v=job['verdicts'][0]
                self.assertEqual(decision,v['decision']);self.assertTrue(v['quorum_met'])
                self.assertEqual(len(job['findings']),int(decision=='flag'))
