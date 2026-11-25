"use strict";
const $ = selector => document.querySelector(selector);
const form = $("#form"), fields = form.elements, result = $("#result"), statusBox = $("#status");
let mode = "text", revision = 0, active = null, generation = false;

function el(tag, value = "", cls = "") {
  const node = document.createElement(tag);
  node.textContent = value;
  if (cls) node.className = cls;
  return node;
}
function section(title) { result.append(el("h2", title)); }
function button(label, action, cls = "") {
  const node = el("button", label, cls);
  node.type = "button";
  node.onclick = async () => {
    const jobAtClick = active;
    node.disabled = true;
    try { await action(); } catch (e) {
      if (active === jobAtClick) statusBox.textContent = e.message;
    } finally { node.disabled = false; }
  };
  return node;
}
async function send(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw Error(typeof body.detail === "string" ? body.detail : "Запрос не удалось выполнить.");
  }
  return response;
}
function jsonRequest(url, body) {
  return send(url, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)});
}
function download(blob, name) {
  const url = URL.createObjectURL(blob), a = el("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}
function reset() {
  revision++;
  active = null;
  generation = false;
  result.replaceChildren();
  statusBox.textContent = "";
}
function selectMode(next) {
  reset();
  fields.share_document.checked = false;
  fields.share_examples.checked = false;
  mode = next;
  $("#text-pane").hidden = mode !== "text";
  $("#file-pane").hidden = mode !== "file";
  for (const id of ["text","file"]) $("#tab-" + id).setAttribute("aria-pressed", String(mode === id));
}
function count() {
  const n = Array.from(fields.text.value).length;
  $("#count").textContent = n.toLocaleString("ru") + " " + ({one:"символ",few:"символа",many:"символов",other:"символа"})[new Intl.PluralRules("ru").select(n)];
}
function checked(kind) {
  return Array.from(result.querySelectorAll("." + kind + ":checked"), node => node.value);
}
function selection(kind, id, title, allowed) {
  const label = el("label", "", "check"), input = el("input");
  input.type = "checkbox";
  input.className = kind;
  input.value = id;
  input.disabled = !allowed;
  label.append(input, document.createTextNode(title));
  return label;
}
function contextLine(block, f) {
  const chars = Array.from(block.text);
  const p = el("p", "", "quote");
  p.append(document.createTextNode(chars.slice(Math.max(0, f.start - 80), f.start).join("")),
    el("mark", f.quote), document.createTextNode(chars.slice(f.end, f.end + 80).join("")));
  return p;
}
function findingCard(job, f, personal) {
  const b = job.blocks[Number(f.block_id)], card = el("article");
  const allow = b.edit && (personal ? f.kind !== "exception" : f.decision === "flag");
  card.append(selection("finding", f.id, b.label, allow), contextLine(b, f));
  if (personal) {
    card.append(el("strong", f.kind === "exception" ? "Оставлять эту фразу" : f.kind === "variant" ? "Похоже на личный пример: "+f.before+" → "+f.after : "Предпочитаю: " + f.after));
    if (f.rationale) card.append(el("p", f.rationale));
    if (f.note) card.append(el("p", f.note));
  } else {
    card.append(el("strong", f.pattern), el("p", f.effect), el("small", f.normal));
    const verdict = (job.verdicts || []).find(v => v.block_id === f.block_id && v.start === f.start && v.end === f.end);
    if (verdict?.votes?.length) {
      const details = el("details");
      details.append(el("summary","Голоса проверяющих"));
      const labels = {flag:"Есть замечание", keep:"Оставить", uncertain:"Нужен контекст"};
      for (const vote of verdict.votes) {
        details.append(el("p",(vote.family || vote.model)+" · "+(labels[vote.decision] || "Ответ недоступен")+
          (vote.effect ? ". "+vote.effect : "")));
      }
      card.append(details);
    }
  }
  if (!b.edit) card.append(el("p", "Доступно только для диагностики.", "hint"));
  return card;
}
function reports(job) {
  const bar = el("div", "", "actions");
  const kinds = [["docx", "Скачать отчёт DOCX"]];
  if ([".pdf", ".pptx"].includes(job.ext)) kinds.push(["pdf","Замечания на страницах PDF"]);
  for (const [kind, title] of kinds) {
    const a = el("a", title);
    a.href = "/report/" + job.token + "/" + kind;
    a.download = "findings." + kind;
    bar.append(a);
  }
  bar.append(button("Отчёт JSON", () => {
    const {token, blocks, trace, source, terms, preference_examples, personal_matches, ...diagnosis} = job;
    const data = {file:job.name, revision:job.revision_hash, backend:job.backend, seen:job.seen, total:job.total,
      consensus:diagnosis.consensus, findings:job.findings.map(f => ({...f, location:blocks[Number(f.block_id)].label})),
      verdicts:job.verdicts, stages:job.stages, refusals:job.refusals};
    download(new Blob([JSON.stringify(data,null,2)], {type:"application/json"}), "findings.json");
  }));
  return bar;
}
function exportButton(job, mode, title, withChanges) {
  return button(title, async () => {
    const ids = withChanges ? checked("patch") : [];
    const response = await jsonRequest("/export/"+job.token, {ids,mode});
    const name = response.headers.get("Content-Disposition")?.match(/filename="([^"]+)"/)?.[1];
    download(await response.blob(), name || "edited"+job.ext);
  }, withChanges && mode === "native" ? "primary" : "");
}
function preview(job) {
  const card = el("article"), row = el("div", "", "actions"), label = el("label","Страница или слайд"), num = el("input"), img = el("img");
  num.type = "number";
  num.min = 1;
  num.value = 1;
  label.append(num);
  card.append(label);
  for (const [title, modified] of [["Посмотреть оригинал",false],["Посмотреть правки",true]]) {
    row.append(button(title, async () => {
      const response = await jsonRequest("/preview/"+job.token+"/"+(Number(num.value)-1), {ids:modified ? checked("patch") : []});
      const url = URL.createObjectURL(await response.blob());
      img.onload = () => URL.revokeObjectURL(url);
      img.src = url;
      img.alt = "Предпросмотр страницы "+num.value;
    }));
  }
  card.append(row, img);
  return card;
}
function diffLine(block, change, updated) {
  const node = el(updated ? "ins" : "del");
  if (!change.diff?.length) {
    node.textContent = updated ? change.after : block.text;
    return node;
  }
  const source = Array.from(block.text);
  let cursor = 0;
  for (const part of change.diff) {
    node.append(document.createTextNode(source.slice(cursor,part.start).join("")));
    node.append(el("mark", updated ? part.after : source.slice(part.start,part.end).join("")));
    cursor = part.end;
  }
  node.append(document.createTextNode(source.slice(cursor).join("")));
  if (!node.textContent) node.textContent = "(удалить фрагмент)";
  return node;
}
function show(job, rewritten) {
  generation = rewritten;
  active = job;
  result.replaceChildren();
  statusBox.textContent = job.backend + " · Прочитано блоков: " + job.seen + " из " + job.total + ". " + job.summary;
  section("Замечания");
  const panel = job.consensus || {};
  if (panel.quorum) {
    const states = {complete:"голосование завершено",partial:"получена часть оценок",unavailable:"не удалось собрать кворум",no_candidates:"кандидатов для голосования нет"};
    result.append(el("p","Независимая проверка: "+(states[panel.status] || panel.status)+". Кворум: "+panel.quorum+
      ". Семейства: "+(panel.families?.join(", ") || "не настроены")+"."));
  }
  result.append(reports(job));
  const flags = job.findings.filter(f => f.decision === "flag");
  const uncertain = (job.verdicts || job.findings).filter(f => f.decision === "uncertain");
  if (!flags.length) {
    const incomplete = ["incomplete_search","partial","unavailable"].includes(panel.status) || job.seen < job.total;
    result.append(el("p", incomplete
      ? "Подтверждённых замечаний пока нет. Проверка выполнена частично."
      : "В прочитанном тексте подтверждённых замечаний нет."));
  }
  for (const f of flags) result.append(findingCard(job, f, false));
  if (uncertain.length) {
    result.append(el("h3","Требуют контекста"));
    for (const f of uncertain) result.append(findingCard(job, f, false));
  }
  const dissent = (job.verdicts || []).filter(v => v.decision === "keep" &&
    v.votes?.some(vote => vote.status === "ok" && vote.decision === "flag"));
  if (dissent.length) {
    const details = el("details");
    details.append(el("summary", "Оставлены большинством, есть возражения (" + dissent.length + ")"));
    for (const item of dissent) details.append(findingCard(job, item, false));
    result.append(details);
  }
  if (job.personal_matches?.length) {
    section("Совпадения с личными правилами");
    for (const f of job.personal_matches) result.append(findingCard(job, f, true));
    if (job.personal_total > job.personal_matches.length) result.append(el("p","Показаны первые "+job.personal_matches.length+" совпадений из "+job.personal_total+"."));
  }
  if (job.personal_example_count && job.detector !== "offline") {
    result.append(button("Передать модели текст и личные примеры для поиска похожих фраз", async () => {
      const seq = ++revision;
      statusBox.textContent = "Модель сопоставляет черновик с личными примерами…";
      const updated = await (await jsonRequest("/personal-check/"+job.token,{share_examples:true})).json();
      if (seq === revision) show(updated,rewritten);
    }));
  }
  if (job.personal_search) {
    result.append(el("h3","Смысловые совпадения с личными примерами"));
    const scan = job.personal_search;
    result.append(el("p","Найдено: "+job.personal_variants.length+". Запросов: "+scan.calls+" из "+scan.planned+". Ошибок: "+scan.errors+"."));
    for (const f of job.personal_variants) result.append(findingCard(job,f,true));
  }
  if (result.querySelector(".finding:not(:disabled)")) {
    const go = button("Предложить замены для выбранных", async () => {
      const seq = ++revision;
      statusBox.textContent = "Подбираем и проверяем замены…";
      const response = await jsonRequest("/rewrite/"+job.token, {ids:checked("finding"), style:fields.style.value,
        length:fields.namedItem("length").value, precision:fields.precision.value,
        terms:fields.terms.value.split(/\n/).map(s=>s.trim()).filter(Boolean), share_examples:fields.share_examples.checked});
      const updated = await response.json();
      if (seq === revision) { show(updated, true); $("#changes")?.scrollIntoView({block:"start"}); }
    }, "primary");
    go.disabled = true;
    for (const n of result.querySelectorAll(".finding")) n.onchange = () => {go.disabled = !checked("finding").length;};
    result.append(go);
  }
  if (rewritten) {
    const h = el("h2", "Предлагаемые замены"); h.id = "changes"; result.append(h);
    if (job.rewrite_summary) result.append(el("p", job.rewrite_summary));
    if (!job.changes.length) result.append(el("p", "Подходящей замены нет."));
    for (const f of job.refusals || []) result.append(el("p", (job.blocks[Number(f.block_id)]?.label || "Фрагмент") + " · " + f.reason));
    for (const c of job.changes) {
      const b = job.blocks[Number(c.id)], card = el("article");
      card.append(el("strong", b.label), el("p","Было","diff-label"), diffLine(b,c,false), el("p","Стало","diff-label"),
        diffLine(b,c,true), el("p", c.reason), el("small", c.personal ? "Личный выбор. Независимая смысловая оценка этой пары отсутствует." : (c.verification?.reason || "")), selection("patch",c.id,"Принять эту замену",true));
      result.append(card);
    }
    const bar = el("div","","actions");
    if (job.changes.length) bar.append(exportButton(job,"native","Скачать с выбранными правками",true));
    bar.append(exportButton(job,"text","Скачать чистый текст",true));
    if (job.ext === ".pdf") bar.append(exportButton(job,"annotate","PDF с комментариями к заменам",true));
    result.append(bar);
  } else {
    const bar = el("div","","actions");
    bar.append(exportButton(job,"native","Скачать исходный файл",false), exportButton(job,"text","Скачать чистый текст",false));
    result.append(bar);
  }
  if ([".pdf",".pptx"].includes(job.ext)) result.append(preview(job));
  const trace = el("details");
  trace.append(el("summary","Ход проверки"),el("pre",JSON.stringify({consensus:job.consensus,stages:job.stages,trace:job.trace},null,2)));
  result.append(trace);
}
$("#tab-text").onclick = () => selectMode("text");
$("#tab-file").onclick = () => selectMode("file");
function updateConsent() {
  const models = !$("#consent-panel").disabled && fields.analysis_mode.value === "models";
  $("#local-description").hidden = models;
  $("#document-agreement").hidden = !models;
  fields.share_document.required = models;
  if (!models) fields.share_document.checked = false;
}
updateConsent();
form.oninput = e => {
  if (["analysis_mode","text","file","format","context","source"].includes(e.target.name)) {
    fields.share_document.checked = false;
    fields.share_examples.checked = false;
    updateConsent();
  }
  if (["style","length","precision","terms","share_examples"].includes(e.target.name)) {
    if (active) {
      revision++;
      if (generation) show({...active,changes:[]},false);
    }
  } else reset();
  if (e.target === fields.text) count();
};
fields.file.onchange = () => {
  selectMode("file");
  if (fields.file.files[0]?.name.toLowerCase().endsWith(".pptx")) fields.namedItem("length").value = "fit";
};
$("#example").onclick = () => {
  selectMode("text");
  fields.format.value = "txt";
  fields.text.value = "В рамках мероприятия будет осуществляться проведение демонстрации станка.";
  count(); fields.text.focus();
};
form.onsubmit = async e => {
  e.preventDefault(); reset();
  const seq = revision, payload = new FormData(form);
  if (mode === "file" && !fields.file.files.length) {statusBox.textContent = "Выберите файл."; return;}
  if (mode === "text" && !fields.text.value.trim()) {statusBox.textContent = "Введите текст черновика."; fields.text.focus(); return;}
  if (mode === "text") payload.delete("file"); else payload.delete("text");
  payload.delete("share_examples");
  payload.set("exact_terms", JSON.stringify(fields.terms.value.split(/\n/).map(s=>s.trim()).filter(Boolean)));
  statusBox.textContent = "Идёт проверка…";
  try {
    const job = await (await send("/review",{method:"POST",body:payload})).json();
    if (seq === revision) {show(job,false); statusBox.scrollIntoView({block:"start"});}
  } catch (error) {if (seq === revision) statusBox.textContent = error.message;}
};
const memory = $("#memory"), rules = $("#rules"), ruleForm = $("#rule-form"), ruleStatus = $("#rule-status");
function renderRules(items) {
  rules.replaceChildren();
  if (!items.length) rules.append(el("p", "Личных правил пока нет.", "hint"));
  for (const item of items) {
    const line = el("div","","entry");
    line.append(el("strong",item.before), el("p",item.kind === "example" ? "Предпочитаю: "+item.after : "Оставлять эту фразу"));
    if (item.note) line.append(el("small",item.note));
    const del = el("button","Удалить");
    del.type = "button";
    del.onclick = async () => {
      try {renderRules(await (await send("/preferences/"+item.id,{method:"DELETE"})).json()); ruleStatus.textContent = "Запись удалена."; reset();}
      catch (e) {ruleStatus.textContent = e.message;}
    };
    line.append(el("div")); line.lastChild.append(del); rules.append(line);
  }
}
$("#open-memory").onclick = async () => {
  ruleStatus.textContent = ""; rules.replaceChildren();
  memory.showModal();
  try {renderRules(await (await send("/preferences")).json());}
  catch (e) {ruleStatus.textContent = e.message;}
};
$("#close-memory").onclick = () => memory.close();
const toggleAfter = () => {
  const exception = ruleForm.elements.kind.value === "exception";
  $("#rule-after").hidden = exception; ruleForm.elements.after.required = !exception;
};
ruleForm.elements.kind.onchange = toggleAfter;
ruleForm.onsubmit = async e => {
  e.preventDefault();
  try {
    renderRules(await (await jsonRequest("/preferences",Object.fromEntries(new FormData(ruleForm)))).json());
    ruleForm.reset(); toggleAfter(); ruleStatus.textContent = "Запись сохранена."; reset();
  } catch (error) {ruleStatus.textContent = error.message;}
};
