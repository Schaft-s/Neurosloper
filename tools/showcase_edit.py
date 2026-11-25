"""Try the release editor on an already diagnosed native showcase file; annotations never enter the prompt."""
import argparse, asyncio, copy, hashlib, json, os, sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'po_delu'))
from engine import init_job,rewrite,settings,finish
from formats import load
from spending import BudgetTransport,LARGE_PANEL
from score_benchmark import iou

async def evaluate(name,model,label,policy):
    v=ROOT/'evaluation/v2'
    reference=next(r for r in json.loads((v/'showcase.json').read_text())['documents'] if r['id']==name)
    saved=json.loads((v/'showcase_run'/(name+'.json')).read_text())
    path=ROOT/reference['path'];raw=path.read_bytes();_,blocks=load(path.name,raw)
    if [b['text'] for b in blocks]!=[b['text'] for b in saved['blocks']]:raise ValueError('Source changed')
    hit=next(f for f in saved['findings'] if any(f['block_id']==t['block_id'] and iou((f['start'],f['end']),(t['start'],t['end']))>=.5 for e in reference['expected'] for t in e['locations']))
    os.environ.update(LLM_BASE_URL='https://openrouter.ai/api/v1',LLM_MODEL=LARGE_PANEL[0],
        EDITOR_REWRITER='minimal_edit',EDITOR_REWRITER_MODEL=model,EDITOR_FIDELITY_MODEL=LARGE_PANEL[2],
        EDITOR_FIDELITY_CONSENSUS='1',EDITOR_FIDELITY_POLICY=policy,EDITOR_API_JSON='1',EDITOR_PARALLEL_VOTES='1',
        EDITOR_JUDGES_JSON=json.dumps([{'model':m} for m in LARGE_PANEL]))
    j=init_job(path.name,raw,reference['brief'],'','manual-selection',settings('direct','fit' if path.suffix=='.pptx' else 'free','minimal',[]))
    j.update(findings=[hit],seen=len(blocks))
    tx=BudgetTransport(ROOT/'evaluation/v1/runs/billing/budget.sqlite','v2-showcase-edit/'+name)
    w=await rewrite(j,raw,[hit['id']],tx)
    result={k:w.get(k) for k in ('findings','changes','refusals','fidelity_checks','alternative_checks','stages','revision_hash','fidelity_mode')}
    result['generator']=model
    if w['changes']:
        fn,edited=finish(w,raw,[c['id'] for c in w['changes']]); _,parsed=load(fn,edited)
        changes={c['id']:c['after'] for c in w['changes']}
        assert [b['text'] for b in parsed]==[changes.get(b['id'],b['text']) for b in blocks]
        (v/'showcase_run'/(path.stem+'-accepted'+path.suffix)).write_bytes(edited)
        result['roundtrip']='verified'
    (v/'showcase_run'/(name+'-'+label+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(name,len(w['changes']))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('example');p.add_argument('--model',default='anthropic/claude-sonnet-4.6');p.add_argument('--label',default='rewrite');p.add_argument('--policy',default='relations_v3');a=p.parse_args()
    asyncio.run(evaluate(a.example,a.model,a.label,a.policy))
