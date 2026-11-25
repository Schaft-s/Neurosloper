"""Measure exact, analogous and inapplicable preferences using isolated synthetic accounts."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'po_delu'))
from personal_search import search
from preferences import add, attach, entries, remove
from provider import digest
from spending import BudgetTransport,LARGE_PANEL

async def main(out):
    if out.exists():raise ValueError('Choose a new output')
    inputs=[json.loads(l) for l in (ROOT/'evaluation/v2/memory_cases.jsonl').read_text().splitlines()]
    os.environ.update(LLM_BASE_URL='https://openrouter.ai/api/v1',LLM_MODEL=LARGE_PANEL[0])
    if not os.environ.get('LLM_API_KEY'):raise ValueError('LLM_API_KEY required')
    with tempfile.TemporaryDirectory() as tmp:
        os.environ['PO_DELU_MEMORY_DB']=str(Path(tmp)/'synthetic.sqlite')
        async def one(r):
            owner='test/'+r['id'];rows=add(owner,'example',r['before'],r['after'],r['note'])
            def job(text,context=''):
                return {'blocks':[{'id':'0','label':'Письмо','text':text,'edit':True}], 'context':context,'detector':'union','revision_hash':digest(text.encode())}
            t=job(r['before']);checks={'owner_exact':attach(t,owner)['personal_total']==1,'other_profile':attach(t,'another')['personal_total']==0}
            events={}
            for category in ['positive','negative']:
                tx=BudgetTransport(ROOT/'evaluation/v1/runs/billing/budget.sqlite','v2-memory/'+r['id'])
                j=await search(job(r[category],r[category+'_brief']),owner,consent=True,transport=tx)
                events[category]={'matches':[x['quote'] for x in j['personal_variants']],'calls':j['personal_search']['stages'],'errors':j['personal_search']['errors']}
            remove(owner,rows[0]['id']);checks['deletion']=not entries(owner)
            return {'id':r['id'],'local_checks':checks,'events':events}
        result=await asyncio.gather(*(one(r) for r in inputs))
    out.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in result))
    print(sum(bool(r['events']['positive']['matches']) for r in result),sum(bool(r['events']['negative']['matches']) for r in result))
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('output',type=Path)
    asyncio.run(main(parser.parse_args().output))
