"""Compare fixed locations, free search and two-way rewrite acceptability, by source family."""
import argparse
from collections import Counter, defaultdict
import csv
import json
from pathlib import Path
import random
import statistics
from score_benchmark import matching

ROOT=Path(__file__).resolve().parents[1]
V=ROOT/'evaluation/v2'
KEYS={'dev-detect':'evaluation/lockbox/keys.jsonl','dev-contrast':'evaluation/v1/contrast_keys.jsonl','dev-meaning':'evaluation/rewrite_lockbox/keys.jsonl',
      'dev-writing':'evaluation/v1/rewrite_keys.jsonl','presentation-detect':'evaluation/v2/presentation_holdout/detect_keys.jsonl','audit2-detect':'evaluation/v2/audit2/detect_keys.jsonl','audit2-meaning':'evaluation/v2/audit2/rewrite_keys.jsonl','fresh-detect':'evaluation/v2/sealed/detect_keys.jsonl','fresh-meaning':'evaluation/v2/sealed/rewrite_keys.jsonl'}
def read(p):return [json.loads(s) for s in p.read_text().splitlines() if s.strip()]
def div(n,d):return n/d if d else None
def span_scores(cnt):
    n=cnt['positives']; m=cnt['delivered']; k=cnt['matched']
    return {'recall':div(k,n),'precision':div(k,m),'f1':div(2*k,n+m),'clean_ok':div(cnt['clean_no_flag'],cnt['clean'])}

def intervals(items,rec):
    return [(rec['block_offsets'][s['block_id']]+s['start'],rec['block_offsets'][s['block_id']]+s['end']) for s in items]
def gold_spans(key):
    if 'spans' in key:return [(s['start'],s['end']) for s in key['spans'] if s.get('decision','flag')=='flag'] if key['decision']=='flag' else []
    return [(key['start'],key['end'])] if key['decision']=='flag' else []
def doc_case(key,rec,task):
    d=Counter(documents=1)
    verdicts=rec.get('verdicts',[]);failed=bool(rec.get('failure'))
    row={'id':key['id'],'failed':failed}
    if task=='fixed':
        v=verdicts[0] if verdicts else {}
        decision=v.get('decision','unavailable') if v.get('quorum_met',True) else 'unavailable';expect=key['decision']
        d.update(correct=int(decision==expect),answered=int(bool(v) and decision!='unavailable'),arbiter=int(bool(v.get('arbiter_called'))))
        pair=v.get('without_arbiter');pair=pair.get('decision') if isinstance(pair,dict) else pair
        d['pair_correct']=int(pair==expect)
        row.update(expected=expect,decision=decision,pair=pair,correct=decision==expect)
    elif task in ['union','baseline','semantic','slopotron']:
        g=gold_spans(key); found=intervals([s for s in rec.get('findings',[]) if s['decision']=='flag'],rec)
        cand=intervals(rec.get('candidates',[]),rec);m=len(matching(found,g));before=len(matching(cand,g))
        errors=len(rec.get('errors',[]))
        complete=not failed and rec.get('seen',0)==rec.get('total',-1) and not errors and rec.get('consensus',{}).get('status') not in ['unavailable','partial','incomplete_search']
        clean=key['decision']=='keep';d.update(positives=len(g),delivered=len(found),matched=m,candidates=len(cand),proposal_matched=before,clean=int(clean),
            clean_no_flag=int(clean and not found and complete),clean_false_alarm=int(clean and bool(found)),complete=int(complete))
        row.update(gold=len(g),candidate_hits=before,tp=m,fp=len(found)-m,fn=len(g)-m,complete=complete,clean=clean)
    elif task=='fidelity':
        ref=key['variants'];pred={s['id']:s for s in rec.get('results',[])}
        for ident,k in ref.items():
            label=k['variant'];decision=pred.get(ident,{}).get('status','missing')
            d[label]+=1;d[label+'_'+decision]+=1
        row.update({k:v for k,v in d.items() if k!='documents'})
    else:
        offered=bool(rec.get('changes'));d['offered']=int(offered)
        row.update(expected=key.get('expected_action'),offered=offered,after=[i['after'] for i in rec.get('changes',[])],refusals=[i.get('reason') for i in rec.get('refusals',[])])
    return d,row

def group_ci(groups,task):
    if task not in ('fixed','union','baseline','semantic','slopotron'):return {}
    rng=random.Random(7218);values=list(groups.values());scores=[]
    for _ in range(1000):
        tally=Counter()
        for fam in rng.choices(values,k=len(values)):tally.update(fam)
        a=div(tally['correct'],tally['documents']) if task=='fixed' else span_scores(tally)['f1']
        if a is not None:scores.append(a)
    scores.sort()
    return {'metric':'accuracy' if task=='fixed' else 'f1','groups':len(groups),'CI95':[scores[int((len(scores)-1)*q)] for q in (.025,.975)]} if scores else {}
def summarize(folder):
    cfg=json.loads((folder/'config.json').read_text());key={r['id']:r for r in read(ROOT/KEYS[cfg['suite']])}
    saved=read(folder/'predictions.jsonl') if (folder/'predictions.jsonl').exists() else []
    byid={r['id']:r for r in saved}
    if len(byid)!=len(saved) or not byid.keys()<=cfg['inputs'].keys():raise ValueError('Invalid prediction IDs '+folder.name)
    groups=defaultdict(Counter);total=Counter();cases=[];times=[];stages=Counter()
    for i in cfg['inputs']:
        rec=byid.get(i,{'failure':'missing','block_offsets':{}})
        k=key[i];c,row=doc_case(k,rec,cfg['task']);group=k.get('group') or k.get('family') or i
        groups[group].update(c);total.update(c);cases.append({'run':folder.name,'group':group,**row})
        if 'elapsed_s'in rec:times.append(rec['elapsed_s'])
        stages.update(s.get('status','missing') for s in rec.get('stages',[]))
    stats={'task':cfg['task'],'suite':cfg['suite'],'recorded':len(saved),'planned':len(cfg['inputs']),'raw':dict(total),'stages':dict(stages),'median_seconds':round(statistics.median(times),2) if times else None}
    if cfg['task']=='fixed':stats.update(accuracy=div(total['correct'],total['documents']),all_versions_correct=sum(c['correct']==c['documents'] for c in groups.values()),groups=len(groups))
    if cfg['task'] in ('union','baseline','semantic','slopotron'):stats.update(span_scores(total))
    stats['uncertainty']=group_ci(groups,cfg['task'])
    return stats,cases
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--fresh',action='store_true');p.add_argument('--out',type=Path);args=p.parse_args();results={};docs=[]
    if args.fresh and not (V/'FREEZE.json').exists():raise ValueError('Freeze before inspecting held-out results')
    for folder in sorted((V/'runs').glob('*')):
        if not (folder/'config.json').exists():continue
        cfg=json.loads((folder/'config.json').read_text())
        if cfg['suite'].startswith(('fresh-','audit2-','presentation-')) and not args.fresh:continue
        s,cs=summarize(folder);results[folder.name]=s;docs+=cs
    if args.out:
        args.out.mkdir(exist_ok=True,parents=True);(args.out/'summary.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
        (args.out/'per_case.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in docs))
    for name,s in results.items():print(name,s['recorded'],'/',s['planned'],s.get('accuracy',s.get('f1','')),s['raw'])
