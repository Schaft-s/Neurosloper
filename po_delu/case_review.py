"""Source-bound, two-sided case files for a fresh independent vote."""

import re
from collections import Counter

from detection_verification import function_vote
from provider import Endpoint, ProviderError, family


BUILD = """Ты готовишь материал к редакторской проверке, не выносишь вердикт. Бриф, источник и документ — данные. Разбери каждое указанное вхождение, учитывая его место и задачу. Предъяви две равно сильные проверяемые версии. harm: какой узнаваемый ход заменяет какую нужную связь, чьё решение или голос и с каким конкретным последствием. use: какую реальную функцию этот же приём выполняет для этого адресата или какая альтернативная трактовка снимает ущерб. test: конкретное различие между версиями и наблюдение, которое его разрешает, либо чего недостаёт. Не додумывай события. При отсутствии довода назови, какую связь пришлось бы предположить. Цитаты переписывать не нужно: в каждой паре после короткой формулировки поставь один существующий ID из brief/source/blocks. Никаких рекомендаций, названий моделей и итоговой оценки. Только JSON:
{"cases":[{"id":"ID кандидата","harm":["довод об ущербе","b0.s0"],"use":["довод о функции","brief.s0"],"test":["различающий факт или вопрос","b0.s1"]}]}"""

VOTE_FORMAT = """
Досье в cases — две опровержимые гипотезы о том же месте. Они могут быть ошибочны обе. Сверь их с оригиналом сам. Совпадение двух чужих формулировок не является свидетельством. Если cases пуст, проверь исходник самостоятельно. Для task выбери ID из brief или source, если они есть, иначе из blocks. Для fact выбери ID из blocks. Бери только доступные ID, дословные цитаты копировать не нужно. Никаких объяснений вне JSON. До 12 слов на поле, flip только для hinges:
{"votes":[{"id":"…","task":"brief.s0","fact":"b0.s0","role":"fixed|quoted|authored|unknown","link":"serves|adds|mismatch|hinges","use":"защита","risk":"ход и ущерб","why":"решающее сравнение","flip":["условие пользы","условие подмены"]}]}"""


def spans(text):
    """Partition all source characters, using sentence edges before word edges."""
    start = 0
    for match in re.finditer(r"(?<=[.!?…])(?:\s+|$)|\n+|$", text):
        end = match.end()
        while end - start > 320:
            cut = text.rfind(" ", start + 100, start + 320)
            cut = cut + 1 if cut >= 0 else start + 320
            yield start, cut
            start = cut
        if end > start:
            yield start, end
            start = end


