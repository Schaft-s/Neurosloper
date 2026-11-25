"""Human-readable diagnosis, available before any rewriting is requested."""

import io

import pymupdf
from docx import Document

from formats import render


DECISIONS = {"flag": "Есть замечание", "keep": "Оставить", "uncertain": "Решение открыто"}
CHANNELS = {"semantic": "Контекстный поиск", "slopotron": "Слопотрон"}


def vote_details(vote, locations):
    role = "Арбитр" if vote.get("role") == "arbiter" else "Проверяющий"
    model = vote.get("served_model") or vote.get("model") or vote.get("family") or ""
    label = " · ".join(filter(None, (role, model)))
    if vote.get("status") != "ok":
        return {"title": label + " · Оценка недоступна", "reasons": []}
    reasons = []
    used = set()

    def add(label, value):
        if isinstance(value, str) and value.strip() and value not in used:
            reasons.append([label, value])
            used.add(value)

    cited = set()
    for key in ("task_anchor", "fact_anchor", "evidence"):
        ref = vote.get(key)
        if ref and ref.get("quote"):
            location = locations.get(str(ref.get("block_id")), "Исходный текст")
            anchor = (location, "«" + ref["quote"] + "»")
            if anchor not in cited:
                reasons.append(list(anchor))
                cited.add(anchor)
    add("Обычное объяснение", vote.get("normal"))
    add("Возможный ущерб", vote.get("challenge"))
    add("Основание решения", vote.get("normal_test") or vote.get("effect"))
    add("Наблюдение", vote.get("pattern"))
    add("Эффект", vote.get("effect"))
    add("При замене", vote.get("counterfactual"))
    add("Что уточнить у автора", vote.get("question"))
    if vote.get("flip"):
        for caption, condition in zip(("Когда оставить", "Когда пересмотреть"), vote["flip"]):
            add(caption, condition)
    return {"title": label + " · " + DECISIONS[vote["decision"]], "reasons": reasons}


def audit_note(item, locations):
    audit = item.get("audit", {})
    if audit.get("status") == "withdrawn":
        witness = audit["evidence"]
        where = locations.get(witness["block_id"], "Исходный текст")
        if item["decision"] == "flag" or item.get("priority") == "author_review":
            model = audit.get("served_model") or audit.get("model")
            outcome = "Возражение дополнительной модели" + (f" ({model})" if model else "") + "."
        else:
            outcome = "Замечание снято после сверки."
        return f"{outcome} {audit['reason']} {where} · «{witness['quote'].strip()}»"
    if audit.get("status") == "unavailable":
        return "Дополнительная сверка с исходником не завершена."
    return ""


