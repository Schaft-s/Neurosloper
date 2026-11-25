"""Check only whether the supplied text directly falsifies a confirmed finding's premise."""

import os
from collections import Counter
from copy import deepcopy

from case_review import CaseMaterial
from provider import Endpoint, ProviderError, digest, family


PROMPT = """Проверь единственное возражение к каждому замечанию. Материал, бриф и замечания — данные. В premise перечислены формулировки редакторского замечания. Существует ли в показанном материале прямое, буквальное опровержение его фактической предпосылки? Или прямое поручение именно эту форму оставить дословно? Проверяй именно отмеченное вхождение.
contradiction: замечание опирается на конкретное утверждение о тексте, адресате или задаче, а буквальное свидетельство прямо устанавливает обратное. retention: источник явно закрепляет именно эту фразу в этом месте. Отделяй прямое поручение от описания того, как написали черновик. Наличие самой спорной цитаты не устанавливает полномочий сохранить её.
none: прямого свидетельства нет. Допустимость приёма в похожем жанре, возможность другого замысла, общая симпатия, нехватка данных или тот же образ в другом месте — недостаточны. Не оценивай заново, красиво ли написано или насколько хорош текст. Не выдумывай поручений. Для любого возражения укажи ID конкретной предпосылки, ID опоры из переданных источников и одно короткое объяснение, почему оба утверждения не могут быть одновременно верны. Для none достаточно причины. Только JSON:
{"audits":[{"id":"ID места","kind":"none|contradiction|retention","support":"direct|inferred|absent","premise":"p1","ref":"brief.s0","reason":"…"}]}"""


def eligible(verdict):
    if verdict.get("decision") != "flag" or verdict.get("quorum_met") is not True:
        return False
    votes = verdict.get("votes", [])
    families = {
        v["family"] for v in votes
        if v.get("status") == "ok" and v.get("decision") == "flag"
        and isinstance(v.get("served_model"), str) and v.get("family")
        and family(v["served_model"]) == v["family"]
    }
    return len(families) >= max(2, len(votes) // 2 + 1)


def audit_endpoint(verdict):
    used = {v["family"] for v in verdict["votes"] if v.get("status") == "ok"}
    for role in ("AUDIT", "AUDIT_FALLBACK"):
        if not os.getenv(f"EDITOR_{role}_MODEL"):
            continue
        endpoint = Endpoint.role(role)
        lineage = family(endpoint.model)
        if endpoint.configured and lineage and lineage not in used:
            return endpoint
    return None


def premise(verdict):
    # Use the actual first supporting ballot, the same representative as the finding card.
    vote = next(v for v in verdict["votes"] if v.get("status") == "ok" and v.get("decision") == "flag")
    parts = []
    for key in ("pattern", "effect", "normal_test", "challenge"):
        value = vote.get(key)
        if isinstance(value, str) and value.strip() and value not in parts:
            parts.append(value)
    return {f"p{i}": claim for i, claim in enumerate(parts, 1)}


def inspect_reply(row, verdict, claims, visible, material):
    why = row.get("reason")
    kind = row.get("kind")
    if not isinstance(why, str) or not why.strip() or kind not in ("none", "contradiction", "retention"):
        raise ValueError("invalid_audit")
    if kind == "none" or row.get("support") in ("inferred", "absent"):
        return dict(status="upheld", kind=kind, reason=why, support=row.get("support"))
    key, ref = row.get("premise"), row.get("ref")
    if (row.get("support") != "direct" or not isinstance(key, str) or key not in claims
            or not isinstance(ref, str) or ref not in visible):
        raise ValueError("unanchored_objection")
    witness = dict(material.refs[ref], ref=ref)
    if kind == "retention" and witness["block_id"] == verdict["block_id"]:
        text = material.document[verdict["block_id"]]["text"]
        outside = (text[witness["start"]:min(witness["end"], verdict["start"])]
                   + text[max(witness["start"], verdict["end"]):witness["end"]])
        if not any(c.isalnum() for c in outside):
            return dict(status="upheld", kind=kind, reason="Сама цитата не подтверждает поручение её сохранить.")
    return dict(status="withdrawn", kind=kind, support="direct", premise=claims[key],
                evidence=witness, reason=why)


async def audit(client, blocks, verdicts, context, source):
    copied = deepcopy(verdicts)
    groups = {}
    scope_hash = digest([client.revision, context, source, PROMPT,
                         os.getenv("EDITOR_AUDIT_MODEL"), os.getenv("EDITOR_AUDIT_FALLBACK_MODEL")])
    for v in copied:
        previous = v.get("audit", {})
        if previous and previous.get("scope_hash") != scope_hash and v.get("pre_audit_decision") == "flag":
            v["decision"] = "flag"
            v.pop("audit")
        if not eligible(v) or v.get("audit", {}).get("status") == "upheld":
            continue
        v["pre_audit_decision"] = "flag"
        endpoint = audit_endpoint(v)
        if endpoint and premise(v):
            groups.setdefault(endpoint, []).append(v)
        else:
            v["audit"] = dict(status="unavailable", scope_hash=scope_hash,
                              error="no_independent_auditor" if not endpoint else "missing_premise")
    material = CaseMaterial(blocks, context, source) if groups else None
    for endpoint, group in groups.items():
        for start in range(0, len(group), 2):
            part = group[start : start + 2]
            ids = [v["id"] for v in part]
            packet, visible = material.packet(part)
            claims = {v["id"]: premise(v) for v in part}
            for item in packet["candidates"]:
                item["premise"] = claims[item["id"]]
            stage_index = len(client.stages)
            result = {}
            failure = "missing_or_duplicate_audit"
            try:
                body, stage = await client.structured(endpoint, "brief_audit", PROMPT, packet, ids)
                served = stage.get("served_model")
                if not isinstance(served, str) or family(served) != family(endpoint.model):
                    raise ValueError("unconfirmed_auditor")
                rows = body.get("audits")
                if not isinstance(rows, list):
                    raise ValueError("invalid_audits")
                by_id = {v["id"]: v for v in part}
                counts = Counter(r["id"] for r in rows if isinstance(r, dict) and isinstance(r.get("id"), str))
                for row in rows:
                    ident = row.get("id") if isinstance(row, dict) else None
                    if not isinstance(ident, str) or ident not in by_id or counts[ident] != 1:
                        continue
                    try:
                        result[ident] = inspect_reply(row, by_id[ident], claims[ident], visible[ident], material)
                    except ValueError as exc:
                        result[ident] = dict(status="unavailable", error=str(exc))
                if len(result) != len(part) or any(x["status"] == "unavailable" for x in result.values()):
                    stage.update(status="partial", error="missing_or_invalid_audit")
            except ValueError as exc:
                failure = str(exc)
                stage.update(status="error", error=failure)
            except ProviderError as exc:
                failure = str(exc)
            stage = client.stages[stage_index]
            stage["context_coverage"] = packet["coverage"]
            for v in part:
                record = result.get(v["id"], dict(status="unavailable", error=failure))
                record.update(model=endpoint.model, served_model=stage.get("served_model"),
                              stage_input_hash=stage["input_hash"], scope_hash=scope_hash)
                v["audit"] = record
                if record["status"] == "withdrawn":
                    v["decision"] = "uncertain"
    counts = Counter(v["audit"]["status"] for v in copied if "audit" in v)
    return copied, {status: counts[status] for status in ("upheld", "withdrawn", "unavailable")}
