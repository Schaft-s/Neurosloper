import pyarrow.parquet as pq,json,re,hashlib
from pathlib import Path
pat=r'слишком (?:общ|пафосн|банальн|сух|формальн|официальн|однообразн|шаблон|многослов|много (?:воды|лишн|повтор))|меньше (?:воды|пафоса|штамп|канцеляр|повтор)|без (?:воды|пафоса|клише|штампов|канцелярита|шаблонных фраз)|много воды|одна вода|канцелярит|избит\w* (?:фраз|выраж)|шаблонн\w* (?:фраз|ответ)|ты повторяешься|ты повторяешь одно|сплошная вода|звучит (?:слишком|неестественно)|излишн\w+ (?:пафос|формальн)|не повторяйся|перестань повторять|повторяешь одно и то же|слишком длинн|короче и (?:проще|понятн|по делу)|перепиши.{0,40}человеческ'
pat += r'|слишком (?:книжн|заумн|сложн|примитивн|громоздк|расплывчат|поверхностн|однотипн|неопредел|абстрактн|робот|много слов|замудрен|заезж|эмоциональн|наигранн|слащав)|очень (?:банальн|пафосн|обобщ[её]н|шаблонн|много воды)|(?:без|убери|убрать|избегай|меньше|много) (?:лишних слов|лишнего текста|вводных слов|общих фраз|повторений|эпитетов|банальностей|повторов|абстракций)|(?:ответ|текст|вариант) (?:банальный|поверхностный|одинаковый|однообразный)|одно и то же|(?:ты|вы) повторя|менее (?:официальн|формальн|банальн|пафосн)|(?:напиши|перепиши|звучит) (?:как робот|по.?человечески)|больше конкретики|меньше общих|это ни о ч[её]м|звучит как реклама|не лей воду'
skip=re.compile(r'эроти|секс|порн|наркот|суицид|самоуб|изнасил|боеприпас|оружи|взлом|пароль|депресс|нацист|мошенн|интим|\[redacted|[\w.+-]+@[\w.-]+|\+7\s*\d|\b\d{9,}|https?://|убий|пытк|полово|кров|насил|членом|пизд|хуй',re.I)
rows=[];seen=set()
for ix,part in enumerate(['first','second']):
 base=0
 for batch in pq.ParquetFile('/tmp/realset/'+part+'.parquet').iter_batches(batch_size=128,columns=['conversation_hash','model','conversation','toxic','redacted']):
  for pos,r in enumerate(batch.to_pylist(),base):
   if r['toxic'] or r['redacted']: continue
   c=r['conversation']
   for j in range(2,len(c)):
    if [x['role'] for x in c[j-2:j+1]]!=['user','assistant','user']:continue
    q,a,f=[x['content'] for x in c[j-2:j+1]]
    if not 5<=len(f)<=380 or not re.search(pat,f,re.I) or not 70<len(a)<4200 or len(q)>1350 or skip.search(q+a+f) or not re.search('[а-яА-Яё]{3}',a):continue
    h=hashlib.sha256(a.encode()).hexdigest()
    if h in seen:continue
    seen.add(h);rows.append(dict(id='F%04d'%(len(rows)+1),hash=r['conversation_hash'],model=r['model'],shard=ix,row=pos,turn=j-1,request=q,text=a,user_feedback=f,response_sha256=h));break
  base+=len(batch)
Path('/tmp/antiresearch/wide.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows))
print(len(rows))
