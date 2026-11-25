"""Fill missing documents from a compatible retry while preserving both raw attempts."""
import json
import sys
from pathlib import Path


def read(folder):
    return json.loads((folder/'config.json').read_text()), [json.loads(l) for l in (folder/'predictions.jsonl').read_text().splitlines()]


def merge(original,retry,out):
    cfg,a=read(original); rc,b=read(retry)
    for k in ('suite','task','model','search_model','panel','verify','rubric','policy','rewriter','api_json','case_model','priority','all_reviewers','brief_audit','audit_model'):
        if cfg.get(k)!=rc.get(k):raise ValueError('Config differs: '+k)
    rows={x['id']:x for x in a if not x.get('failure')}
    prompts={(s['stage'],s['model'],s['prompt_hash']) for x in a for s in x.get('stages',[])}
    for x in b:
        key=x['id']
        if key in rows or cfg['inputs'].get(key)!=rc['inputs'][key]:raise ValueError('Wrong retry '+key)
        for s in x['stages']:
            if (s['stage'],s['model'],s['prompt_hash']) not in prompts:raise ValueError('Unverified retry prompt: '+s['stage'])
        rows[key]=x
    if set(rows)!=set(cfg['inputs']):raise ValueError('Missing cases')
    out.mkdir(exist_ok=False)
    (out/'config.json').write_text(json.dumps({**cfg,'assembled_from':[original.name,retry.name],'retry_hashes':rc['hashes'],'retry_ids':[x['id'] for x in b],'replaced_failures':[x['id'] for x in a if x.get('failure')]},ensure_ascii=False,indent=2)+'\n')
    (out/'predictions.jsonl').write_text(''.join(json.dumps(rows[i],ensure_ascii=False)+'\n' for i in cfg['inputs']))
    print(f'{len(a)-sum(bool(x.get("failure")) for x in a)} completed originals + {len(b)} recovered = {len(rows)}')
if __name__=='__main__':merge(*(Path(x) for x in sys.argv[1:]))
