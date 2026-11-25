"""Rebuild the normalized evidence tables from the editorial snapshot and the filtered WildChat candidates."""
import collections, hashlib, json, re, sys
from pathlib import Path
from urllib.parse import urljoin
from lxml import html
base=Path(__file__).resolve().parents[1]/'data'
def dump(name,rows):
    (base/name).write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows))
source='https://en.wikipedia.org/w/index.php?oldid=1387041075'
rows=[]
for i,b in enumerate(html.parse(sys.argv[1]).xpath('//blockquote[.//cite]')):
    cite=b.xpath('.//cite')[0]
    url=next((urljoin(source,s) for s in cite.xpath('.//a/@href') if re.search(r'(Special:(Diff|PermanentLink)/|oldid=|diff=)\d+',s)),None)
    if not url:continue
    credit=' '.join(cite.text_content().split())
    label=b.xpath('preceding::*[self::h2 or self::h3 or self::h4][1]')[0].text_content()
    cite.drop_tree()
    # Map each non-whitespace character to its source mark before normalizing whitespace.
    marks=b.xpath('.//mark'); owners=[]; chunks=[]
    def add(t,m):
        if t:chunks.append(t);owners.extend([m]*len(t))
    def walk(e,m=None):
        if e.tag=='mark':m=marks.index(e)
        add(e.text,m)
        for child in e:
            if isinstance(child.tag,str):walk(child,m)
            add(child.tail,m)
    walk(b);raw=''.join(chunks);tokens=list(re.finditer(r'\S+',raw))
    text=' '.join(t.group() for t in tokens);offset=0;hits=collections.defaultdict(list)
    for token in tokens:
        for j,m in enumerate(owners[token.start():token.end()]):
            if m is not None:hits[m].append(offset+j)
        offset+=len(token.group())+1
    spans=[]
    for j,idx in sorted(hits.items()):
        a,z=idx[0],idx[-1]+1; q=' '.join(marks[j].text_content().split())
        assert text[a:z]==q
        spans.append(dict(start=a,end=z,quote=q))
    if spans:rows.append(dict(id='WE%03d'%i,text=text,spans=spans,revision_url=url,guide=source,category=label,attribution=credit,license='CC BY-SA 4.0',scope='partial_editorial_highlights'))
dump('wiki_full_marks.jsonl',rows)
# Editorial screening of actual follow-ups. Separate stated dissatisfaction from preferences.
groups={'explicit_feedback':{1,3,7,12,27,35,36,45,56,57,58,59,68,70,73,80,83,91,93},'rewrite_brief':{2,19,22,25,29,32,33,42,55,61,65,72,79,81,85,86,90,92}}
prior={json.loads(l)['conversation_hash'] for l in (base/'ru_annotated.jsonl').read_text().splitlines()}
feedback=[]
for r in map(json.loads,Path(sys.argv[2]).open()):
    n=int(r['id'][1:]);label=next((k for k,v in groups.items() if n in v),None)
    if label:feedback.append(dict(**r,label=label,overlaps_catalog=r['hash'] in prior,revision='3e57f16cd6a3660f4e4083d70e9e66abd634e704',url='https://huggingface.co/datasets/ZeroAgency/ru-wildchat-v1/viewer/default/train?row='+str(r['row']+43564*r['shard'])))
dump('ru_user_feedback.jsonl',feedback)
print('Wiki',len(rows),len(set(r['revision_url'] for r in rows)),sum(len(r['spans']) for r in rows),len(set(r['category'] for r in rows)))
print('Feedback',collections.Counter(r['label'] for r in feedback),sum(r['overlaps_catalog'] for r in feedback))
