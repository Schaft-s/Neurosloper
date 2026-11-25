"""Join locally downloaded upstream votes.jsonl to the index by vote, sample, score and publisher hash."""
import argparse,csv,json
from pathlib import Path
parser=argparse.ArgumentParser()
parser.add_argument('votes',type=Path,help='original SlopFinder votes.jsonl downloaded from the linked bucket')
parser.add_argument('output',type=Path)
args=parser.parse_args()
root=Path(__file__).resolve().parents[1]/'data'
expected={int(r['vote_id']):r for r in csv.DictReader((root/'slopfinder_votes.csv').open())}
joined={}
for r in map(json.loads,args.votes.open()):
    ident=r['vote_id']
    if ident not in expected: continue
    ref=expected[ident]
    assert str(r['sample_id'])==ref['sample_id'] and r['content_hash']==ref['content_hash'] and float(r['slop_score'])==float(ref['slop_score'])
    assert r['content'] and ident not in joined
    joined[ident]=r
assert joined.keys()==expected.keys(),'Incomplete or different upstream version'
with args.output.open('x') as f:
    for k in sorted(joined):f.write(json.dumps(joined[k],ensure_ascii=False)+'\n')
