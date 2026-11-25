"""Scripted provider; real MCP process, model tool-call schema and results."""
import asyncio,json,os,unittest
from unittest.mock import patch
import httpx
from engine import analyze,rewrite,DEMO

class Agent(unittest.TestCase):
    def test_detection_then_retry_and_semantic_veto(self):
        sent=[]
        def call(name,args):
            return {"role":"assistant","content":None,"tool_calls":[{"id":"c"+str(len(sent)),"type":"function","function":{"name":name,"arguments":json.dumps(args)}}]}
        seq=[
            ("read_blocks",{}),("locate_finding",dict(block_id="0",quote=DEMO,pattern="Канцелярит",effect="Скрыто действие",normal_explanation="Жанр не требует цепочки существительных")),None,
            ("read_blocks",{}),("check_patch",dict(block_id="0",before=DEMO,after="Покажут 10 станков.",reason="Прямой глагол")),
            ("check_patch",dict(block_id="0",before=DEMO,after="Покажут станок.",reason="Прямой глагол")),None,None]
        def handler(req):
            i=len(sent);sent.append(json.loads(req.content))
            msg=call(*seq[i]) if seq[i] else {"role":"assistant","content":'{"safe_ids":[]}' if i==7 else "Готово"}
            return httpx.Response(200,json={"choices":[{"message":msg}]})
        transport=httpx.MockTransport(handler)
        async def run():
            j=await analyze("draft.txt",DEMO.encode(),transport=transport)
            self.assertEqual(len(j["findings"]),1)
            self.assertFalse(j["changes"])
            self.assertFalse(any(t["function"]["name"]=="check_patch" for t in sent[0]["tools"]))
            return await rewrite(j,DEMO.encode(),["0"],transport)
        with patch.dict(os.environ,{"LLM_API_KEY":"fixture","LLM_MODEL":"fixture"}):
            j=asyncio.run(run())
        checks=[c["result"]["accepted"] for c in j["trace"] if c["tool"]=="check_patch"]
        self.assertEqual(checks,[False,True]);self.assertEqual(j["changes"],[]);self.assertEqual(len(j["findings"]),1)