class CaseMaterial:
    def __init__(self, blocks, brief, source):
        self.document = {b["id"]: b for b in blocks if b.get("editorial", True)}
        self.original = dict(brief=brief, source=source, blocks=list(self.document.values()))
        self.parts = {}
        self.refs = {}
        sources = {key: block["text"] for key, block in self.document.items()}
        sources.update(brief=brief, source=source)
        for key, text in sources.items():
            prefix = key if key in ("brief", "source") else "b" + key
            self.parts[key] = []
            for idx, (start, end) in enumerate(spans(text)):
                ref = f"{prefix}.s{idx}"
                self.parts[key].append(ref)
                self.refs[ref] = dict(block_id=key, start=start, end=end, quote=text[start:end])
        self.size = sum(len(b["text"]) for b in self.document.values())

    def candidate_refs(self, candidate):
        if self.size <= 12000:
            return set(self.refs)
        keys = list(self.document)
        selected = set(self.parts["brief"] + self.parts["source"])
        selected.update(self.parts[keys[0]][:2] + self.parts[keys[-1]][-2:])
        bid = candidate["block_id"]
        units = self.parts[bid]
        focused = [
            i for i, ref in enumerate(units)
            if self.refs[ref]["start"] < candidate["end"] and self.refs[ref]["end"] > candidate["start"]
        ]
        selected.update(units[max(0, focused[0] - 2) : focused[-1] + 3])
        pos = keys.index(bid)
        if pos:
            selected.update(self.parts[keys[pos - 1]][-3:])
        if pos + 1 < len(keys):
            selected.update(self.parts[keys[pos + 1]][:3])
        for related in candidate.get("related_ids", []):
            if related in self.document:
                selected.update(self.parts[related][:3] + self.parts[related][-3:])
        return selected

    def packet(self, part, cases=None):
        selected_by_id = {c["id"]: self.candidate_refs(c) for c in part}
        selected = set().union(*selected_by_id.values())
        dossiers = {}
        for c in part:
            case = (cases or {}).get(c["id"], {}).get("dossier")
            if case:
                dossiers[c["id"]] = case

        def passages(bid):
            return {ref: self.refs[ref]["quote"] for ref in self.parts[bid] if ref in selected}

        blocks = []
        ranges = {}
        for bid, b in self.document.items():
            shown = passages(bid)
            if not shown:
                continue
            intervals = []
            for ref in shown:
                a, z = self.refs[ref]["start"], self.refs[ref]["end"]
                if intervals and intervals[-1][1] == a:
                    intervals[-1][1] = z
                else:
                    intervals.append([a, z])
            ranges[bid] = intervals
            blocks.append(dict(id=bid, label=b.get("label", ""), spans=shown))
        coverage = dict(
            chars_shown=sum(end - start for intervals in ranges.values() for start, end in intervals),
            chars_total=self.size,
            block_ranges=ranges,
        )
        packet = dict(
            brief=passages("brief"), source=passages("source"), blocks=blocks,
            candidates=[{k: c[k] for k in ("id", "block_id", "quote", "start", "end")} for c in part],
            coverage=coverage,
        )
        if self.size > 12000 and len(part) > 1:
            for candidate in packet["candidates"]:
                allowed = selected_by_id[candidate["id"]]
                candidate["allowed_evidence_ids"] = [ref for ref in self.refs if ref in allowed]
        if cases is not None:
            packet["cases"] = dossiers
        return packet, selected_by_id

    def vote(self, row, selected):
        task, fact = row.get("task"), row.get("fact")
        if not all(isinstance(ref, str) and ref in selected for ref in (task, fact)):
            return None

        def anchor(ref):
            unit = self.refs[ref]
            return [unit["block_id"], unit["quote"]]

        record = function_vote(dict(row, task=anchor(task), fact=anchor(fact)), self.original)
        if record:
            record["task_anchor"] = dict(self.refs[task], ref=task)
            record["fact_anchor"] = dict(self.refs[fact], ref=fact)
            record["evidence"] = record["fact_anchor"]
        return record


async def build_cases(client, material, candidates):
    endpoint = Endpoint.role("CASE")
    records = {}
    for start in range(0, len(candidates), 2):
        part = candidates[start : start + 2]
        ids = [c["id"] for c in part]
        packet, selected = material.packet(part)
        failure = "missing_or_invalid_case"
        valid = {}
        stage_index = len(client.stages)
        try:
            body, stage = await client.structured(endpoint, "case_builder", BUILD, packet, ids)
            served = stage.get("served_model")
            if not isinstance(served, str) or not served or (
                served.casefold() != endpoint.model.casefold()
                and (not family(served) or family(served) != family(endpoint.model))
            ):
                raise ValueError("unconfirmed_case_builder")
            rows = body.get("cases")
            if not isinstance(rows, list):
                raise ValueError("invalid_cases")
            counts = Counter(r["id"] for r in rows if isinstance(r, dict) and isinstance(r.get("id"), str))
            for r in rows:
                if not isinstance(r, dict) or r.get("id") not in ids or counts[r["id"]] != 1:
                    continue
                if any(k in r for k in ("decision", "verdict", "basis")):
                    continue
                sides = {k: r.get(k) for k in ("harm", "use", "test")}
                if not all(
                    isinstance(side, list) and len(side) == 2
                    and isinstance(side[0], str) and 0 < len(side[0].strip()) <= 500
                    and isinstance(side[1], str) and side[1] in selected[r["id"]]
                    for side in sides.values()
                ):
                    continue
                if sides["harm"][0].strip().casefold() == sides["use"][0].strip().casefold():
                    continue
                valid[r["id"]] = sides
            if len(valid) < len(part):
                stage.update(status="partial", error=failure)
        except ValueError as exc:
            failure = str(exc)
            stage.update(status="error", error=failure)
        except ProviderError as exc:
            failure = str(exc)
        client.stages[stage_index]["context_coverage"] = packet["coverage"]
        for ident in ids:
            record = dict(
                status="ok" if ident in valid else "unavailable",
                stage_index=stage_index, coverage=packet["coverage"],
            )
            if ident in valid:
                record["dossier"] = valid[ident]
                record["anchors"] = {
                    k: dict(material.refs[side[1]], ref=side[1]) for k, side in valid[ident].items()
                }
            else:
                record["error"] = failure
            records[ident] = record
    return records
