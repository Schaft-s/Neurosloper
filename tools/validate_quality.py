"""Validate new input/reference boundaries, exact evidence coordinates and linked source hashes."""
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
def sha(raw):return hashlib.sha256(raw).hexdigest()
def index(p):
    rows=[json.loads(s) for s in p.read_text().splitlines() if s]
    indexed={r['id']:r for r in rows}
    if len(rows)!=len(indexed):raise ValueError('Duplicate document id: '+str(p))
    return indexed
def check(condition,msg):
    if not condition:raise ValueError(msg)

def verify_folder(d):
    inputs=index(d/'detect_inputs.jsonl');targets=index(d/'detect_targets.jsonl');keys=index(d/'detect_keys.jsonl')
    check(inputs.keys()==targets.keys()==keys.keys(),'Unequal detection inventory')
    for i,doc in inputs.items():
        source=doc['text'];t=targets[i];r=keys[i]
        check(set(doc)=={'id','text','request'},'Nonblind fields: '+i)
        for span in [t]+r['spans']:
            a,b=span['start'],span['end'];check(0<=a<b<=len(source) and source[a:b]==span['quote'],'Invalid evidence '+i)
        check(r['decision'] in {'flag','keep','uncertain'} and r['normal'] and r['reason'],i)
        if r['decision']=='keep':check(not r['spans'],i)
    groups={k['group'] for k in keys.values()}
    for group in groups:
        check({'flag','keep'}<={k['decision'] for k in keys.values() if k['group']==group},group)
    rewrites=index(d/'rewrite_inputs.jsonl');rk=index(d/'rewrite_keys.jsonl')
    check(rewrites.keys()==rk.keys(),'Unequal rewrite inventory')
    for i,doc in rewrites.items():
        r=rk[i];check(sha(doc['text'].encode())==r['source_sha256'],i)
        check(all(word in doc['text'] for word in doc['locked']),i)
        variants={v['id'] for v in doc['candidates']};check(variants==set(r['variants']) and len(variants)==2,i)
        check({v['expected'] for v in r['variants'].values()}=={'pass','reject'},i)
        check(all(v['after']!=doc['text'] and set(v)=={'id','after','block_id'} for v in doc['candidates']),i)
    for line in (d/'SHA256SUMS').read_text().splitlines():
        expected,file=line.split();check(sha((d/file).read_bytes())==expected,file)
    return {'documents':len(inputs),'groups':len(groups),'meaning_contracts':len(rewrites)},inputs,rewrites

if __name__=='__main__':
    past=[ROOT/'data/genre_blind.jsonl',ROOT/'evaluation/lockbox/inputs.jsonl',ROOT/'evaluation/v1/contrast_inputs.jsonl',
          ROOT/'evaluation/rewrite_lockbox/inputs.jsonl',ROOT/'evaluation/v1/fidelity_inputs.jsonl']
    seen={r['text'] for p in past for r in index(p).values()}
    for name in ['sealed','audit2']:
        d=ROOT/'evaluation/v2'/name;stats,inputs,rewrites=verify_folder(d)
        new={r['text'] for r in [*inputs.values(),*rewrites.values()]}
        check(not new&seen,'Previously used text: '+name)
        seen|=new
        print(name,json.dumps(stats,ensure_ascii=False))
    r=ROOT/'evaluation/v2'
    check(all(sha((r/'audit2'/n).read_bytes())==h for n,h in json.loads((r/'FINAL_REWRITE_GATE.json').read_text())['new_locked_files'].items()),'Second rewrite lock changed')
    for n in ['FREEZE','CONFIRMATION']:
        cfg=json.loads((r/(n+'.json')).read_text());d=r/('sealed' if n=='FREEZE' else 'audit2')
        st=cfg.get('reference_inputs_sha') or cfg['sealed_manifest'];check(st==sha((d/'SHA256SUMS').read_bytes()),n)
    print('Blinding, target anchors, split overlap and both frozen manifests verified.')
