from pathlib import Path
import json,sys,random
base=Path(__file__).resolve().parents[1]/'data'
out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)
rows=[json.loads(s) for s in (base/'genre_blind.jsonl').read_text().splitlines()]
for i in [1,2]:
    order=rows[:];random.Random(440+i).shuffle(order)
    with (out/f'reviewer_{i}.jsonl').open('x') as f:
        for r in order:f.write(json.dumps(dict(**r,reviewer=i,review_status='pending',findings=None),ensure_ascii=False)+'\n')
