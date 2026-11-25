"""Shared personal editing workflow for the web editor and the Telegram adapter."""

import asyncio
import os
import re
from collections import defaultdict

import preferences
from engine import finish, rewrite, verify_replacements
from fidelity import confirmed
from html_format import replacements
from policy import allowed_ranges, check, literal_terms
from provider import Endpoint, digest


def document_consent_required():
    return os.getenv("PO_DELU_REQUIRE_MODEL_CONSENT", "").lower() in {"1", "true", "yes", "on"}


def document_detector(mode, consent):
    """Bind the detector choice to this immutable document, used later by rewrite/fidelity."""
    if mode == "local" or (not mode and document_consent_required()):
        return "offline"
    if mode == "models":
        if not consent:
            raise ValueError(
                "Подтвердите передачу текста этого черновика и требований настроенным моделям."
            )
        if not Endpoint.role().configured:
            raise ValueError("Модельный сервис не настроен. Доступна локальная проверка.")
        if os.getenv("EDITOR_DETECTOR") == "offline":
            raise ValueError("На этом сервере включена только локальная проверка.")
    elif mode:
        raise ValueError("Неизвестный режим проверки документа.")
    return None


def preserves_exceptions(before, after, exceptions):
    locked = sorted(
        (m.start(), m.end()) for phrase in exceptions for m in re.finditer(re.escape(phrase), before)
    )
    if not locked:
        return True
    cursor, editable = 0, []
    for start, end in locked:
        if start > cursor:
            editable.append({"start": cursor, "end": start})
        cursor = max(cursor, end)
    if cursor < len(before):
        editable.append({"start": cursor, "end": len(before)})
    return before == after or allowed_ranges(before, after, editable)


