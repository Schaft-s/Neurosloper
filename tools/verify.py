import csv, hashlib,json
from collections import Counter
from pathlib import Path
root=Path(__file__).resolve().parents[1];data=root/'data'
def read(s):return [json.loads(l) for l in (data/(s+'.jsonl')).read_text().splitlines()]
wiki=read('wiki_full_marks');ru=read('ru_annotated');fr=read('ru_user_feedback');brief=read('genre_blind');pro=read('genre_provenance');styles=read('style_tasks');checks=read('fidelity_keys');hist=read('real_revision_requests');reg=read('register_pairs')
for r in wiki:
    for s in r['spans']:assert 0<=s['start']<s['end']<=len(r['text']) and r['text'][s['start']:s['end']]==s['quote']
for r in fr+hist:assert hashlib.sha256((r.get('text') or r['original']).encode()).hexdigest()==r['response_sha256']
assert len(brief)==len(set(r['id'] for r in brief))==90
assert [r['id'] for r in brief]==[r['id'] for r in pro]
seen={r['hash'] for r in fr+reg+hist}|{r['conversation_hash'] for r in ru+read('random40_reviews')}
assert not seen&{r['hash'] for r in pro}
assert len(styles)==32 and len(checks)==8
for row in styles:assert all(x in row['source'] for x in row['locked'])
assert all(r['source']!=r['invalid_rewrite'] for r in checks)
votes=list(csv.DictReader((data/'slopfinder_votes.csv').open()))
assert list(map(int,(r['vote_id'] for r in votes)))==list(range(1,86))
def counts(rows,k):return ', '.join(f'{x} — {n}' for x,n in Counter(r[k] for r in rows).items())
lines=['# Инвентаризация подготовленных данных','',f'* Wiki — {len(wiki)} фрагментов, {len(set(r["revision_url"] for r in wiki))} ревизий, {sum(len(r["spans"]) for r in wiki)} проверенных символьных интервалов, {len(set(r["category"] for r in wiki))} категорий.',
f'* WildChat, предыдущий каталог — {len(ru)} диалогов. Естественная обратная связь — {len(fr)} ({counts(fr,"label")}). Пересекается с каталогом по диалогу — {sum(r["overlaps_catalog"] for r in fr)}.',
f'* SlopFinder — {len(votes)} голосов по {len(set(r["content_hash"] for r in votes))} текстам, {sum(float(r["slop_score"])<0 for r in votes)} отрицательных голосов, {sum(float(r["slop_score"])<=-.5 for r in votes)} оценок ≤ −0,5.',
f'* Русские черновики для будущей двойной разметки — {len(pro)} ({counts(pro,"genre")}). Пересечение по диалогу с приложенными разработческими наборами — 0.',
f'* Реальные запросы на переделку — {len(hist)} ({counts(hist,"group")}); дополнительно узкий корпус смены регистра — {len(reg)} ({counts(reg,"group")}). Возможны пересечения — по conversation hash, в узком корпусе поле hash.',
f'* Контрольные задания для генерации — {len(styles)}; смысловых контрактов и отрицательных вариантов — {len(checks)}. Русская редакторская очередь страниц — {len(read("ru_wiki_annotation_queue"))}.',
'','Размеры разных таблиц не суммировать: разная единица наблюдения и есть пересечения.']
(root/'COUNTS.md').write_text('\n'.join(lines)+'\n')
