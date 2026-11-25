"""Live detection on the native showcase files, then author-selected rewrite and native export."""
import argparse
import asyncio
from pathlib import Path
import json, os, sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'po_delu'))
from engine import analyze,rewrite,finish
from formats import load
from spending import BudgetTransport,LARGE_PANEL
from score_benchmark import iou

async def main(a):
    v=ROOT/'evaluation/v2'; metadata=json.loads((v/'showcase.json').read_text())['documents'];out=v/a.output;out.mkdir(exist_ok=True)
    sonnet='anthropic/claude-sonnet-4.6'; mini='openai/gpt-5.4-mini'
    panel=([mini,LARGE_PANEL[1],sonnet] if a.verify=='reader_committee' else [LARGE_PANEL[0],LARGE_PANEL[1],sonnet])
    os.environ.update(LLM_BASE_URL='https://openrouter.ai/api/v1',LLM_MODEL=LARGE_PANEL[0],EDITOR_SEARCH_MODEL=sonnet,EDITOR_SEARCH_RUBRIC='reader_task',
       EDITOR_CASE_MODEL=mini,EDITOR_JUDGES_JSON=json.dumps([{'model':x} for x in panel]),EDITOR_VERIFICATION=a.verify,EDITOR_PARALLEL_VOTES='1',EDITOR_API_JSON='1',
       EDITOR_FIDELITY_JUDGES_JSON=json.dumps([{'model':m} for m in [sonnet,LARGE_PANEL[0],LARGE_PANEL[2]]]),EDITOR_FIDELITY_CONSENSUS='1',EDITOR_FIDELITY_POLICY=a.policy,EDITOR_REWRITER='minimal_edit')
    async def trial(ref):
        rec=out/(ref['id']+'.json')
        if rec.exists():return
        path=ROOT/ref['path'];data=path.read_bytes()
        tx=BudgetTransport(ROOT/'evaluation/v1/runs/billing/budget.sqlite','v2-showcase/'+ref['id'])
        # Only file content and the independently written audience brief enter discovery.
        j=await analyze(path.name,data,context=ref['brief'],transport=tx,detector='union')
        def aligned(f,objects):
            return any(f['block_id']==g['block_id'] and iou((f['start'],f['end']),(g['start'],g['end']))>=.5 for x in objects for g in x['locations'])
        matches=[s for s in j['findings'] if aligned(s,ref['expected'])]
        guard=[s for s in j['findings'] if aligned(s,ref['control'])]
        summary={'id':ref['id'],'expected_found':bool(matches),'protected_flagged':len(guard),'findings':j['findings'],'verdicts':j['verdicts'],'stages':j['stages'],
            'seen':j['seen'],'total':j['total'],'blocks':j['blocks'],'errors':j['errors'],'consensus':j['consensus'],'name':j['name'],'backend':j['backend'],'summary':j['summary'],'coverage_summary':j.get('coverage_summary',{})}
        if matches:
            if path.suffix=='.pptx':j['length']='fit'
            w=await rewrite(j,data,[matches[0]['id']],tx)
            summary['changes']=w['changes'];summary['refusals']=w['refusals'];summary['rewrite_stages']=w['stages'][len(j['stages']):]
            if w['changes']:
                fn,edited=finish(w,data,[x['id'] for x in w['changes']])
                _,blocks=load(fn,edited);actual={x['id']:x['after'] for x in w['changes']}
                assert [actual.get(b['id'],b['text']) for b in j['blocks']]==[b['text'] for b in blocks]
                (out/(path.stem+'-reviewed'+path.suffix)).write_bytes(edited)
                summary['native_roundtrip']=True
        rec.write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
        print(ref['id'],summary['expected_found'],summary['protected_flagged'],summary.get('native_roundtrip'),flush=True)
    await asyncio.gather(*(trial(r) for r in metadata))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--policy',default='relations_v2');p.add_argument('--verify',default='case_arbiter');p.add_argument('--output',default='showcase_run')
    asyncio.run(main(p.parse_args()))
