import pyarrow.parquet as pq,json,re,random,hashlib
from pathlib import Path
import sys
out=Path(sys.argv[3])
patterns={
 'legal':r'юридическ(?:им языком|и грамотно|ом стиле|им стилем|и корректно)|юридически[ -]формальн',
 'formal':r'более (?:официальн|формальн|делов)|в (?:официальн[оы][ -]делов|деловом стиле|официальном стиле)',
 'direct':r'(?:более |по)?ж[её]стче|более ж[её]стк|(?:более )?строже|в ж[её]стком стиле',
 'warm':r'дружелюбнее|помягче|более (?:дружелюбн|мягк|дружеск)|по[ -]дружески|менее официальн'}
rng=random.Random(2711);pools={k:[] for k in patterns};seen=set()
for part,filename in enumerate(sys.argv[1:3]):
 pos=0
 for batch in pq.ParquetFile(filename).iter_batches(batch_size=128,columns=['conversation_hash','conversation','model','toxic','redacted']):
  for row in batch.to_pylist():
   pos+=1
   if row['toxic'] or row['redacted'] or row['conversation_hash'] in seen:continue
   c=row['conversation']
   for i in range(2,len(c)-1):
    if [m['role'] for m in c[i-2:i+2]]!=['user','assistant','user','assistant']:continue
    q,a,b,v=[m['content'] for m in c[i-2:i+2]]
    if not (3<=len(b)<=150 and 70<len(a)<3000 and 50<len(v)<3300 and len(q)<1000):continue
    group=next((k for k,p in patterns.items() if re.search(p,b,re.I)),None)
    if not group or not re.search('[а-яА-Яё]{3}',a):continue
    if re.search(r'http|@|\b\d{8,}|\+7\s*\d|порн|секс|интим|боеприпас|оруж|наркот|убий|взлом|наци|суицид|уязвимост|взрыв|изнасил|бомб|пытк|эротич|ракет',q+a+b+v,re.I):continue
    pools[group].append(dict(group=group,hash=row['conversation_hash'],model=row['model'],shard=part,shard_row=pos-1,turn=i-1,source_request=q,original=a,request=b,historical_rewrite=v,status='observed_request_and_completion_unrated'));seen.add(row['conversation_hash']);break
rows=[]
for k,items in pools.items():
 rng.shuffle(items);rows.extend(items[:30])
out.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows))
print({k:len(v) for k,v in pools.items()},len(rows))
