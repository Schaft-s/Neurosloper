"""Build the private, standalone editorial corpus catalogue. No backend or web assets."""

import argparse
import csv
import difflib
import json
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

from score_benchmark import (
    blocks_for,
    digest,
    index,
    load_lockbox,
    read_rows,
    validate_quote,
)

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {"mail-05", "report-27"}


def read_table(path):
    return index(read_rows(path))


def safe_link(value):
    parsed = urlparse(value)
    if parsed.scheme not in ("https", "http") or not parsed.hostname:
        raise ValueError("Invalid source URL")
    return value


def editorial(source, text):
    spans = []
    for span in source.get("spans", []):
        validate_quote(text, span["start"], span["end"], span["quote"])
        spans.append(
            {
                k: span[k]
                for k in (
                    "quote",
                    "start",
                    "end",
                    "decision",
                    "reason",
                    "normal",
                    "pattern",
                )
                if k in span
            }
        )
    return {
        "decision": source["decision"],
        "reason": source.get("reason", ""),
        "spans": spans,
    }


def genre(root, v):
    notes = {
        label: read_table(path)
        for label, path in [
            ("A", v / "review_a.jsonl"),
            ("B", root / "evaluation/review_b.jsonl"),
            ("Итог", v / "adjudicated.jsonl"),
        ]
    }
    log = defaultdict(list)
    for entry in read_rows(v / "adjudication_log.jsonl"):
        log[entry["id"]].append(entry)
    data = []
    for doc in read_rows(root / "data/genre_blind.jsonl"):
        ident = doc["id"]
        if ident in EXCLUDED:
            data.append(
                {
                    "id": ident,
                    "title": "Исключённый документ",
                    "layer": "genre",
                    "labels": ["excluded"],
                    "excluded": True,
                }
            )
            continue
        reviews = {k: editorial(rows[ident], doc["text"]) for k, rows in notes.items()}
        # Exact boundaries and decisions, independent of editorial wording.
        signatures = [
            {(s["start"], s["end"], s["decision"]) for s in reviews[k]["spans"]}
            for k in ("A", "B")
        ]
        data.append(
            {
                "id": ident,
                "layer": "genre",
                "title": doc["request"].strip().splitlines()[0],
                "request": doc["request"],
                "text": doc["text"],
                "reviews": reviews,
                "labels": [reviews["Итог"]["decision"]],
                "disagreement": signatures[0] != signatures[1]
                or reviews["A"]["decision"] != reviews["B"]["decision"],
                "notes": [
                    {
                        "id": e.get("review_b_span"),
                        "action": e["action"],
                        "reason": e["reason"],
                    }
                    for e in log[ident]
                ],
                "conclusions": notes["Итог"][ident].get("adjudication", []),
            }
        )
    return data


def external(v):
    data = []
    for row in read_rows(v / "external.jsonl"):
        versions = []
        for side in ("before", "after"):
            source = row[side]
            versions.append(
                {
                    "name": "До правки" if side == "before" else "После правки",
                    "text": source["text"],
                    "url": safe_link(source["url"]),
                    "line": source["line"],
                    "sha": source["sha"],
                    **editorial(source, source["text"]),
                }
            )
        credit = row["source"]
        data.append(
            {
                "id": row["id"],
                "layer": "external",
                "title": row["title"],
                "request": row["function_brief"],
                "context": row["context"],
                "versions": versions,
                "labels": sorted({v["decision"] for v in versions}),
                "reason": row["reason"],
                "normal": row["normal"],
                "preference": row["preference"],
                "edit_type": row["edit_type"],
                "repository": row["repository"],
                "scope": "Локальный фрагмент",
                "credit": " · ".join(
                    [
                        credit["project_credit"],
                        "Правка — " + credit["editor"],
                        *credit["coauthors"],
                    ]
                ),
                "links": [
                    ["Коммит", safe_link(credit["diff_url"])],
                    ["Рецензирование", safe_link(credit["pr_url"])],
                    [credit["license"], safe_link(credit["license_at_revision"])],
                ],
            }
        )
    return data


