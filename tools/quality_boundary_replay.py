"""Apply the deterministic v4 numeric-boundary veto to prior v3 decisions and audit the deltas."""
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]; V=ROOT/'evaluation/v2'
sys.path.insert(0,str(ROOT/'po_delu'))
from boundary_review import replay

def rows(p):return [json.loads(x) for x in p.read_text().splitlines()]
def main():
    released=[]
    for run,group in [('rel3-regression','sealed'),('rel3-independent','audit2')]:
        folder=V/'runs'/run
        output=V/'runs'/(run.replace('rel3','rel4')+'-replay');output.mkdir(exist_ok=True)
        config=json.loads((folder/'config.json').read_text())
        docs={x['id']:x for x in rows(V/group/'rewrite_inputs.jsonl')}
        records=[]
        for row in rows(folder/'predictions.jsonl'):
            doc=docs[row['id']]; candidates={x['id']:x for x in doc['candidates']}
            assert hashlib.sha256(doc['text'].encode()).hexdigest()==row['text_sha256']
            results=[]
            for original in row['results']:
                after=candidates[original['id']]['after']
                result=replay(doc['text'],after,original)
                if original['status']!='pass':assert result['status']!='pass'
                if original['status']!=result['status']:
                    released.append(dict(run=run,case=doc['id'],variant=original['id'],before=original['status'],after=result['status'],reason=result['reason']))
                results.append(result)
            records.append({**row,'results':results,'source_run':run})
        config.update(policy='relations_v4',offline_replay_from=run,extra_paid_calls=0,boundary_source=hashlib.sha256((ROOT/'po_delu/boundary_review.py').read_bytes()).hexdigest())
        (output/'config.json').write_text(json.dumps(config,ensure_ascii=False,indent=2)+'\n')
        (output/'predictions.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in records))
    (V/'boundary_changes.json').write_text(json.dumps(released,ensure_ascii=False,indent=2)+'\n')
    print(len(released),'changed decisions (no new model calls)')
if __name__=='__main__':main()