async def propose(job, data, ids, owner, share_examples=False):
    if len(ids) != len(set(ids)) or not ids:
        raise ValueError("Выберите фрагменты для замены.")
    if job.get("revision_hash") != digest(data):
        raise ValueError("Исходный документ изменился.")
    rules = preferences.entries(owner)
    exceptions = [r["before"] for r in rules if r["kind"] == "exception"]
    examples = {r["id"]: r for r in rules if r["kind"] == "example"}
    matches = {m["id"]: m for m in job.get("personal_matches", [])}
    variants = {m["id"]: m for m in job.get("personal_variants", [])}
    general_ids = [key for key in ids if not key.startswith(("p/", "v/"))]
    for key in ids:
        if not key.startswith("v/"):
            continue
        match = variants.get(key)
        if (
            not match
            or match["rule_id"] not in examples
            or any(examples[match["rule_id"]][k] != match[k] for k in ("before", "after", "note"))
        ):
            raise ValueError("Личный пример изменился. Повторите поиск.")
        if not share_examples:
            raise ValueError(
                "Чтобы предложить замену для смыслового совпадения, разрешите передать модели ваши примеры."
            )
        general_ids.append(key)

    personal = defaultdict(list)
    for key in ids:
        if key in general_ids:
            continue
        m = matches.get(key)
        if (
            not m
            or m["rule_id"] not in examples
            or any(examples[m["rule_id"]][k] != m[k] for k in ("before", "after"))
        ):
            raise ValueError("Личное правило изменилось. Проверьте черновик ещё раз.")
        personal[m["block_id"]].append(m)

    settings = dict(
        job,
        preference_examples=[{k: r[k] for k in ("before", "after", "note")} for r in examples.values()]
        if share_examples
        else [],
    )
    settings.pop("personal_matches", None)
    settings.pop("personal_total", None)
    settings.pop("personal_variants", None)
    settings.pop("personal_search", None)
    settings["findings"] = list(job["findings"])
    for key in general_ids:
        if key in variants:
            v = variants[key]
            settings["findings"].append(
                dict(
                    id=key,
                    block_id=v["block_id"],
                    start=v["start"],
                    end=v["end"],
                    quote=v["quote"],
                    pattern="Сходство с личным примером",
                    effect=v["rationale"],
                    normal="Личное предпочтение автора.",
                    decision="flag",
                    revision_hash=job["revision_hash"],
                )
            )
    settings["terms"] = literal_terms(settings.get("terms", []))
    result = (
        await rewrite(settings, data, general_ids)
        if general_ids
        else dict(settings, changes=[], refusals=[], selected_spans={})
    )
    changes = {c["id"]: c for c in result["changes"]}
    spans = dict(result.get("selected_spans") or {})
    refusals = list(result.get("refusals", []))
    by_block = {b["id"]: b for b in result["blocks"]}
    for key in sorted(set(changes) | personal.keys(), key=int):
        block = by_block[key]
        before = block["text"]
        try:
            intervals = [(p["start"], p["end"], p["after"]) for p in personal[key]]
            mixed_consensus = bool(
                intervals and key in changes and result.get("fidelity_mode") == "consensus"
            )
            if key in changes:
                after = changes[key]["after"]
                if not personal[key]:
                    intervals = []
                elif settings.get("precision") == "free":
                    raise ValueError("Для свободной правки блока выберите один источник замены.")
                else:
                    selected = [
                        f
                        for f in settings["findings"]
                        if f["id"] in general_ids and f["block_id"] == key
                    ]
                    intervals += replacements(before, after, selected)
            if intervals:
                cursor = 0
                parts = []
                for a, b, replacement in sorted(intervals):
                    if a < cursor:
                        raise ValueError("Выбранные фрагменты пересекаются.")
                    parts.extend([before[cursor:a], replacement])
                    cursor = b
                after = "".join(parts) + before[cursor:]
                reason = "Применена сохранённая автором формулировка."
                if key in changes:
                    reason = changes[key]["reason"] + " " + reason
                changes[key] = dict(
                    id=key,
                    after=after,
                    reason=reason,
                    personal=True,
                    verification={
                        "status": "author_example",
                        "reason": "Оценка ограничена локальными проверками.",
                    },
                )
                spans[key] = [{"start": a, "end": b} for a, b, _ in sorted(intervals)]
            if mixed_consensus:
                if not share_examples:
                    raise ValueError(
                        "Для совместной проверки личной и модельной замены разрешите передать личный пример или выберите их по отдельности."
                    )
                audit = await verify_replacements(
                    result["name"],
                    data,
                    [dict(id=key, block_id=key, after=after, spans=spans[key])],
                    context=result["context"],
                    source=result["source"],
                    terms=result["terms"],
                    length=result["length"],
                )
                verification = audit["results"][0]
                result["stages"].extend(audit["stages"])
                result["usage"].extend(
                    s["usage"] for s in audit["stages"] if isinstance(s.get("usage"), dict)
                )
                result.setdefault("fidelity_checks", []).append(verification)
                if verification["status"] != "pass" or verification.get("mode") != "consensus":
                    raise ValueError(verification["reason"])
                changes[key].update(verification=verification, requires_consensus=True)
            if personal[key] or exceptions:
                active_exceptions = [phrase for phrase in exceptions if phrase in before]
                if not preserves_exceptions(before, after, active_exceptions):
                    raise ValueError("Правка затрагивает личное исключение.")
                verified = confirmed(
                    changes[key].get("verification"),
                    result["revision_hash"],
                    key,
                    before,
                    after,
                    spans.get(key, []),
                )
                error = check(
                    block,
                    after,
                    settings["length"],
                    settings["terms"] + active_exceptions,
                    reviewed_phrasing=verified,
                )
                if error:
                    raise ValueError(error)
            if personal[key]:
                trial = dict(result, changes=list(changes.values()), selected_spans=spans)
                await asyncio.to_thread(
                    finish, trial, data, [key], "text" if result["ext"] == ".pdf" else "native"
                )
        except (ValueError, RuntimeError) as e:
            changes.pop(key, None)
            refusals.append(dict(block_id=key, reason=str(e)))
    for key, change in changes.items():
        before = by_block[key]["text"]
        try:
            fragments = replacements(before, change["after"], spans.get(key))
        except ValueError:
            fragments = replacements(before, change["after"], [])
        change["diff"] = [dict(start=a, end=b, after=text) for a, b, text in fragments]
    result.update(changes=list(changes.values()), refusals=refusals, selected_spans=spans)
    if general_ids and (personal or exceptions):
        result["rewrite_summary"] = f"Подготовлено замен: {len(changes)}. Отказов: {len(refusals)}."
    if not general_ids:
        result["rewrite_summary"] = (
            "Точные личные правила применены локально. Проверьте предложенный текст."
        )
    result["findings"] = job["findings"]
    if "personal_search" in job:
        result.update(personal_search=job["personal_search"], personal_variants=list(variants.values()))
    return preferences.attach(result, owner)
