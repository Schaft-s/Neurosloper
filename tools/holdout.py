"""Reserve unseen WildChat text drafts by request genre; layout and cross-source OOD require native documents."""
import hashlib,json,re,sys
from pathlib import Path
import pyarrow.parquet as pq
root=Path(__file__).resolve().parents[1]/'data'
def read(p):return [json.loads(l) for l in p.read_text().splitlines()]
skip={r['conversation_hash'] for r in read(root/'ru_annotated.jsonl')}
skip.update(r['hash'] for r in read(root/'ru_user_feedback.jsonl')+read(root/'register_pairs.jsonl'))
for r in read(root/'random40_blind.jsonl'):skip.add(r['id'].removeprefix('RU-'))
patterns={'slides':r'слайд|презентаци','mail':r'письм[оа]|электронн\w+ письм','report':r'отч[её]т|доклад'}
items={k:[] for k in patterns}
ban=re.compile(r'эроти|секс|наркот|суицид|порн|оруж|взрыв|самоуб|взлом|убий|уязвимост|пароль|нарушени. защит|ракет|депресс|боеприпас|войн|президент|мошенн|изнасил|интим|https?://|\b\d{8,}|[\w.+-]+@[\w.-]+|\+7\s*\d|\[redacted',re.I)
for shard,src in enumerate(sys.argv[1:3]):
 pos=0
 for batch in pq.ParquetFile(src).iter_batches(batch_size=192,columns=['conversation_hash','conversation','model','toxic','redacted']):
  for r in batch.to_pylist():
   pos+=1;c=r['conversation'];h=r['conversation_hash']
   if h in skip or len(c)<2 or r['toxic'] or r['redacted'] or [m['role'] for m in c[:2]]!=['user','assistant']:continue
   q,t=[m['content'] for m in c[:2]]
   if not 12<len(q)<650 or not 200<len(t)<5800 or ban.search(q+t) or not re.search(r'состав|подготов|напиш|сдела|придум|созда|напис|сформир|сгенер',q,re.I):continue
   g=[k for k,v in patterns.items() if re.search(v,q,re.I)]
   if len(g)!=1 or not re.search('[а-яА-Яё]{4}',t):continue
   k=g[0]
   if k=='slides' and len(re.findall('слайд',t,re.I))<2:continue
   if k=='mail' and not re.search('уважаем|здравствуйте|добрый|привет|дорог',t,re.I):continue
   if k=='report' and not re.search('отч[её]т|доклад|введение',t,re.I):continue
   items[k].append(dict(hash=h,model=r['model'],shard=shard,shard_row=pos-1,genre=k,request=q,text=t));skip.add(h)
selected=[]; used=set()
for k,pool in items.items():
 n=0
 for r in sorted(pool,key=lambda x:hashlib.sha256(('podelu/holdout/3'+x['hash']).encode()).digest()):
  sig=re.sub(r'\W','',r['text'].lower())
  if sig in used:continue
  used.add(sig);n+=1;r['id']=k+'-%02d'%n;selected.append(r)
  if n==30:break
 print(k,len(pool),n)
for name,records in [('genre_blind',({k:r[k] for k in ['id','request','text']} for r in selected)),('genre_provenance',({k:v for k,v in r.items() if k not in ['request','text']} for r in selected))]:
 (root/(name+'.jsonl')).write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in records))
