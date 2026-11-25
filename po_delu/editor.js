"use strict";
const $ = selector => document.querySelector(selector);
const form = $("#form"), fields = form.elements, result = $("#result"), statusBox = $("#status");
const reviewStatus = $("#review-status");
let mode = "text", revision = 0, active = null, generation = false;
let exampleRequest = 0, openedExample = null, ownSettings = null, reviewTimer = null;

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
  clearInterval(reviewTimer);
  $("#review").disabled = false;
  result.replaceChildren();
  statusBox.textContent = "";
  reviewStatus.textContent = "";
}
function selectMode(next) {
  reset();
  exampleRequest++;
  if (mode !== "showcase" && next === "showcase") {
    ownSettings = Object.fromEntries(["context","source","terms","length"].map(name => [name, fields.namedItem(name).value]));
    for (const name of ["context","source","terms"]) fields.namedItem(name).value = "";
    fields.namedItem("length").value = "short";
    openedExample = null;
    $("#showcase-choice").value = "";
    $("#showcase-preview").hidden = true;
    $("#showcase-status").textContent = "";
  } else if (mode === "showcase" && next !== "showcase" && ownSettings) {
    for (const [name, value] of Object.entries(ownSettings)) fields.namedItem(name).value = value;
    ownSettings = null;
  }
  fields.share_document.checked = false;
  fields.share_examples.checked = false;
  fields.analysis_mode.value = "local";
  updateConsent();
  mode = next;
  for (const id of ["text","file","showcase"]) {
    $("#" + id + "-pane").hidden = mode !== id;
    $("#tab-" + id).setAttribute("aria-pressed", String(mode === id));
  }
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
function findingCard(job, f, personal, reviewCase) {
  const b = job.blocks[Number(f.block_id)], card = el("article");
  const allow = b.edit && (personal ? f.kind !== "exception" : reviewCase?.group === "flag");
  card.append(selection("finding", f.id, b.label, allow), contextLine(b, f));
  if (personal) {
    card.append(el("strong", f.kind === "exception" ? "Оставлять эту фразу" : f.kind === "variant" ? "Похоже на личный пример: "+f.before+" → "+f.after : "Предпочитаю: " + f.after));
    if (f.rationale) card.append(el("p", f.rationale));
    if (f.note) card.append(el("p", f.note));
  } else {
    if (reviewCase?.priority_reason) card.append(el("p", reviewCase.priority_reason));
    if (reviewCase?.note) card.append(el("p", reviewCase.note, "hint"));
    card.append(el("strong", f.pattern));
    if (f.effect && f.effect !== f.pattern) card.append(el("p", f.effect));
    if (f.normal) card.append(el("p", f.normal, "hint"));
    if (reviewCase?.question) card.append(el("p", reviewCase.question));
    if (reviewCase?.arbitration) card.append(el("p", reviewCase.arbitration));
    if (reviewCase?.material) {
      const material = el("details");
      material.append(el("summary", "Материал для проверяющих"));
      if (reviewCase.scope) material.append(el("p", reviewCase.scope, "hint"));
      for (const [title, claim, evidence] of reviewCase.material) {
        const row = el("div", "", "ballot");
        row.append(el("strong", title), el("p", claim), el("small", evidence)); material.append(row);
      }
      card.append(material);
    }
    if (reviewCase?.votes.length) {
      const details = el("details");
      details.append(el("summary","Голоса проверяющих (" + reviewCase.votes.length + ")"));
      for (const vote of reviewCase.votes) {
        const ballot = el("div", "", "ballot"), reasons = el("dl");
        ballot.append(el("strong", vote.title));
        for (const [label, value] of vote.reasons) reasons.append(el("dt", label), el("dd", value));
        ballot.append(reasons);
        details.append(ballot);
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
      editorial_total:job.editorial_total, coverage:job.coverage, coverage_summary:job.coverage_summary, errors:job.errors,
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
  statusBox.textContent = "";
  const heading = el("h2", "Замечания");
  heading.id = "review-results"; heading.tabIndex = -1;
  const view = job.review_view, overview = el("div", "", "overview" + (view.incomplete ? " incomplete" : ""));
  overview.append(el("strong", view.state), el("p", job.name + " · " + job.backend), el("p", view.coverage));
  for (const line of [...view.channels, ...view.notes]) overview.append(el("small", line));
  result.append(heading, overview);
  result.append(reports(job));
  const groups = Object.entries(view.titles).map(([group, title]) =>
    ({group, title, cases: view.cases.filter(c => c.group === group)})).filter(g => g.cases.length);
  if (groups.length > 1) {
    const nav = el("nav", "", "result-links"); nav.setAttribute("aria-label", "Разделы замечаний");
    for (const {group, title, cases} of groups) {
      const link = el("a", title + " (" + cases.length + ")"); link.href = "#results-" + group; nav.append(link);
    }
    result.append(nav);
  }
  for (const {group, title, cases} of groups) {
    const h = el("h3", title + " (" + cases.length + ")"); h.id = "results-" + group; h.tabIndex = -1;
    result.append(h);
    for (const item of cases) result.append(findingCard(job, job[item.collection][item.index], false, item));
  }
  if (job.personal_matches?.length) {
    section("Совпадения с личными правилами");
    for (const f of job.personal_matches) result.append(findingCard(job, f, true));
    if (job.personal_total > job.personal_matches.length) result.append(el("p","Показаны первые "+job.personal_matches.length+" совпадений из "+job.personal_total+"."));
  }
  if (job.personal_example_count && job.detector !== "offline") {
    const agreement = selection("personal-consent", "", "Разрешаю передать модели этот черновик и мои личные примеры для поиска похожих фраз.", true);
    const consent = agreement.querySelector("input");
    const search = button("Найти похожие фразы", async () => {
      if (!consent.checked) return;
      const seq = ++revision;
      statusBox.textContent = "Модель сопоставляет черновик с личными примерами…";
      const updated = await (await jsonRequest("/personal-check/"+job.token,{share_examples:consent.checked})).json();
      if (seq === revision) show(updated,rewritten);
    });
    search.disabled = true;
    consent.onchange = () => { search.disabled = !consent.checked; };
    result.append(agreement, search);
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
      if (seq === revision) { show(updated, true); $("#changes").focus(); }
    }, "primary");
    go.disabled = true;
    for (const n of result.querySelectorAll(".finding")) n.onchange = () => {go.disabled = !checked("finding").length;};
    result.append(go);
  }
  if (rewritten) {
    const h = el("h2", "Предлагаемые замены"); h.id = "changes"; h.tabIndex = -1; result.append(h);
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
for (const id of ["text","file","showcase"]) {
  $("#tab-" + id).onclick = () => { if (mode !== id) selectMode(id); };
}
function showExampleSlide() {
  const page = Number($("#showcase-page").value), seq = exampleRequest;
  const img = el("img");
  img.id = "showcase-image"; img.alt = "Слайд " + (page + 1); img.hidden = true;
  $("#showcase-image").replaceWith(img);
  $("#slide-status").textContent = "Открываем слайд…";
  img.onload = () => {
    if (seq !== exampleRequest || !img.isConnected) return;
    img.hidden = false;
    $("#slide-status").textContent = "";
  };
  img.onerror = () => {
    if (seq !== exampleRequest || !img.isConnected) return;
    $("#slide-status").textContent = "Предпросмотр слайда недоступен. Текст приведён ниже.";
    $("#showcase-preview details").open = true;
  };
  img.src = "/examples/" + encodeURIComponent(openedExample.name) + "/slides/" + page;
}
$("#showcase-page").onchange = showExampleSlide;
const savedDialog = $("#saved-showcase");
$("#close-saved").onclick = () => savedDialog.close();
savedDialog.onclose = () => $("#saved-content").replaceChildren();
$("#open-saved").onclick = () => {
  const example = openedExample, record = example?.saved_review;
  if (!record || record.source_sha256 !== example.revision) return;
  const blocks = new Map(example.blocks.map(b => [b.id, b]));
  const content = $("#saved-content");
  content.replaceChildren();
  const date = record.date.split("-").reverse().join(".");
  content.append(el("p", example.name + " · " + date + " · Профиль " + record.profile));
  const coverage = "Проверено блоков: " + record.seen + " из " + record.total + ".";
  content.append(el("p", coverage + (record.search_errors ? " Сбоев поиска или привязки: " + record.search_errors + "." : ""), "hint"));
  const decisions = {flag:"Есть замечание", keep:"Оставить", uncertain:"Решение открыто"};
  for (const item of record.cases) {
    const b = blocks.get(item.block_id), card = el("article");
    card.append(el("h3", item.decision === "flag" ? "Подтверждённое замечание" : "Спорный фрагмент"),
      el("small", b.label), contextLine(b, item));
    const supporting = item.votes.find(v => v.status === "ok" && v.decision === item.decision);
    if (supporting) card.append(el("p", supporting.reason));
    else if (item.decision === "uncertain") card.append(el("p", "Голоса проверяющих не дали общего решения."));
    const details = el("details"); details.append(el("summary", "Голоса проверяющих (" + item.votes.length + ")"));
    for (const vote of item.votes) {
      const ballot = el("div", "", "ballot");
      const role = vote.role === "arbiter" ? "Арбитр" : "Проверяющий";
      ballot.append(el("strong", role + " · " + vote.family + " · " + (decisions[vote.decision] || "Оценка недоступна")));
      if (vote.reason) ballot.append(el("p", vote.reason));
      if (vote.evidence) ballot.append(el("small", blocks.get(vote.evidence.block_id).label + " · «" + vote.evidence.quote.trim() + "»"));
      details.append(ballot);
    }
    card.append(details); content.append(card);
  }
  for (const edit of record.edits) {
    const card = el("article");
    if (edit.status === "refused") {
      card.append(el("h3", "Замена отклонена проверкой"), el("small", blocks.get(edit.block_id).label), el("p", edit.reason));
    } else if (edit.status === "verified_native") {
      card.append(el("h3", "Отдельный прогон правки"), el("small", blocks.get(edit.block_id).label + " · " + edit.model + " · " + edit.profile),
        el("p", "Исходный абзац", "diff-label"), el("del", edit.before), el("p", "Вариант модели", "diff-label"), el("ins", edit.after),
        el("p", edit.editorial_note || "Сформированный файл повторно открыт и проверен."));
    }
    content.append(card);
  }
  savedDialog.showModal(); savedDialog.scrollTop = 0; $("#saved-title").focus();
};
$("#showcase-choice").onchange = async e => {
  reset();
  const seq = ++exampleRequest, name = e.target.value;
  openedExample = null;
  $("#showcase-preview").hidden = true;
  $("#showcase-status").textContent = name ? "Открываем документ…" : "";
  fields.context.value = fields.source.value = fields.terms.value = "";
  fields.share_document.checked = fields.share_examples.checked = false;
  fields.analysis_mode.value = "local"; updateConsent();
  fields.namedItem("length").value = name.endsWith(".pptx") ? "fit" : "short";
  if (!name) return;
  try {
    const url = "/examples/" + encodeURIComponent(name);
    const example = await (await send(url)).json();
    if (seq !== exampleRequest) return;
    const blocks = $("#showcase-blocks"); blocks.replaceChildren();
    for (const b of example.blocks) blocks.append(el("small", b.label), el("p", b.text));
    fields.context.value = example.brief;
    fields.share_document.checked = fields.share_examples.checked = false;
    $("#showcase-name").textContent = example.name;
    $("#showcase-download").href = url + "/download";
    $("#open-saved").hidden = !example.saved_review || example.saved_review.source_sha256 !== example.revision;
    $("#showcase-slides").hidden = !example.slides;
    $("#showcase-preview details").open = !example.slides;
    $("#showcase-preview").hidden = false;
    $("#showcase-status").textContent = "";
    openedExample = example;
    if (example.slides) {
      const pages = $("#showcase-page"); pages.replaceChildren();
      for (let i = 0; i < example.slides; i++) pages.add(new Option((i + 1) + " из " + example.slides, i));
      showExampleSlide();
    }
  } catch (error) {
    if (seq === exampleRequest) $("#showcase-status").textContent = error.message;
  }
};
function updateConsent() {
  const models = !$("#consent-panel").disabled && fields.analysis_mode.value === "models";
  $("#local-description").hidden = models;
  $("#document-agreement").hidden = !models;
  fields.share_document.required = models;
  if (!models) fields.share_document.checked = false;
}
updateConsent();
form.oninput = e => {
  if (!e.target.name) return;
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
  e.preventDefault();
  if ($("#review").disabled) return;
  reset();
  const seq = revision, payload = new FormData(form);
  if (mode === "file" && !fields.file.files.length) {reviewStatus.textContent = "Выберите файл."; fields.file.focus(); return;}
  if (mode === "text" && !fields.text.value.trim()) {reviewStatus.textContent = "Введите текст черновика."; fields.text.focus(); return;}
  if (mode === "showcase" && !openedExample) {reviewStatus.textContent = "Откройте пример для проверки."; $("#showcase-choice").focus(); return;}
  if (mode !== "file") payload.delete("file");
  if (mode !== "text") payload.delete("text");
  if (mode === "showcase") {
    payload.set("showcase", openedExample.name);
    payload.set("showcase_revision", openedExample.revision);
  }
  payload.delete("share_examples");
  payload.set("exact_terms", JSON.stringify(fields.terms.value.split(/\n/).map(s=>s.trim()).filter(Boolean)));
  $("#review").disabled = true;
  reviewStatus.textContent = fields.analysis_mode.value === "models" ? "Идёт проверка моделями…" : "Идёт проверка локальными правилами…";
  const started = Date.now(), timer = el("span");
  timer.setAttribute("aria-hidden", "true"); reviewStatus.append(timer);
  reviewTimer = setInterval(() => { timer.textContent = " " + Math.floor((Date.now() - started) / 1000) + " с"; }, 1000);
  try {
    const job = await (await send("/review",{method:"POST",body:payload})).json();
    if (seq === revision) {
      reviewStatus.textContent = "";
      show(job,false);
      $("#review-results").focus({preventScroll:true});
      $("#review-results").scrollIntoView({block:"start"});
    }
  } catch (error) {if (seq === revision) reviewStatus.textContent = error.message;}
  finally {if (seq === revision) {clearInterval(reviewTimer); $("#review").disabled = false;}}
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
