"""Run the application on authorized synthetic quality fixtures without opening any gold file."""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
V = ROOT / 'evaluation/v2'
PUBLIC = ('qwen/qwen3-235b-a22b-2507','google/gemma-4-31b-it','deepseek/deepseek-v3.2')
SONNET = 'anthropic/claude-sonnet-4.6'
PANELS = {'public':PUBLIC, 'mixed':(SONNET,PUBLIC[0],PUBLIC[2]), 'fast':('openai/gpt-5.4-mini',PUBLIC[1],SONNET),'public_check':('openai/gpt-5.4-mini',PUBLIC[1],PUBLIC[0]), 'referee':(PUBLIC[0],PUBLIC[1],SONNET), 'referee_small':(PUBLIC[0],PUBLIC[1],'openai/gpt-5.4-mini'), 'editor_pair':(SONNET,PUBLIC[1],PUBLIC[0])}
REGIONS = {
    'dev-detect':('evaluation/lockbox/inputs.jsonl','evaluation/lockbox/targets.jsonl'),
    'dev-contrast':('evaluation/v1/contrast_inputs.jsonl','evaluation/v1/contrast_targets.jsonl'),
    'dev-meaning':('evaluation/rewrite_lockbox/inputs.jsonl',None),
    'dev-writing':('evaluation/v1/rewrite_inputs.jsonl',None),
    'fresh-detect':('evaluation/v2/sealed/detect_inputs.jsonl','evaluation/v2/sealed/detect_targets.jsonl'),
    'presentation-detect':('evaluation/v2/presentation_holdout/detect_inputs.jsonl','evaluation/v2/presentation_holdout/detect_targets.jsonl'),
    'audit2-detect':('evaluation/v2/audit2/detect_inputs.jsonl','evaluation/v2/audit2/detect_targets.jsonl'),
    'audit2-meaning':('evaluation/v2/audit2/rewrite_inputs.jsonl',None),
    'fresh-meaning':('evaluation/v2/sealed/rewrite_inputs.jsonl',None),
}
def lines(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
def digest(data):
    if not isinstance(data,bytes):data=json.dumps(data,ensure_ascii=False,sort_keys=True).encode()
    return hashlib.sha256(data).hexdigest()
def configure(a):
    panel=PANELS[a.panel]
    os.environ.update(LLM_BASE_URL='https://openrouter.ai/api/v1', LLM_MODEL=PUBLIC[0],
        EDITOR_JUDGES_JSON=json.dumps([{'model':m} for m in panel]),EDITOR_VERIFICATION=a.verify,
        EDITOR_SEARCH_RUBRIC=a.rubric,EDITOR_SEARCH_MODEL=SONNET if a.search=='sonnet' else PUBLIC[0],
        EDITOR_FIDELITY_CONSENSUS='1',EDITOR_FIDELITY_POLICY=a.policy,EDITOR_FIDELITY_MODEL=PUBLIC[2],
        EDITOR_FINDINGS_POLICY=a.priority,EDITOR_ALL_REVIEWERS='1' if a.all_reviewers else '0',EDITOR_BRIEF_AUDIT='1' if a.audit else '0',EDITOR_AUDIT_MODEL=PUBLIC[2],EDITOR_AUDIT_FALLBACK_MODEL=PUBLIC[0],EDITOR_CASE_MODEL=(SONNET if a.case=='sonnet' else 'openai/gpt-5.4-mini' if a.case=='mini' else PUBLIC[0]),EDITOR_PARALLEL_VOTES='1',EDITOR_SEARCH_PROTOCOL='json',EDITOR_REWRITER=a.rewriter,EDITOR_API_JSON=a.api_json)
    if not os.getenv('LLM_API_KEY'):raise ValueError('LLM_API_KEY required')
    if os.getenv('PO_DELU_BUDGET_DB'):raise ValueError('Use the benchmark ledger transport, without interactive overrides')
async def run(a):
    configure(a)
    app=Path(a.app).resolve()
    sys.path.insert(0,str(app))
    from engine import analyze,init_job,rewrite,settings,verify_candidates,verify_replacements
    from formats import load
    # The same budget policy is used even for a historical archive.
    import importlib.util
    spec=importlib.util.spec_from_file_location('trial_spending',ROOT/'po_delu/spending.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    source,targets=REGIONS[a.suite]
    docs=lines(ROOT/source)
    if a.limit:docs=docs[:a.limit]
    if a.ids:docs=[d for d in docs if d['id'] in a.ids]
    target_rows={r['id']:r for r in lines(ROOT/targets)} if targets and a.task=='fixed' else {}
    replay={r['id']:r for r in lines(V/'runs'/a.replay/'predictions.jsonl')} if a.replay else {}
    output=V/'runs'/a.name
    output.mkdir(parents=True,exist_ok=True)
    files=['engine.py','policy.py','fidelity.py','consensus.py','provider.py','document_tools.py','offline.py','formats.py','search_rubric.py','verifier.py','verification_v2.py','fidelity_relations.py','relations.py']
    modules=sorted({*files,*(p.name for p in app.glob('*.py') if p.name not in ('serve_loopback.py','bot.py','web.py'))})
    hashes={n:digest((app/n).read_bytes()) for n in modules if (app/n).exists()}
    cfg={'suite':a.suite,'task':a.task,'search_model':os.environ['EDITOR_SEARCH_MODEL'],'model':PUBLIC[0],'case_model':os.environ['EDITOR_CASE_MODEL'],
         'panel':PANELS[a.panel],'verify':a.verify,'rubric':a.rubric,'policy':a.policy,'rewriter':a.rewriter,'api_json':a.api_json,'brief_audit':a.audit,'priority':a.priority,'all_reviewers':a.all_reviewers,'audit_model':PUBLIC[2],'replay':a.replay,'hashes':hashes,
         'inputs':{d['id']:digest(d) for d in docs},'targets_hash':digest(target_rows) if target_rows else None}
    config=output/'config.json'
    if config.exists():
        if json.loads(config.read_text())!=json.loads(json.dumps(cfg)):raise ValueError('Run already exists with different configuration')
    else:config.write_text(json.dumps(cfg,ensure_ascii=False,indent=2)+'\n')
    dest=output/'predictions.jsonl';done={r['id'] for r in lines(dest)} if dest.exists() else set()
    gate=asyncio.Semaphore(a.parallel)
    async def case(d):
        if d['id'] in done:return
        async with gate:
            started=time.monotonic();raw=d['text'].encode();name='draft.txt';brief=d['request']
            tx=module.BudgetTransport(ROOT/'evaluation/v1/runs/billing/budget.sqlite','v2-'+a.name+'/'+d['id'])
            try:
                if a.replay:
                    from engine import audit_findings
                    old=replay[d['id']]
                    if old['text_sha256']!=digest(raw):raise ValueError('Input differs from original run')
                    audit=await audit_findings(name,raw,old['verdicts'],context=brief,revision_hash=digest(raw),transport=tx)
                    j={**old,'verdicts':audit['verdicts'],'findings':audit['findings'],'stages':old['stages']+audit['stages'],'errors':old.get('errors',[])+audit['errors'],'consensus':dict(old['consensus'],brief_audit=audit['brief_audit'])}
                elif a.task=='fixed':
                    t=target_rows[d['id']];t=t.get('target',t)
                    item={k:t[k] for k in ('block_id','start','end','quote') if k in t}
                    item.update(id='candidate',block_id=item.get('block_id','0'))
                    j=await verify_candidates(name,raw,[item],context=brief,transport=tx)
                elif a.task=='fidelity':
                    j=await verify_replacements(name,raw,d['candidates'],terms=d.get('locked',[]),context=brief,transport=tx)
                elif a.task=='rewrite':
                    j=init_job(name,raw,brief,'','manual-selection',settings(d.get('style','voice'),d.get('length','free'),'minimal',d.get('locked',[])))
                    t=d['target'];b=j['blocks'][int(t['block_id'])];s=b['text'].index(t['quote'])
                    j.update(seen=len(j['blocks']),findings=[dict(id='0',block_id=b['id'],start=s,end=s+len(t['quote']),quote=t['quote'],revision_hash=j['revision_hash'],effect=d.get('reason','Выбранный оборот мешает прочитать действие.'),pattern='Замечание автора',normal='',decision='flag')])
                    j=await rewrite(j,raw,['0'],tx)
                else:
                    j=await analyze(name,raw,context=brief,transport=tx,detector=a.task)
                out={k:j[k] for k in ['revision_hash','backend','coverage','coverage_summary','seen','total','consensus','candidates','verdicts','findings','refusals','changes','results','errors','stages','fidelity_checks'] if k in j}
                _,blocks=load(name,raw); cursor=0; offsets={}
                for b in blocks:
                    start=d['text'].find(b['text'],cursor)
                    if start<0:raise ValueError('Unresolved block offset')
                    offsets[b['id']]=start;cursor=start+len(b['text'])
                out['block_offsets']=offsets
            except Exception as e:
                out={'failure':type(e).__name__+': '+str(e)[:160]}
            out.update(id=d['id'],text_sha256=digest(raw),elapsed_s=round(time.monotonic()-started,3))
            with dest.open('a') as f:f.write(json.dumps(out,ensure_ascii=False)+'\n')
    await asyncio.gather(*(case(d) for d in docs))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('name');p.add_argument('suite',choices=REGIONS);p.add_argument('task',choices=['fixed','fidelity','rewrite','baseline','union','semantic','slopotron'])
    p.add_argument('--app',default=str(ROOT/'po_delu'))
    p.add_argument('--panel',choices=PANELS,default='public')
    p.add_argument('--search',choices=['qwen','sonnet'],default='qwen');p.add_argument('--case',choices=['qwen','sonnet','mini'],default='sonnet')
    p.add_argument('--verify',choices=['legacy','evidence','evidence_arbiter','evidence_v2','evidence_arbiter_v2','case_arbiter','reader_committee'],default='legacy')
    p.add_argument('--rubric',choices=['legacy','reader_task'],default='legacy')
    p.add_argument('--policy',choices=['legacy','relations_v2','relations_v3','relations_v4'],default='legacy')
    p.add_argument('--rewriter',default='standard');p.add_argument('--api-json',choices=['0','1'],default='0')
    p.add_argument('--all-reviewers',action='store_true');p.add_argument('--priority',choices=['all','corroborated'],default='all');p.add_argument('--audit',action='store_true');p.add_argument('--replay',help='Apply only the final brief check to a saved run on the same inputs')
    p.add_argument('--parallel',type=int,default=5);p.add_argument('--limit',type=int);p.add_argument('--id',action='append',dest='ids')
    asyncio.run(run(p.parse_args()))
