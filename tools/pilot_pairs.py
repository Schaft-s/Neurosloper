"""Prepare blinded editorial comparisons. Record abstention without inventing a rewrite."""
import argparse
import hashlib
import html
import json
from pathlib import Path


def read(path):
    return {r['id']:r for r in (json.loads(line) for line in path.read_text().splitlines() if line.strip())}


def example(row):
    if row.get('failure'):
        return {'status':'error','after':None}
    offers=row.get('changes',[])
    return {'status':'offer','after':offers[0]['after']} if offers else {'status':'abstain','after':None}


def main():
    p=argparse.ArgumentParser()
    p.add_argument('inputs',type=Path)
    p.add_argument('first',type=Path)
    p.add_argument('second',type=Path)
    p.add_argument('output',type=Path)
    p.add_argument('--seed',default='po-delu-v2-register')
    a=p.parse_args()
    raw,first,second=map(read,(a.inputs,a.first,a.second))
    if first.keys()!=second.keys() or not first.keys()<=raw.keys():raise ValueError('Incomplete comparison')
    a.output.mkdir(parents=True,exist_ok=True)
    rows=[];keys=[]
    ids=sorted(first,key=lambda i:hashlib.sha256((a.seed+'order'+i).encode()).digest())
    for idx,i in enumerate(ids):
        case=raw[i];key=hashlib.sha256((a.seed+i).encode()).hexdigest()[:12]
        sides=['first','second'] if idx%2==0 else ['second','first']
        options={k:example(v[i]) for k,v in [('first',first),('second',second)]}
        rows.append({'id':key,'brief':case['request'],'original':case['text'],'style':case.get('style','voice'),
                     'quote':case['target']['quote'],'locked':case.get('locked',[]),'A':options[sides[0]],'B':options[sides[1]]})
        keys.append({'id':key,'source_id':i,'A':sides[0],'B':sides[1]})
    for name,data in [('inputs',rows),('key',keys)]:
        (a.output/(name+'.jsonl')).write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in data))
    cards=[]
    for n,r in enumerate(rows):
        def option(v):return html.escape(v['after']) if v['status']=='offer' else 'Предложение отсутствует.'
        cards.append(f'<article data-id="{r["id"]}"><h2>{n+1}. {html.escape(r["brief"])}</h2><p>Исходник: {html.escape(r["original"])}</p><p>Выбранная цитата: <mark>{html.escape(r["quote"])}</mark></p><p><b>A</b> {option(r["A"])}</p><p><b>B</b> {option(r["B"])}</p><label>Предпочтение <select><option value="">Выберите</option><option>A</option><option>B</option><option>оба</option><option>ни один</option></select></label> <label>Почему <input></label></article>')
    js="""document.querySelector('button').onclick=()=>{const vals=Array.from(document.querySelectorAll('article'),e=>({id:e.dataset.id,choice:e.querySelector('select').value,reason:e.querySelector('input').value}));const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(vals,null,2)],{type:'application/json'}));a.download='my-review.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000)}"""
    (a.output/'Слепая сверка.html').write_text('<!doctype html><html lang="ru"><meta charset=utf-8><title>Слепая сверка редакций</title><meta name=viewport content="width=device-width, initial-scale=1"><style>body{max-width:50rem;margin:2rem auto;padding:0 1rem;background:#faf8f2;color:#242824;font:17px/1.5 system-ui}article{padding:1em 0;border-top:1px solid #b5b6aa}h2{font-size:1.05em}mark{background:#f2dfa3}button,input,select{font:inherit}button{margin:2rem 0}</style><h1>Выбор редакции</h1><p>Сначала проверьте сохранность фактов. Затем решите, какая версия лучше устраняет замечание и подходит автору. Отказ может быть оправдан. Решения сохраняются в файл на вашем устройстве.</p>'+''.join(cards)+'<button>Скачать мои решения</button><script>'+js+'</script></html>')
if __name__=='__main__':main()