def diagnostic(inputs, keys, layer):
    records = []
    for ident, doc in inputs.items():
        key = keys[ident]
        text = doc["text"]
        target = doc["target"]
        # The prospective loader has verified the fixed anchor; the diagnostic key supplies absolute coordinates.
        if "absolute" in target:
            start, end = target["absolute"]
        else:
            start, end = key["start"], key["end"]
        validate_quote(text, start, end, target["quote"])
        spans = key.get(
            "spans", [{"start": start, "end": end, "quote": target["quote"]}]
        )
        marks = []
        for s in spans:
            validate_quote(text, s["start"], s["end"], s["quote"])
            marks.append(
                dict(
                    s,
                    decision=s.get("decision", key["decision"]),
                    reason=s.get("reason", key["reason"]),
                )
            )
        if not marks:
            marks.append(
                {
                    "start": start,
                    "end": end,
                    "quote": target["quote"],
                    "decision": "keep",
                    "reason": key["reason"],
                }
            )
        cluster = key.get("cluster", key["family"])
        records.append(
            {
                "layer": layer,
                "id": ident,
                "title": doc["request"].splitlines()[0],
                "request": doc["request"],
                "text": text,
                "labels": [key["decision"]],
                "group": cluster,
                "family": key["family"],
                "reason": key["reason"],
                "normal": key.get("normal", ""),
                "spans": marks,
            }
        )
    return records


def change_spans(before, after):
    matcher = difflib.SequenceMatcher(None, before, after, autojunk=False)
    return [
        {"start": j1, "end": j2, "quote": after[j1:j2], "decision": "change"}
        for op, _, _, j1, j2 in matcher.get_opcodes()
        if op != "equal" and j2 > j1
    ]


def fidelity(v, layer="fidelity"):
    prefix = "fidelity_" if layer == "fidelity" else ""
    inputs = read_table(v / (prefix + "inputs.jsonl"))
    keys = read_table(v / (prefix + "keys.jsonl"))
    result = []
    for ident, doc in inputs.items():
        key = keys[ident]
        variants = []
        for c in doc["candidates"]:
            solution = key["variants"][c["id"]]
            variants.append(
                {
                    "id": c["id"],
                    "decision": solution["expected"],
                    "variant": solution["variant"],
                    "text": c["after"],
                    "spans": change_spans(doc["text"], c["after"]),
                }
            )
        result.append(
            {
                "layer": layer,
                "id": ident,
                "title": doc["text"],
                "text": doc["text"],
                "request": doc["request"],
                "atoms": key.get("atoms", []),
                "error": key["error"],
                "variants": variants,
                "labels": sorted({v["decision"] for v in variants}),
                "locked": doc.get("locked", []),
            }
        )
    return result


def reader_feedback(root, v):
    source = read_table(root / "data/ru_user_feedback.jsonl")
    items = []
    for key in read_rows(v / "reader_feedback.jsonl"):
        doc = source[key["id"]]
        text = doc["text"]
        feedback = doc["user_feedback"]
        if (
            digest(text) != key["source_sha256"]
            or digest(feedback) != key["user_feedback_sha256"]
        ):
            raise ValueError("Historical response/comment hash differs")
        quote = key.get("possible_original_quote")
        places = []
        if quote and text.count(quote) == 1:
            start = text.index(quote)
            places.append(
                {
                    "start": start,
                    "end": start + len(quote),
                    "quote": quote,
                    "decision": "signal",
                }
            )
        items.append(
            {
                "layer": "reader",
                "id": doc["id"],
                "title": doc["request"],
                "request": doc["request"],
                "text": text,
                "feedback": feedback,
                "claim": key["claim"],
                "evidence": key["direct_evidence"],
                "labels": [key["feedback_type"]],
                "spans": places,
                "scope": "Частичное наблюдение читателя",
            }
        )
    return items


