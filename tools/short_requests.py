"""Collect short, explicitly worded revision requests with an actual following assistant answer."""
import json,re,hashlib,sys
from pathlib import Path
from collections import Counter
import pyarrow.parquet as pq
small=[
('shorter',r'(?:сократи|сократите|покороче|короче|кратче|пократче|покороче пожалуйста|кратко|кратко и по делу|можно короче|можно покороче|сделай (?:это |его |этот текст |текст |ответ |немного |чуть )?(?:короче|покороче|кратче)|напиши (?:по)?короче|ещ[её] (?:короче|кратче)|сократи (?:это|ответ|текст|вдвое|в два раза))'),
('simpler',r'(?:проще|попроще|проще пожалуйста|можно проще|можно попроще|сделай (?:это |его |текст |ответ )?(?:проще|попроще)|объясни (?:попроще|проще|простыми словами)|(?:пере)?пиши (?:попроще|проще|простыми словами)|напиши (?:попроще|проще|простыми словами)|упрости(?: текст| ответ)?)'),
('specific',r'(?:конкретнее|поконкретнее|давай конкретнее|больше конкретики|можно (?:по)?конкретнее|приведи конкретный пример|напиши конкретнее)'),
('tone',r'(?:менее (?:формально|официально)|более (?:формально|официально|дружелюбно)|помягче|перепиши строже и лаконичней)')]
seen=set();rows=[]
for part,src in enumerate(sys.argv[1:3]):
 pos=0
 for batch in pq.ParquetFile(src).iter_batches(batch_size=150,columns=['conversation_hash','conversation','model','toxic','redacted']):
  for r in batch.to_pylist():
   pos+=1;c=r['conversation']
   if r['toxic'] or r['redacted']:continue
   for j in range(2,len(c)-1):
    if [m['role'] for m in c[j-2:j+2]]!=['user','assistant','user','assistant']:continue
    q,a,f,b=[m['content'] for m in c[j-2:j+2]]
    if len(q)>700 or not 80<=len(a)<=3500 or not 8<=len(b)<=3500 or len(f)>75:continue
    s=f.lower().strip(' .!?\n\t,'); s=re.sub(r'^(?:давай|пожалуйста|а теперь|а можно ли)\s*,?\s+','',s)
    k=next((k for k,p in small if re.fullmatch(p,s)),None)
    if not k or not re.search('[а-яА-Яё]{4}',a+b) or re.search(r'http|@|\b\d{8,}|\+7\s*\d|сексу|порн|убий|боеприпас|бомб|пытк|взрыв|оруж|наркот|суицид|взлом|интим|изнасил',q+a+b,re.I):continue
    h=hashlib.sha256(a.encode()).hexdigest()
    if h in seen:continue
    seen.add(h)
    rows.append(dict(id='R%05d'%(len(rows)+1),group=k,hash=r['conversation_hash'],model=r['model'],shard=part,shard_row=pos-1,turn=j-1,source_request=q,original=a,request=f,historical_rewrite=b,response_sha256=h));break
Path(sys.argv[3]).write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows))
print(Counter(r['group'] for r in rows),len(rows))