def review_view(job):
    blocks = job["blocks"]
    locations = {str(b["id"]): b["label"] for b in blocks}
    locations.update(brief="Бриф", source="ТЗ автора")
    panel = job.get("consensus") or {}
    local = job.get("detector") == "offline" or panel.get("status") == "offline"
    titles = {
        "flag": "Замечания локальных правил"
        if local
        else ("Подтверждённые замечания" if panel.get("quorum") else "Замечания поиска"),
        "author_review": "Разобрать автору",
        "context": "Нужен контекст автора",
        "no_quorum": "Недостаточно оценок",
        "uncertain": "Спорные фрагменты",
        "dissent": "Оставлены с возражениями",
    }
    cases, anchors = [], set()
    for collection in ("verdicts", "findings"):
        for index, item in enumerate(job.get(collection, [])):
            key = (item["block_id"], item["start"], item["end"])
            if key in anchors:
                continue
            anchors.add(key)
            group = item["decision"]
            votes = item.get("votes", [])
            undecided = [
                v for v in votes if v.get("status") == "ok" and v.get("decision") == "uncertain"
            ]
            question = item.get("question") or next(
                (v["question"] for v in undecided if v.get("question")), ""
            )
            if item.get("priority") == "author_review":
                group = "author_review"
            elif group == "uncertain" and item.get("quorum_met") is False:
                group = "no_quorum"
            elif (
                group == "uncertain"
                and item.get("audit", {}).get("status") != "withdrawn"
                and (question or any(v.get("flip") for v in undecided))
            ):
                group = "context"
            elif group == "keep":
                if not any(v.get("status") == "ok" and v.get("decision") != "keep" for v in votes):
                    continue
                group = "dissent"
            case = dict(
                collection=collection,
                index=index,
                group=group,
                votes=[vote_details(v, locations) for v in votes],
            )
            if item.get("priority") in {"confirmed", "author_review"} and item.get("priority_reason"):
                case["priority_reason"] = item["priority_reason"]
            if group == "context" and question:
                case["question"] = "Что уточнить у автора · " + question
            dossier = item.get("case_file") or {}
            if dossier.get("status") == "ok":
                material = []
                for key, title in (
                    ("harm", "Довод об ущербе"),
                    ("use", "Возможная функция"),
                    ("test", "Что различает версии"),
                ):
                    anchor = dossier["anchors"][key]
                    material.append(
                        [
                            title,
                            dossier["dossier"][key][0],
                            locations[str(anchor["block_id"])] + " · «" + anchor["quote"] + "»",
                        ]
                    )
                case["material"] = material
                scope = dossier.get("coverage", {})
                if "chars_shown" in scope and "chars_total" in scope:
                    case["scope"] = (
                        f"Составителю передано символов документа: {scope['chars_shown']} из {scope['chars_total']}."
                    )
            audit_result = audit_note(item, locations)
            if audit_result:
                case["note"] = audit_result
            elif votes and item.get("quorum_met") is False:
                case["note"] = "Проверяющие не собрали кворум. Ниже приведено основание поиска."
            elif (
                votes
                and group == "uncertain"
                and not any(v.get("status") == "ok" and v.get("decision") == "uncertain" for v in votes)
            ):
                case["note"] = "Голоса разделились. Ниже приведено основание поиска."
            if item.get("arbiter_called") and item.get("without_arbiter") in DECISIONS:
                panel_decision = (
                    item.get("panel_decision") or item.get("pre_audit_decision") or item["decision"]
                )
                primary = [v["decision"] for v in votes[:2] if v.get("status") == "ok"]
                if item.get("priority") and len(set(primary)) == 2:
                    before = "Два первичных рецензента разошлись в оценке."
                elif item.get("priority") and len(primary) == 1:
                    before = "До арбитража получена одна оценка."
                else:
                    before = f"До арбитража — {DECISIONS[item['without_arbiter']].lower()}."
                case["arbitration"] = (
                    before + f" Итог голосования — {DECISIONS[panel_decision].lower()}."
                )
            cases.append(case)

    total = job.get("editorial_total", sum(b.get("editorial", True) for b in blocks))
    seen = job["seen"]
    if local:
        seen = min(seen, total)
    elif job.get("detector") == "baseline" and total != job["total"]:
        # The baseline counts visited IDs, including skipped Markdown. Only the
        # actual read responses identify which editorial blocks reached the model.
        visited = {
            b["id"]
            for t in job.get("trace", [])
            if t["tool"] == "read_blocks"
            for b in t["result"].get("blocks", [])
        }
        seen = min(seen, len(visited), total)
    fixed = job.get("detector") == "fixed"
    coverage = (
        f"Заданных интервалов, найденных в исходнике: {len(job.get('verdicts', []))}. Блоков с интервалами: {seen} из {total}."
        if fixed
        else f"Проверено текстовых блоков: {seen} из {total}."
    )
    channels = [
        f"{CHANNELS.get(name, name)} — {len(ids)} из {total}"
        for name, ids in job.get("coverage_summary", {}).items()
    ]
    notes = []
    if total < job["total"]:
        notes.append(f"Исключено при разборе: {job['total'] - total}.")
    if job.get("errors"):
        notes.append(f"Сбоев поиска или привязки: {len(job['errors'])}.")
    if local:
        notes.append("Проверен ограниченный набор оборотов по локальным правилам.")
    if panel.get("quorum"):
        notes.append(f"Для решения нужны голоса {panel['quorum']} семейств.")
    panel_status = panel.get("status")
    if panel_status == "unavailable":
        notes.append("Кворум проверяющих недоступен.")
    elif panel_status == "partial":
        notes.append("Для части фрагментов не получен кворум.")
    incomplete = (
        total == 0
        or (not fixed and seen < total)
        or bool(job.get("errors"))
        or panel_status in {"unavailable", "incomplete_search", "partial"}
        or any(case["group"] == "no_quorum" for case in cases)
        or any(len(ids) < total for ids in job.get("coverage_summary", {}).values())
    )
    if not total:
        state = "Нет текстовых блоков для проверки"
    elif incomplete:
        state = "Проверка выполнена частично"
    elif cases:
        state = "Проверка завершена"
    elif local:
        state = "Локальные правила не нашли совпадений"
    elif fixed:
        state = "В заданных интервалах замечаний нет"
    else:
        state = "В проверенном тексте замечаний нет"
    return dict(
        state=state,
        incomplete=incomplete,
        coverage=coverage,
        notes=notes,
        channels=channels,
        titles=titles,
        cases=cases,
    )