def edits(v, layer, outputs):
    keys = read_table(v / "rewrite_keys.jsonl") if layer == "rewrite" else {}
    items = []
    for doc in read_rows(v / (layer + "_inputs.jsonl")):
        key = keys.get(doc["id"], {})
        text = doc["text"]
        if key and digest(text) != key["source_sha256"]:
            raise ValueError("Rewrite input hash differs")
        t = doc["target"]
        base, n = blocks_for(text)[str(t["block_id"])]
        local = text[base : base + n]
        if key:
            a, b = key["allowed_start"], key["allowed_end"]
        else:
            if local.count(t["quote"]) != 1:
                raise ValueError("Ambiguous selected location")
            a = base + local.index(t["quote"])
            b = a + len(t["quote"])
        validate_quote(text, a, b, t["quote"])
        runs = []
        for out in outputs[doc["id"]]:
            if out["original"] != text:
                raise ValueError("Result belongs to another source")
            runs.append(
                {
                    "run": out["run"],
                    "status": out["status"],
                    "text": out["candidate"],
                    "reason": out["reason"],
                    "spans": change_spans(text, out["candidate"])
                    if out["candidate"]
                    else [],
                }
            )
        labels = [doc["style"]] if layer == "style" else [key["expected_action"]]
        labels += sorted({r["status"] for r in runs})
        items.append(
            {
                "id": doc["id"],
                "title": text,
                "layer": layer,
                "text": text,
                "request": doc["request"],
                "style": doc["style"],
                "length": doc["length"],
                "locked": doc["locked"],
                "labels": labels,
                "spans": [
                    {"start": a, "end": b, "quote": t["quote"], "decision": "selected"}
                ],
                "atoms": key.get("meaning_atoms", []),
                "reference": key.get("reference", ""),
                "runs": runs,
            }
        )
    return items


def build(root):
    v = root / "evaluation/v1"
    contrasts = read_table(v / "contrast_inputs.jsonl")
    fixed = read_table(v / "contrast_targets.jsonl")
    for k, doc in contrasts.items():
        doc["target"] = fixed[k]["target"]
    data = (
        genre(root, v)
        + external(v)
        + diagnostic(contrasts, read_table(v / "contrast_keys.jsonl"), "contrast")
        + fidelity(v)
        + fidelity(v.parent / "rewrite_lockbox", "rewriteholdout")
    )
    extra = load_lockbox(root)
    if extra:
        data += diagnostic(*extra, "lockbox")
    data += reader_feedback(root, v)
    outputs = defaultdict(list)
    scores = v / "scores/rewrite.csv"
    if scores.is_file():
        with scores.open(encoding="utf-8", newline="") as stream:
            for row in csv.DictReader(stream):
                outputs[row["case"]].append(row)
    for layer in ("rewrite", "style"):
        data += edits(v, layer, outputs)
    return data


HTML = r"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'none'; img-src 'none'; base-uri 'none'; form-action 'none'">
<title>Каталог разметки · По делу</title>
<style>
[hidden]{display:none!important}
:root{color-scheme:light;--ink:#172c33;--green:#155648;--line:#d0dcd7;--tint:#f0f6f1;--muted:#506665;--paper:#fffefa;--warm:#f7e6bd;--radius:8px}*{box-sizing:border-box}body{margin:0;background:#f2f5f1;color:var(--ink);font:16px/1.55 Inter,"Segoe UI",Arial,sans-serif}button,input,select{font:inherit}button,select{cursor:pointer}button:focus-visible,a:focus-visible,input:focus-visible,select:focus-visible{outline:3px solid #337dbe;outline-offset:2px}header{background:#153e38;color:white;padding:22px clamp(15px,3.4vw,60px) 18px;display:flex;align-items:baseline;justify-content:space-between;gap:12px}header h1{font:clamp(26px,4vw,41px)/1.15 "DM Serif Text",Georgia,serif;margin:0}header span{color:#d3e6de;font-size:14px}.layers{display:flex;flex-wrap:wrap;gap:6px;background:var(--paper);padding:12px clamp(15px,3.4vw,60px);border-bottom:1px solid var(--line)}button{border:1px solid var(--line);border-radius:var(--radius);background:white;color:var(--ink);padding:5px 10px}.layers button[aria-pressed=true],.seg button[aria-pressed=true]{background:var(--green);border-color:var(--green);color:white}.workspace{display:grid;grid-template-columns:minmax(225px,300px) minmax(0,1fr);gap:18px;max-width:1700px;margin:0 auto;padding:18px clamp(12px,2.4vw,42px)}aside,main{min-width:0}aside{position:sticky;top:12px;align-self:start;background:var(--paper);border:1px solid var(--line);border-radius:var(--radius);overflow:hidden}.filter{padding:14px;display:grid;gap:8px}.filter label{font-size:14px;font-weight:600;display:block}.filter label input[type=search],select{display:block;margin-top:4px;width:100%;padding:7px 9px;border:1px solid #afc2b7;border-radius:6px;background:white;color:var(--ink)}.filter .check{font-weight:400}.count{margin:0;color:var(--muted);font-size:13px}.docs{max-height:57vh;overflow:auto;border-top:1px solid var(--line)}.doc{display:block;width:100%;text-align:left;border:0;border-bottom:1px solid var(--line);border-radius:0;padding:9px 13px;background:transparent;line-height:1.35}.doc[aria-current=true]{background:#e4eee6;box-shadow:inset 4px 0 var(--green)}.doc small{display:block;font:12px/1.4 ui-monospace,monospace;color:var(--green)}.doc span{display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;overflow:hidden}.panel{border:1px solid var(--line);border-radius:var(--radius);background:var(--paper);padding:clamp(12px,2vw,30px);margin-bottom:12px}.panel h2,.panel h3,.panel h4,.panel p{margin:0 0 9px}.panel h2{font:clamp(22px,2.7vw,34px)/1.22 "DM Serif Text",Georgia,serif;overflow-wrap:anywhere}.panel h3{font-size:19px}.panel h4{font-size:16px}.context{white-space:pre-wrap;overflow-wrap:anywhere}.cap{margin-bottom:4px;color:var(--muted);font-size:13px;font-weight:600}.meta,.seg,.rowtags,.linkrow{display:flex;flex-wrap:wrap;gap:7px;align-items:center;margin-bottom:10px}.ident{font:12px/1.5 ui-monospace,monospace;color:var(--muted)}.tag{font-size:12px;font-weight:600;display:inline-block;border-radius:15px;background:#e4ebe8;padding:2px 9px}.tag.flag{background:#f5d9c9;color:#6a351d}.tag.uncertain{background:#fdecc4;color:#694e12}.tag.keep,.tag.pass{background:#d9eddf;color:#25523c}.tag.reject{background:#f6d9d7;color:#75312b}.text{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.62;padding:12px 15px;border-left:3px solid var(--line);background:#f8faf6}.text mark{color:inherit;padding:1px 0;background:#f7d2b8;border-radius:2px}.text mark.uncertain{background:#f6e7a6}.text mark.keep{background:#d0e7d8}.text mark.change{background:#d6e4f5}.locations{margin-top:16px}.locations article{padding:10px 0;border-bottom:1px solid var(--line)}.locations article:last-child{border:0}.locations blockquote{white-space:pre-wrap;overflow-wrap:anywhere;margin:5px 0;padding-left:12px;border-left:2px solid #bc8838}.versions{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.versions .panel{padding:14px}.panel a{color:var(--green)}.note{font-size:14px;color:var(--muted)}details{margin:8px 0}summary{cursor:pointer;font-weight:600}ul{padding-left:22px}button.jump{font:12px/1.4 ui-monospace,monospace}.highlight{outline:3px solid #b0751d}.noresult{padding:15px}.sronly{position:absolute;clip:rect(0,0,0,0);width:1px;height:1px;overflow:hidden}@media(max-width:760px){.workspace{grid-template-columns:minmax(0,1fr);gap:12px}aside{position:static}.docs{max-height:175px}.filter{grid-template-columns:1fr 1fr}.filter .count{align-self:center}.versions{grid-template-columns:1fr}header{align-items:center}.layers button{font-size:14px}}@media(max-width:450px){.filter{grid-template-columns:1fr}.workspace{padding:8px}header span{display:none}}
</style></head><body>
<header><h1>Каталог разметки</h1><span>По делу · Внутренняя проверка</span></header>
<nav id="layers" class="layers" aria-label="Слои корпуса"></nav>
<div class="workspace"><aside><div class="filter">
<label>Поиск<input id="query" type="search" placeholder="ID, фраза или основание"></label>
<label>Решение<select id="decision"></select></label>
<label class="check" id="diffbox"><input type="checkbox" id="diff"> Есть расхождение A / B</label>
<p class="count" id="count" role="status"></p></div><nav id="docs" class="docs" aria-label="Документы"></nav></aside><main id="main" aria-label="Выбранный документ"></main></div>
<script type="application/json" id="records">__DATA__</script><script>__APP__</script></body></html>"""
APP = r""""use strict";
const records=JSON.parse(document.querySelector("#records").textContent);
const layers={genre:"Жанровые тексты",external:"Опубликованные правки",contrast:"Контрасты",fidelity:"Сохранение смысла",rewriteholdout:"Новые смысловые пары",lockbox:"Перспективный набор",reader:"Отзывы читателей",rewrite:"Правка участка",style:"Регистры речи"};
const names={flag:"Пересмотреть",keep:"Оставить",uncertain:"Неопределённо",excluded:"Исключён",pass:"Допустимо",reject:"Отклонить",change:"Изменено",signal:"Возможное место",selected:"Выбранный участок",style_objection:"Возражение по стилю",style_request:"Просьба о стиле",mixed:"Смешанный отзыв",factual_or_task_error:"Ошибка в факте или задаче",offer:"Ожидается замена",refuse:"Ожидается отказ",offered:"Замена предложена",refused:"Система отказала",voice:"Сохранить голос",direct:"Прямо",formal:"Официально",friendly:"Дружелюбно"};
const prefs={before:"До правки",after:"После правки",either:"Обе допустимы",neither:"Обе требуют правки"};
const variants={identity:"Без изменения",equivalent:"Эквивалентный вариант",corrupt:"Изменён смысл"};
const $=q=>document.querySelector(q);
const element=(tag,txt,cls)=>{const x=document.createElement(tag);if(txt!==undefined)x.textContent=txt;if(cls)x.className=cls;return x};
const append=(p,t,s,cls)=>{const x=element(t,s,cls);p.append(x);return x};
const link=(p,label,url)=>{const a=append(p,"a",label);a.href=url;a.target="_blank";a.rel="noopener noreferrer";return a};
const markLabel=(p,decision)=>append(p,"span",names[decision]||decision,"tag "+decision);
const grouping=new Map(Object.keys(layers).map(k=>[k,records.filter(x=>x.layer===k)]));
const lookup=new Map(records.map(x=>[x.layer+"/"+x.id,x]));
const searchIndex=new Map(records.map(d=>[d,JSON.stringify(d).toLocaleLowerCase("ru")]));
const route=new URLSearchParams(location.hash.slice(1));
let layer=grouping.get(route.get("layer"))?.length?route.get("layer"):"genre";
let active=lookup.get(layer+"/"+route.get("id"))||null;
let reviewer="Итог";
function actionButton(container,label,on,selected){
 const b=append(container,"button",label);b.type="button";b.addEventListener("click",on);
 if(selected!==undefined)b.setAttribute("aria-pressed",String(selected));return b;
}
function panel(parent,title){const p=append(parent,"section",undefined,"panel");if(title)append(p,"h3",title);return p}
function paragraph(p,label,value){if(!value)return;append(p,"div",label,"cap");append(p,"p",value,"context")}
function labelledText(parent,text,spans=[]){
 const block=append(parent,"div",undefined,"text");
 const chars=Array.from(text),cuts=new Set([0,chars.length]);
 for(const s of spans){cuts.add(s.start);cuts.add(s.end)}
 const bounds=Array.from(cuts).sort((a,b)=>a-b), marks=new Map();
 for(let i=1;i<bounds.length;i++){
  const a=bounds[i-1],b=bounds[i],part=chars.slice(a,b).join("");
  const covering=spans.map((s,j)=>[s,j]).filter(([s])=>s.start<b&&a<s.end);
  if(!covering.length){block.append(document.createTextNode(part));continue}
  const item=append(block,"mark",part,covering[0][0].decision);
  item.title=covering.map(([s])=>names[s.decision]||s.decision).join(" / ");
  for(const [,j] of covering)if(!marks.has(j))marks.set(j,item);
 }
 return marks;
}
function intervals(parent,spans,marks){
 if(!spans.length)return;
 const box=append(parent,"div",undefined,"locations");append(box,"h4","Размеченные места · "+spans.length);
 spans.forEach((s,j)=>{
  const item=append(box,"article"),meta=append(item,"div",undefined,"meta");
  markLabel(meta,s.decision);
  const run=actionButton(meta,"["+s.start+", "+s.end+")",()=>{
   const m=marks.get(j);if(!m)return;
   document.querySelectorAll(".highlight").forEach(el=>el.classList.remove("highlight"));
   m.classList.add("highlight");m.scrollIntoView({block:"center"});
  });run.className="jump";run.title="Перейти к месту в исходнике";
  append(item,"blockquote",s.quote);
  if(s.pattern)append(item,"strong",s.pattern);
  if(s.reason)append(item,"p",s.reason);
  if(s.normal)paragraph(item,"Обычное объяснение",s.normal);
 });
}
function showGenre(root,d){
 const p=panel(root,"Исходный текст"),seg=append(p,"div",undefined,"seg");
 for(const k of ["A","B","Итог"])actionButton(seg,k==="Итог"?k:"Редактор "+k,()=>{reviewer=k;details()},reviewer===k);
 const r=d.reviews[reviewer],meta=append(p,"div",undefined,"meta");
 markLabel(meta,r.decision);append(meta,"span","Мест: "+r.spans.length,"note");
 const marked=labelledText(p,d.text,r.spans);paragraph(p,"Основание",r.reason);
 intervals(p,r.spans,marked);
 const review=panel(root,"Согласование");
 const meta2=append(review,"div",undefined,"meta");
 for(const k of ["A","B","Итог"]){append(meta2,"strong",k);markLabel(meta2,d.reviews[k].decision)}
 for(const note of d.conclusions||[])append(review,"p",typeof note==="string"?note:JSON.stringify(note));
 if(d.notes?.length){
  const actions={same:"Совпало"};
  const list=append(review,"ul");
  for(const n of d.notes){const li=append(list,"li");
   append(li,"strong",(typeof n.id==="number"?"B · место "+(n.id+1)+" · ":"")+(actions[n.action]||"Решение при согласовании")+" ");li.append(document.createTextNode(n.reason))}
 } else if(!d.conclusions.length)append(review,"p","Отдельных решений о согласовании в журнале нет.","note");
}
function showExternal(root,d){
 const columns=append(root,"div",undefined,"versions");
 for(const v of d.versions){const p=panel(columns,v.name);markLabel(p,v.decision);const marks=labelledText(p,v.text,v.spans);
  intervals(p,v.spans,marks);link(p,"Строка "+v.line+" · "+v.sha.slice(0,10),v.url)}
 const p=panel(root,"Редакторское сравнение");paragraph(p,"Предпочтение",prefs[d.preference]);paragraph(p,"Что менялось",d.edit_type);
 paragraph(p,"Основание",d.reason);paragraph(p,"Обычное объяснение",d.normal);
 paragraph(p,"Атрибуция",d.credit);const links=append(p,"div",undefined,"linkrow");for(const [title,url] of d.links)link(links,title,url);
}
function showDiagnostic(root,d){
 const p=panel(root,"Исходный текст");const marks=labelledText(p,d.text,d.spans);intervals(p,d.spans,marks);
 if(d.normal)paragraph(p,"Обычное объяснение",d.normal);
 const mates=grouping.get(layer).filter(x=>x.group===d.group&&x.id!==d.id);
 if(mates.length){const other=panel(root,"Другие версии сценария");
  for(const x of mates)actionButton(other,x.id+" · "+names[x.labels[0]],()=>{resetFilters();active=x;refresh()})}
}
function showFidelity(root,d){
 const p=panel(root,"Исходный текст");labelledText(p,d.text);append(p,"h4","Смысловые требования");const list=append(p,"ul");
 for(const x of d.atoms)append(list,"li",x);if(d.locked.length)paragraph(p,"Защищённые выражения",d.locked.join(" · "));
 const v=panel(root,"Варианты");paragraph(v,"Проверяемое искажение",d.error);
 for(const item of d.variants){const box=append(v,"article",undefined,"locations"),meta=append(box,"div",undefined,"meta");
  markLabel(meta,item.decision);append(meta,"span",variants[item.variant]||item.variant);append(meta,"span",item.id,"ident");
  labelledText(box,item.text,item.spans)}
}
function showReader(root,d){
 const p=panel(root,"Ответ, который получил читатель");
 labelledText(p,d.text,d.spans);if(d.spans.length)append(p,"p","Цветом отмечено возможное место, к которому относится отзыв. Точные границы читатель не размечал.","note");
 const f=panel(root,"Последующий комментарий читателя");labelledText(f,d.feedback);
 paragraph(f,"Цитата-основание",d.evidence);paragraph(f,"Интерпретация замечания",d.claim);
}
function showEdits(root,d){
 const p=panel(root,"Исходный текст и выбранный участок");labelledText(p,d.text,d.spans);
 paragraph(p,"Регистр",names[d.style]||d.style);if(d.locked.length)paragraph(p,"Защищённые выражения",d.locked.join(" · "));
 if(d.atoms.length){append(p,"h4","Смысловые требования");const ul=append(p,"ul");for(const a of d.atoms)append(ul,"li",a)}
 if(d.reference)paragraph(p,"Контрольная формулировка",d.reference);
 const out=panel(root,"Результаты сохранённых прогонов");
 if(!d.runs.length)append(out,"p","Для этой записи нет сохранённого результата.","note");
 for(const r of d.runs){const a=append(out,"article",undefined,"locations"),m=append(a,"div",undefined,"meta");
  append(m,"strong",r.run);markLabel(m,r.status);if(r.text)labelledText(a,r.text,r.spans);
  paragraph(a,"Причина, записанная системой",r.reason)}
}
function details(){
 const main=$("#main");main.replaceChildren();
 if(!active){append(panel(main),"p","По выбранным условиям ничего не найдено.");return}
 const d=active,p=panel(main);const meta=append(p,"div",undefined,"meta");
 append(meta,"span",d.id,"ident");for(const l of d.labels)markLabel(meta,l);
 if(d.repository)append(meta,"span",d.repository,"ident");if(d.scope)append(meta,"span",d.scope,"note");
 if(d.group)append(meta,"span","Группа: "+d.group,"note");
 append(p,"h2",d.excluded?"Исключённый документ":d.layer==="genre"?({slides:"Презентация",mail:"Письмо",report:"Отчёт"}[d.id.split("-")[0]]||d.id):d.layer==="external"?d.title:layers[layer]);
 if(d.excluded){append(p,"p","Документ исключён из просмотрового набора. Здесь сохранён только его ID.");return}
 paragraph(p,layer==="external"?"Задача отрывка":"Запрос",d.request);
 if(d.context)paragraph(p,"Контекст",d.context);
 if(layer==="genre")showGenre(main,d);else if(layer==="external")showExternal(main,d);
 else if(layer==="fidelity"||layer==="rewriteholdout")showFidelity(main,d);else if(layer==="reader")showReader(main,d);
 else if(layer==="rewrite"||layer==="style")showEdits(main,d);else showDiagnostic(main,d);
}
function renderLayerButtons(){
 const host=$("#layers");host.replaceChildren();
 for(const [k,items] of grouping){if(!items.length)continue;
  actionButton(host,layers[k]+" · "+items.length,()=>{layer=k;active=null;reviewer="Итог";resetFilters();selectors();refresh()},layer===k)}
}
function selectors(){
 const sel=$("#decision"),chosen=sel.value;sel.replaceChildren();
 const options=["",...new Set(grouping.get(layer).flatMap(d=>d.labels))];
 for(const k of options){const op=append(sel,"option",k?names[k]:"Все решения");op.value=k}
 if(options.includes(chosen))sel.value=chosen;$("#diffbox").hidden=layer!=="genre";
}
function resetFilters(){$("#query").value="";$("#decision").value="";$("#diff").checked=false}
function refresh(){
 const items=grouping.get(layer),q=$("#query").value.trim().toLocaleLowerCase("ru"),label=$("#decision").value;
 const visible=items.filter(d=>(!q||searchIndex.get(d).includes(q))&&(!label||d.labels.includes(label))&&
       (!$("#diff").checked||layer!=="genre"||d.disagreement));
 if(!visible.includes(active))active=visible[0]||null;
 $("#count").textContent="Показано "+visible.length+" из "+items.length;
 const list=$("#docs");list.replaceChildren();
 for(const d of visible){const btn=actionButton(list,"",()=>{active=d;reviewer="Итог";refresh()});btn.className="doc";
  btn.dataset.id=d.id;btn.setAttribute("aria-current",String(active===d));append(btn,"small",d.id);append(btn,"span",d.title)}
 if(!visible.length)append(list,"p","Нет совпадений.","noresult");
 renderLayerButtons();details();
 if(active){try{history.replaceState(null,"","#"+new URLSearchParams({layer,id:active.id}))}catch(_){}}
}
$("#query").addEventListener("input",refresh);$("#decision").addEventListener("change",refresh);$("#diff").addEventListener("change",refresh);
selectors();refresh();
"""


def write_catalog(root, output):
    data = build(root)
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    payload = (
        payload.replace("&", r"\u0026").replace("<", r"\u003c").replace(">", r"\u003e")
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    html = HTML.replace("__APP__", APP).replace("__DATA__", payload)
    output.write_text(html, encoding="utf-8")
    return len(data)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    output = args.out or args.root / "evaluation/v1/Каталог.html"
    print(f"{output} · {write_catalog(args.root, output)} записей")