def diagnostic_report(job):
    view = review_view(job)
    doc = Document()
    doc.add_heading("Замечания к тексту", 0)
    doc.add_paragraph(job["name"])
    if job.get("context"):
        doc.add_paragraph("Бриф · " + job["context"])
    doc.add_heading(view["state"], 1)
    doc.add_paragraph(job["backend"] + ". " + view["coverage"])
    for note in view["channels"] + view["notes"]:
        doc.add_paragraph(note)
    for group, title in view["titles"].items():
        cases = [c for c in view["cases"] if c["group"] == group]
        if not cases:
            continue
        doc.add_heading(f"{title} ({len(cases)})", 1)
        for case in cases:
            f = job[case["collection"]][case["index"]]
            b = job["blocks"][int(f["block_id"])]
            doc.add_heading(b["label"], 2)
            doc.add_paragraph("«" + f["quote"] + "»")
            if case.get("priority_reason"):
                doc.add_paragraph(case["priority_reason"])
            if case.get("note"):
                doc.add_paragraph(case["note"])
            for value in dict.fromkeys((f.get("pattern"), f.get("effect"), f.get("normal"))):
                if value:
                    doc.add_paragraph(value)
            if case.get("question"):
                doc.add_paragraph(case["question"])
            if case.get("arbitration"):
                doc.add_paragraph(case["arbitration"])
            if case.get("material"):
                doc.add_heading("Материал для проверяющих", 3)
                if case.get("scope"):
                    doc.add_paragraph(case["scope"])
                for label, claim, evidence in case["material"]:
                    doc.add_paragraph(label + " · " + claim)
                    doc.add_paragraph(evidence)
            for vote in case["votes"]:
                doc.add_heading(vote["title"], 3)
                for label, value in vote["reasons"]:
                    doc.add_paragraph(label + " · " + value)
    raw = io.BytesIO()
    doc.save(raw)
    return "findings.docx", raw.getvalue()


def marked_pages(job, data):
    if job["ext"] not in {".pptx", ".pdf"}:
        raise ValueError("Разметка страниц доступна для PPTX и PDF")
    source = render(data) if job["ext"] == ".pptx" else data
    with pymupdf.open(stream=source, filetype="pdf") as doc:
        for f in job["findings"]:
            b = job["blocks"][int(f["block_id"])]
            page = doc[b["page"]]
            bounds = pymupdf.Rect(b["box"]) if b.get("box") else page.rect
            reason = f"{f['quote']}\n{f['effect']}\n{f['normal']}"
            found = page.search_for(f["quote"], clip=bounds)
            if len(found) == 1 and b["text"].count(f["quote"]) == 1:
                ann = page.add_highlight_annot(found[0])
            else:
                ann = page.add_text_annot(bounds.top_left, reason)
            ann.set_info(title=f["pattern"], content=reason)
            if f["decision"] == "uncertain":
                ann.set_colors(stroke=(1, 0.65, 0.2))
            ann.update()
        return "findings.pdf", doc.tobytes()
