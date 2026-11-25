"""Score frozen benchmark artifacts without importing product code or calling providers.

From the kit root: python3 tools/score_benchmark.py --out evaluation/v1/scores
Only location and verdict runs are in scope. Keys are read solely on the scoring side.
"""

import argparse
import csv
import hashlib
import itertools
import json
import math
import random
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LABELS = {"flag", "keep", "uncertain"}
RULES = ("iou50", "exact", "touching")


def read_rows(path):
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def index(rows):
    table = {}
    for row in rows:
        if row["id"] in table:
            raise ValueError(f"Duplicate id {row['id']}")
        table[row["id"]] = row
    return table


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def ratio(num, den):
    return num / den if den else None


def intersection(a, b):
    return max(0, min(a[1], b[1]) - max(a[0], b[0]))


def iou(a, b):
    common = intersection(a, b)
    return common / ((a[1] - a[0]) + (b[1] - b[0]) - common)


def matching(predicted, reference, rule="iou50"):
    """Maximum cardinality via augmenting paths. Keep duplicate nodes distinct."""
    edges = []
    for pred in predicted:
        if rule == "exact":
            linked = [j for j, ref in enumerate(reference) if pred == ref]
        elif rule == "touching":
            linked = [
                j for j, ref in enumerate(reference) if intersection(pred, ref) > 0
            ]
        elif rule == "iou50":
            linked = [j for j, ref in enumerate(reference) if iou(pred, ref) >= 0.5]
        else:
            raise ValueError(rule)
        edges.append(sorted(linked, key=lambda j: (-iou(pred, reference[j]), j)))
    owner = {}

    def augment(i, visited):
        for j in edges[i]:
            if j in visited:
                continue
            visited.add(j)
            if j not in owner or augment(owner[j], visited):
                owner[j] = i
                return True
        return False

    for i in range(len(edges)):
        augment(i, set())
    return sorted((i, j) for j, i in owner.items())


def blocks_for(text):
    """Benchmark TXT imports use nonblank physical lines, preserving whitespace."""
    blocks = {}
    pos = 1 if text.startswith("\ufeff") else 0
    for raw in text[pos:].splitlines(keepends=True):
        line = raw.rstrip("\r\n")
        if line.strip():
            blocks[str(len(blocks))] = (pos, len(line))
        pos += len(raw)
    return blocks


def validate_quote(text, start, end, quote):
    if type(start) != int or type(end) != int or not 0 <= start < end <= len(text):
        raise ValueError("Invalid interval")
    if text[start:end] != quote:
        raise ValueError("Quote differs from source")
    return start, end


def absolute(item, text, blocks, offsets):
    block = str(item["block_id"])
    base, size = blocks[block]
    if offsets.get(block) != base:
        raise ValueError("Incorrect block offset")
    start, end = item["start"], item["end"]
    if type(start) != int or type(end) != int or not 0 <= start < end <= size:
        raise ValueError("Invalid within-block interval")
    return validate_quote(text, base + start, base + end, item["quote"])


def validate_reference(doc, ref):
    if ref.get("source_sha256") != digest(doc["text"]) or ref["decision"] not in LABELS:
        raise ValueError(f"Invalid reference for {doc['id']}")
    for span in ref["spans"]:
        validate_quote(doc["text"], span["start"], span["end"], span["quote"])
        if span["decision"] not in ("flag", "uncertain"):
            raise ValueError(f"Invalid reference span decision in {doc['id']}")


def intervals(ref, label="flag"):
    return [(s["start"], s["end"]) for s in ref["spans"] if s["decision"] == label]


def covered_chars(ranges):
    end = 0
    total = 0
    for a, b in sorted(ranges):
        total += max(0, b - max(a, end))
        end = max(end, b)
    return total


def coverage(row, blocks, mode):
    """Character counts per requested search channel, excluding separators/blank lines."""
    size = sum(length for _, length in blocks.values())
    channels = {
        "semantic": ["semantic"],
        "skill": ["semantic"],
        "slopotron": ["slopotron"],
        "union": ["semantic", "slopotron"],
    }.get(mode, [])
    result = {}
    for channel in channels:
        spans = []
        for block, start, end in row.get("coverage", {}).get(channel, []):
            base, length = blocks[str(block)]
            if (
                type(start) != int
                or type(end) != int
                or not 0 <= start <= end <= length
            ):
                raise ValueError("Invalid coverage interval")
            spans.append((base + start, base + end))
        result[channel] = covered_chars(spans)
    return size, result


def decode(doc, row, mode):
    """Maintain different availability for proposal search, ballots and final output."""
    state = {
        "status": "missing",
        "search_status": "missing",
        "candidates": [],
        "verdicts": [],
        "flags": [],
        "uncertain": [],
        "coverage": {},
        "available_chars": 0,
        "errors": [],
        "raw_count": 0,
    }
    if row is None:
        return state
    text = doc["text"]
    try:
        original_hash = digest(text)
        if row.get("input_sha256") != original_hash:
            raise ValueError("Prediction/input hash differs")
        if row.get("failure"):
            state.update(status="error", search_status="error", errors=[row["failure"]])
            return state
        if row.get("revision_hash") != original_hash:
            raise ValueError("Prediction/revision hash differs")
        blocks = blocks_for(text)
        offsets = (
            {b: v[0] for b, v in blocks.items()}
            if mode == "fixed"
            else row["block_offsets"]
        )
        if offsets != {b: v[0] for b, v in blocks.items()}:
            raise ValueError("Prediction block map differs from input")
        raw = [absolute(c, text, blocks, offsets) for c in row["candidates"]]
        size, covered = coverage(row, blocks, mode)
        stages = row.get("stages", [])
        errors = list(row.get("errors", []))
        failed_stages = [s for s in stages if s.get("status") not in ("ok",)]
        search_errors = any(s.get("stage") != "vote" for s in failed_stages) or bool(
            errors
        )
        incomplete = mode != "fixed" and (
            row.get("seen") != len(blocks)
            or row.get("total") != len(blocks)
            or any(n != size for n in covered.values())
        )
        search = "partial" if search_errors or incomplete else "ok"
        state.update(
            search_status=search if mode != "fixed" else "fixed",
            candidates=list(dict.fromkeys(raw)),
            raw_count=len(raw),
            coverage=covered,
            available_chars=size,
        )
        verdicts = [(absolute(v, text, blocks, offsets), v) for v in row["verdicts"]]
        flags = [
            absolute(f, text, blocks, offsets)
            for f in row["findings"]
            if f.get("decision") == "flag"
        ]
        if any(v.get("decision") not in LABELS for _, v in verdicts):
            raise ValueError("Invalid recorded decision")
        if bool(raw) != bool(verdicts):
            raise ValueError("Missing search candidates or candidate dispositions")
        if Counter(flags) != Counter(
            pos for pos, v in verdicts if v["decision"] == "flag"
        ):
            raise ValueError("Delivered flags differ from recorded dispositions")
        raw_set = set(raw)
        if any(pos not in raw_set for pos, _ in verdicts):
            raise ValueError("Verdict does not belong to the proposal list")
        quorum = row.get("consensus", {}).get("quorum", 2)
        for _, verdict in verdicts:
            if "quorum_met" not in verdict:
                continue
            valid = {}
            for ballot in verdict.get("votes", []):
                if ballot.get("status") == "ok":
                    family = ballot.get("family") or ballot["model"]
                    if family in valid or ballot.get("decision") not in LABELS:
                        raise ValueError("Duplicate family or unknown ballot")
                    valid[family] = ballot["decision"]
            tally = Counter(valid.values())
            expected = next(
                (label for label in ("flag", "keep") if tally[label] >= quorum),
                "uncertain",
            )
            if (
                verdict["quorum_met"] != (len(valid) >= quorum)
                or verdict["decision"] != expected
            ):
                raise ValueError("Stored consensus disagrees with valid family ballots")
        if mode == "fixed":
            target = doc["target"]
            if (
                len(verdicts) != 1
                or verdicts[0][1]["quote"] != target["quote"]
                or str(verdicts[0][1]["block_id"]) != str(target["block_id"])
                or ("absolute" in target and verdicts[0][0] != target["absolute"])
            ):
                raise ValueError("Missing or wrong fixed target")
        indicator = row.get("consensus", {}).get("status")
        status = "ok"
        if indicator not in {"offline", "no_candidates", "not_requested", "complete"}:
            status = "no_quorum" if indicator == "unavailable" else "partial"
        if search != "ok" and mode != "fixed":
            status = "partial"
        if mode == "fixed" and not verdicts[0][1].get("quorum_met"):
            status = "no_quorum"
        # A two-family quorum can succeed if a third judge failed. Keep that vote's
        # failure visible without invalidating a genuinely returned consensus.
        state.update(
            status=status,
            verdicts=verdicts,
            flags=flags,
            uncertain=[
                p
                for p, v in verdicts
                if v["decision"] == "uncertain" and v.get("quorum_met", True)
            ],
            errors=errors
            + [
                {"stage": s.get("stage"), "error": s.get("error", s.get("status"))}
                for s in failed_stages
            ],
        )
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        if state["search_status"] == "missing":
            state["search_status"] = "invalid"
        state.update(status="invalid", errors=[str(exc)])
    return state


def doc_label(state):
    if state["flags"]:
        return "flag"
    if state["status"] != "ok":
        return "unavailable"
    return "uncertain" if state["uncertain"] else "keep"


def match_counts(pred, gold):
    return {rule: len(matching(pred, gold, rule)) for rule in RULES}


def doc_record(doc, ref, state, family, group):
    gold = intervals(ref)
    unsure = intervals(ref, "uncertain")
    flags, proposals = state["flags"], state["candidates"]
    mc = match_counts(flags, gold)
    cc = match_counts(proposals, gold)
    predicted_label = doc_label(state)
    return dict(
        id=doc["id"],
        cluster=family,
        genre=group,
        status=state["status"],
        search_status=state["search_status"],
        gold_doc=ref["decision"],
        pred_doc=predicted_label,
        correct_doc=int(predicted_label == ref["decision"]),
        clean_gold=int(ref["decision"] == "keep"),
        clean_return=int(ref["decision"] == "keep" and predicted_label == "keep"),
        clean_false_alarm=int(ref["decision"] == "keep" and bool(flags)),
        positives=len(gold),
        proposed=len(proposals),
        raw_proposed=state["raw_count"],
        predicted=len(flags),
        fp=len(flags) - mc["iou50"],
        fn=len(gold) - mc["iou50"],
        search_missed=len(gold) - cc["iou50"],
        filter_missed=cc["iou50"] - mc["iou50"],
        uncertain_gold=len(unsure),
        uncertain_pred=len(state["uncertain"]),
        uncertain_match=len(matching(state["uncertain"], unsure)),
        flag_overlaps_uncertain=len(matching(flags, unsure)),
        chars=state["available_chars"],
        search_coverage=state["coverage"],
        errors=state["errors"],
        **mc,
        **{"candidate_" + k: v for k, v in cc.items()},
    )


def percentile(xs, p):
    point = (len(xs) - 1) * p
    lo = int(point)
    return xs[lo] + (xs[math.ceil(point)] - xs[lo]) * (point - lo)


def cluster_bootstrap(rows, keys, metric, repetitions, seed):
    """Resample whole source clusters; correlated halves share a single draw."""
    data = defaultdict(list)
    for row in rows:
        data[row["cluster"]].append(tuple(row[k] for k in keys))
    groups = [tuple(map(sum, zip(*data[k]))) for k in sorted(data)]
    totals = tuple(map(sum, zip(*groups))) if groups else (0,) * len(keys)
    observed = metric(*totals)
    draws = [[] for _ in observed]
    rng = random.Random(seed)
    for _ in range(repetitions if groups else 0):
        sample = rng.choices(groups, k=len(groups))
        for series, value in zip(draws, metric(*map(sum, zip(*sample)))):
            if value is not None:
                series.append(value)
    results = []
    for value, distribution in zip(observed, draws):
        distribution.sort()
        valid = len(distribution)
        bounds = (
            [percentile(distribution, p) for p in (0.025, 0.975)]
            if valid
            else [None, None]
        )
        results.append(
            {
                "value": value,
                "ci95": bounds,
                "valid_resamples": valid,
                "clusters": len(groups),
                "seed": seed,
                "resamples": repetitions,
            }
        )
    return results


def summarize_detection(rows, bootstrap, seed):
    keys = (
        "positives",
        "predicted",
        "proposed",
        "raw_proposed",
        "fp",
        "fn",
        *RULES,
        *("candidate_" + k for k in RULES),
        "search_missed",
        "filter_missed",
        "uncertain_gold",
        "uncertain_pred",
        "uncertain_match",
        "flag_overlaps_uncertain",
        "clean_gold",
        "clean_return",
        "clean_false_alarm",
    )
    sums = {key: sum(r[key] for r in rows) for key in keys}
    summary = dict(sums)
    summary["documents"] = len(rows)
    summary["status_counts"] = dict(Counter(r["status"] for r in rows))
    summary["search_status_counts"] = dict(Counter(r["search_status"] for r in rows))
    summary["document_confusion"] = confusion(
        [(r["gold_doc"], r["pred_doc"]) for r in rows]
    )
    summary.update(class_scores([(r["gold_doc"], r["pred_doc"]) for r in rows]))
    for rule in RULES:
        hits = sums[rule]
        summary[rule + "_precision"] = ratio(hits, sums["predicted"])
        summary[rule + "_recall"] = ratio(hits, sums["positives"])
        summary[rule + "_f1"] = ratio(2 * hits, sums["predicted"] + sums["positives"])
        summary["candidate_" + rule + "_recall"] = ratio(
            sums["candidate_" + rule], sums["positives"]
        )
        summary["candidate_" + rule + "_precision"] = ratio(
            sums["candidate_" + rule], sums["proposed"]
        )
        summary["candidate_" + rule + "_f1"] = ratio(
            2 * sums["candidate_" + rule], sums["positives"] + sums["proposed"]
        )
    ci_keys = (
        "iou50",
        "positives",
        "predicted",
        "candidate_iou50",
        "clean_gold",
        "clean_return",
        "clean_false_alarm",
        "correct_doc",
    )
    augmented = [dict(r, n=1) for r in rows]
    cis = cluster_bootstrap(
        augmented,
        (*ci_keys, "n"),
        lambda h, g, p, c, k, clean, alarm, correct, n: (
            ratio(h, p),
            ratio(h, g),
            ratio(2 * h, g + p),
            ratio(c, g),
            ratio(clean, k),
            ratio(alarm, k),
            ratio(correct, n),
        ),
        bootstrap,
        seed,
    )
    ci_names = (
        "iou50_precision",
        "iou50_recall",
        "iou50_f1",
        "candidate_iou50_recall",
        "clean_kept_rate",
        "clean_false_alarm_rate",
        "document_accuracy",
    )
    summary["intervals"] = dict(zip(ci_names, cis))
    summary["abstention_documents"] = sum(r["pred_doc"] == "uncertain" for r in rows)
    summary["retained_hit_fraction"] = ratio(sums["iou50"], sums["candidate_iou50"])
    summary["uncertain_location_precision"] = ratio(
        sums["uncertain_match"], sums["uncertain_pred"]
    )
    summary["uncertain_location_recall"] = ratio(
        sums["uncertain_match"], sums["uncertain_gold"]
    )
    return summary


def confusion(pairs):
    table = defaultdict(Counter)
    for a, b in pairs:
        table[a][b] += 1
    return {a: dict(counts) for a, counts in sorted(table.items())}


def class_scores(pairs):
    metrics = {}
    for label in sorted(LABELS):
        gold = sum(a == label for a, _ in pairs)
        predicted = sum(b == label for _, b in pairs)
        tp = sum(a == b == label for a, b in pairs)
        metrics.update(
            {
                f"{label}_documents": gold,
                f"{label}_predicted_documents": predicted,
                f"{label}_doc_precision": ratio(tp, predicted),
                f"{label}_doc_recall": ratio(tp, gold),
                f"{label}_doc_f1": ratio(2 * tp, gold + predicted),
            }
        )
    return metrics


def agreement(pairs):
    n = len(pairs)
    equal = sum(a == b for a, b in pairs)
    first, second = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    pe = sum(v * second[k] for k, v in first.items())
    return {
        "both_available": n,
        "equal": equal,
        "agreement": ratio(equal, n),
        "cohen_kappa": ratio(equal * n - pe, n * n - pe),
        "confusion": confusion(pairs),
    }


def reference_agreement(docs, a, b, bootstrap, seed):
    if set(a) != set(docs) or set(b) != set(docs):
        raise ValueError(
            "Independent annotation coverage differs from the complete genre suite"
        )
    items = []
    for key in sorted(docs):
        row = {
            "id": key,
            "cluster": key,
            "genre": key.split("-")[0],
            "a": a[key]["decision"],
            "b": b[key]["decision"],
        }
        for label in ("flag", "uncertain"):
            left, right = intervals(a[key], label), intervals(b[key], label)
            row[label + "_a"], row[label + "_b"] = len(left), len(right)
            for rule, hits in match_counts(left, right).items():
                row[label + "_" + rule] = hits
        items.append(row)
    report = {}
    for group in ["all", *sorted({x["genre"] for x in items})]:
        chosen = [x for x in items if group == "all" or x["genre"] == group]
        result = agreement([(x["a"], x["b"]) for x in chosen])
        annotated = [dict(x, correct=int(x["a"] == x["b"]), n=1) for x in chosen]
        result["agreement_ci"] = cluster_bootstrap(
            annotated, ("correct", "n"), lambda h, n: (ratio(h, n),), bootstrap, seed
        )[0]
        for label in ("flag", "uncertain"):
            left, right = (
                sum(x[label + "_" + side] for x in chosen) for side in ("a", "b")
            )
            loc = {"spans_a": left, "spans_b": right}
            for rule in RULES:
                h = sum(x[label + "_" + rule] for x in chosen)
                loc[rule] = {"matched": h, "f1": ratio(2 * h, left + right)}
            loc["iou50_ci"] = cluster_bootstrap(
                chosen,
                (label + "_iou50", label + "_a", label + "_b"),
                lambda h, a, b: (ratio(2 * h, a + b),),
                bootstrap,
                seed,
            )[0]
            result[label] = loc
        report[group] = result
    return report, items


def ballots(verdict):
    result = {}
    for vote in verdict.get("votes", []):
        key = vote["model"]
        if key in result:
            raise ValueError("Duplicate ballot from one model on one candidate")
        result[key] = (
            vote["decision"]
            if vote["status"] == "ok" and vote.get("decision") in LABELS
            else "unavailable"
        )
    return result


def target_verdicts(doc, key, state, mode, panel):
    """Compare every real ballot on the same submitted location; expose off-target ballots."""
    target = (key["start"], key["end"])
    sources = (
        ["consensus", *panel] if mode not in ("offline", "baseline") else ["returned"]
    )
    records, by_source = [], defaultdict(list)
    for pos, verdict in state["verdicts"]:
        overlap = iou(pos, target)
        decisions = ballots(verdict)
        collective = sources[0]
        decisions[collective] = (
            verdict["decision"] if verdict.get("quorum_met", True) else "unavailable"
        )
        for source in sources:
            decision = decisions.get(source, "unavailable")
            records.append(
                {
                    "id": doc["id"],
                    "cluster": key.get("cluster", key["family"]),
                    "source": source,
                    "candidate_id": verdict.get("id"),
                    "start": pos[0],
                    "end": pos[1],
                    "quote": verdict["quote"],
                    "gold": key["decision"] if overlap >= 0.5 else None,
                    "target_iou": overlap,
                    "decision": decision,
                    "delivered": verdict["decision"]
                    if source == collective
                    else decision,
                }
            )
            if overlap >= 0.5:
                by_source[source].append(decision)
    rows = []
    for source in sources:
        labels = by_source[source]
        if "flag" in labels:
            decision = "flag"
        elif labels and "unavailable" not in labels:
            decision = "uncertain" if "uncertain" in labels else "keep"
        elif (
            not labels
            and mode != "fixed"
            and state["status"] != "invalid"
            and state["search_status"] == "ok"
            and (source != "consensus" or state["status"] == "ok")
        ):
            decision = "keep"
        else:
            decision = "unavailable"
        rows.append(
            {
                "id": doc["id"],
                "cluster": key.get("cluster", key["family"]),
                "group_size": key.get("group_size", 2),
                "source": source,
                "gold": key["decision"],
                "decision": decision,
                "retrieved": bool(labels),
                "status": state["status"],
                "search_status": state["search_status"],
            }
        )
    return records, rows


def verdict_summaries(targets, bootstrap, seed):
    output = []
    for (run, source), iterator in itertools.groupby(
        sorted(targets, key=lambda x: (x["run"], x["source"])),
        key=lambda x: (x["run"], x["source"]),
    ):
        rows = list(iterator)
        for scope, chosen in (
            ("end_to_end", rows),
            (
                "retrieved_valid",
                [r for r in rows if r["retrieved"] and r["decision"] in LABELS],
            ),
        ):
            labels = [(r["gold"], r["decision"]) for r in chosen]
            families = defaultdict(list)
            for r in chosen:
                families[r["cluster"]].append(r)
            complete_pairs = []
            groups = []
            for family, pair in families.items():
                expected_size = pair[0].get("group_size", 2)
                if len(pair) != expected_size or expected_size < 2:
                    continue
                expected_labels = {r["gold"] for r in pair}
                outputs = {r["decision"] for r in pair}
                if len(expected_labels) >= 2:
                    groups.append(
                        {
                            "cluster": family,
                            "n": 1,
                            "size": len(pair),
                            "correct": int(
                                all(r["gold"] == r["decision"] for r in pair)
                            ),
                            "scorable": int(outputs <= LABELS),
                            "distinct": int(outputs == expected_labels),
                            "three_way": int(expected_labels == LABELS),
                        }
                    )
                decisive_halves = [r for r in pair if r["gold"] in ("flag", "keep")]
                if len(decisive_halves) != 2 or {
                    r["gold"] for r in decisive_halves
                } != {"keep", "flag"}:
                    continue
                by_gold = {r["gold"]: r["decision"] for r in decisive_halves}
                complete_pairs.append(
                    {
                        "cluster": family,
                        "n": 1,
                        "correct": int(by_gold == {"keep": "keep", "flag": "flag"}),
                        "reversed": int(by_gold == {"keep": "flag", "flag": "keep"}),
                        "scorable": int(set(by_gold.values()) <= LABELS),
                        "changed": int(set(by_gold.values()) == {"flag", "keep"}),
                    }
                )
            enriched = [
                dict(r, n=1, correct=int(r["gold"] == r["decision"])) for r in chosen
            ]
            pair_ci = cluster_bootstrap(
                complete_pairs,
                ("correct", "n"),
                lambda h, n: (ratio(h, n),),
                bootstrap,
                seed,
            )[0]
            output.append(
                {
                    "run": run,
                    "source": source,
                    "scope": scope,
                    "docs": len(chosen),
                    "correct": sum(a == b for a, b in labels),
                    "accuracy_ci": cluster_bootstrap(
                        enriched,
                        ("correct", "n"),
                        lambda h, n: (ratio(h, n),),
                        bootstrap,
                        seed,
                    )[0],
                    "confusion": confusion(labels),
                    "unavailable": sum(b == "unavailable" for a, b in labels),
                    "abstentions": sum(b == "uncertain" for a, b in labels),
                    "pairs": len(complete_pairs),
                    "pairs_scorable": sum(r["scorable"] for r in complete_pairs),
                    "both_correct": sum(r["correct"] for r in complete_pairs),
                    "both_correct_ci": pair_ci,
                    "reversed": sum(r["reversed"] for r in complete_pairs),
                    "decisive_flip": sum(r["changed"] for r in complete_pairs),
                    "groups": len(groups),
                    "groups_scorable": sum(r["scorable"] for r in groups),
                    "all_versions_correct": sum(r["correct"] for r in groups),
                    "all_versions_correct_ci": cluster_bootstrap(
                        groups,
                        ("correct", "n"),
                        lambda h, n: (ratio(h, n),),
                        bootstrap,
                        seed,
                    )[0],
                    "distinct_outcomes_returned": sum(r["distinct"] for r in groups),
                    "three_way_groups": sum(r["three_way"] for r in groups),
                    "three_way_groups_correct": sum(
                        r["three_way"] * r["correct"] for r in groups
                    ),
                    **class_scores(labels),
                }
            )
    return output


def judge_agreement(records):
    indexed = defaultdict(dict)
    for r in records:
        key = (r["id"], r["candidate_id"], r["start"], r["end"])
        indexed[r["run"], r["source"]][key] = r["decision"]
    output = []
    for run in sorted({run for run, _ in indexed}):
        sources = sorted(source for name, source in indexed if run == name)
        for a, b in itertools.combinations(sources, 2):
            first, second = indexed[run, a], indexed[run, b]
            shared = sorted(set(first) & set(second))
            available = [
                (first[k], second[k])
                for k in shared
                if first[k] in LABELS and second[k] in LABELS
            ]
            output.append(
                dict(
                    run=run,
                    left=a,
                    right=b,
                    offered=len(shared),
                    **agreement(available),
                )
            )
    return output


def write_csv(path, rows):
    fields = list(dict.fromkeys(k for row in rows for k in row))
    with path.open("w", encoding="utf-8", newline="") as stream:
        w = csv.DictWriter(stream, fields)
        w.writeheader()
        for row in rows:
            w.writerow(
                {
                    k: json.dumps(v, ensure_ascii=False, separators=(",", ":"))
                    if isinstance(v, (list, dict))
                    else v
                    for k, v in row.items()
                }
            )


def paired_differences(groups, bootstrap, seed):
    """Method deltas on the common configured IDs, with both methods in each draw."""
    outputs = []
    for (suite, ref), runs in groups.items():
        for (name, first), (other, second) in itertools.combinations(
            sorted(runs.items()), 2
        ):
            a = {r["id"]: r for r in first}
            b = {r["id"]: r for r in second}
            paired = []
            for key in sorted(set(a) & set(b)):
                x, y = a[key], b[key]
                if x["positives"] != y["positives"] or x["cluster"] != y["cluster"]:
                    raise ValueError("Unaligned paired references")
                paired.append(
                    {
                        "cluster": x["cluster"],
                        "gold": x["positives"],
                        "a": x["iou50"],
                        "b": y["iou50"],
                        "pa": x["predicted"],
                        "pb": y["predicted"],
                        "ca": x["candidate_iou50"],
                        "cb": y["candidate_iou50"],
                        "acc_a": x["correct_doc"],
                        "acc_b": y["correct_doc"],
                        "n": 1,
                        "both_ok": int(x["status"] == y["status"] == "ok"),
                        "search_ok": int(
                            x["search_status"] == y["search_status"] == "ok"
                        ),
                    }
                )

            def delta(g, a, b, pa, pb, ca, cb, acc_a, acc_b, n):
                f1a, f1b = ratio(2 * a, g + pa), ratio(2 * b, g + pb)
                return (
                    f1b - f1a if f1b is not None and f1a is not None else None,
                    ratio(cb - ca, g),
                    ratio(acc_b - acc_a, n),
                )

            for scope, rows in (
                ("end_to_end", paired),
                ("both_complete", [r for r in paired if r["both_ok"]]),
                ("both_search_complete", [r for r in paired if r["search_ok"]]),
            ):
                cis = cluster_bootstrap(
                    rows,
                    ("gold", "a", "b", "pa", "pb", "ca", "cb", "acc_a", "acc_b", "n"),
                    delta,
                    bootstrap,
                    seed,
                )
                outputs.append(
                    {
                        "suite": suite,
                        "reference": ref,
                        "left": name,
                        "right": other,
                        "scope": scope,
                        "common_documents": len(rows),
                        "delta_f1": cis[0] if scope != "both_search_complete" else None,
                        "delta_candidate_recall": cis[1],
                        "delta_document_accuracy": cis[2]
                        if scope != "both_search_complete"
                        else None,
                    }
                )
    return outputs


def scenario_field(keys, targets):
    """Use scenario membership, not a repeated genre or rhetorical-family heading."""

    def evidence(field):
        groups = defaultdict(list)
        for key, row in keys.items():
            groups[row.get(field)].append((row["decision"], targets[key]["quote"]))
        supported = shared_target = 0
        for name, members in groups.items():
            labels = {label for label, _ in members}
            if (
                name is not None
                and 2 <= len(members) <= 3
                and len(labels) == len(members)
            ):
                supported += len(members)
                if len({q for _, q in members}) == 1:
                    shared_target += len(members)
        return supported, shared_target

    scores = {field: evidence(field) for field in ("group", "family")}
    field = max(scores, key=scores.get)
    if scores[field][0] == 0:
        raise ValueError("No scenario cluster identifier in prospective keys")
    return field


def load_lockbox(root):
    folder = root / "evaluation/lockbox"
    if not all(
        (folder / f).is_file() for f in ("inputs.jsonl", "keys.jsonl", "targets.jsonl")
    ):
        return None
    inputs = index(read_rows(folder / "inputs.jsonl"))
    keys = index(read_rows(folder / "keys.jsonl"))
    targets = {
        k: v.get("target", v)
        for k, v in index(read_rows(folder / "targets.jsonl")).items()
    }
    if not inputs or set(inputs) != set(keys) or set(keys) != set(targets):
        raise ValueError("Inconsistent prospective input/key/target IDs")
    field = scenario_field(keys, targets)
    sizes = Counter(row[field] for row in keys.values())
    for key, row in keys.items():
        doc = inputs[key]
        text = doc["text"]
        target = dict(targets[key])
        quote = target["quote"]
        blocks = blocks_for(text)
        if "start" in target and "end" in target:
            start, end = validate_quote(text, target["start"], target["end"], quote)
            matches = [
                block_id
                for block_id, (base, length) in blocks.items()
                if base <= start and end <= base + length
            ]
            if len(matches) != 1:
                raise ValueError(f"{key}: fixed target must be in one TXT block")
            block_id = matches[0]
        else:
            block_id = str(target["block_id"])
            base, length = blocks[block_id]
            line = text[base : base + length]
            start = line.find(quote)
            if not quote or start < 0 or line.find(quote, start + 1) >= 0:
                raise ValueError(f"{key}: absent or ambiguous fixed target")
            start += base
        target.update(block_id=block_id, absolute=(start, start + len(quote)))
        doc["target"] = target
        row.setdefault("source_sha256", digest(text))
        row.update(
            cluster=row[field],
            cluster_field=field,
            group_size=sizes[row[field]],
            start=start,
            end=start + len(quote),
            quote=quote,
        )
        row["spans"] = [
            dict(s, decision=s.get("decision", row["decision"]))
            for s in row["spans"]
            if s.get("decision", row["decision"]) != "keep"
        ]
    return inputs, keys


def load_keys(v, root):
    docs = {
        "genre": index(read_rows(root / "data/genre_blind.jsonl")),
        "contrast": index(read_rows(v / "contrast_inputs.jsonl")),
    }
    contrast = index(read_rows(v / "contrast_keys.jsonl"))
    if (v / "contrast_targets.jsonl").is_file():
        targets = index(read_rows(v / "contrast_targets.jsonl"))
        for key, doc in docs["contrast"].items():
            doc["target"] = targets[key]["target"]
    refs = {"genre": {}, "contrast": {"contrast": {}}}
    prospective = load_lockbox(root)
    if prospective is not None:
        docs["lockbox"], keys = prospective
        refs["lockbox"] = {"lockbox": keys}
    if (v / "external_inputs.jsonl").is_file():
        docs["external"] = index(read_rows(v / "external_inputs.jsonl"))
        external = index(read_rows(v / "external.jsonl"))
        keys = index(read_rows(v / "external_keys.jsonl"))
        for item in keys.values():
            source = external[item["family"]][item["side"]]
            item["source_sha256"] = source["text_sha256"]
            if (
                item["spans"] != source["spans"]
                or item["decision"] != source["decision"]
            ):
                raise ValueError("External blind keys differ from source annotations")
        refs["external"] = {"external": keys}
    for key, row in contrast.items():
        span = {k: row[k] for k in ("start", "end", "quote", "decision")}
        refs["contrast"]["contrast"][key] = dict(
            row, spans=[span] if row["decision"] != "keep" else []
        )
        validate_quote(
            docs["contrast"][key]["text"], row["start"], row["end"], row["quote"]
        )
        target = docs["contrast"][key]["target"]
        if target["quote"] != row["quote"]:
            raise ValueError(f"{key}: fixed target differs from evaluation reference")
        target["absolute"] = (row["start"], row["end"])
    for name, path in [
        ("a", v / "review_a.jsonl"),
        ("b", root / "evaluation/review_b.jsonl"),
        ("adjudicated", v / "adjudicated.jsonl"),
    ]:
        if name == "adjudicated" and not path.exists():
            continue
        refs["genre"][name] = index(read_rows(path))
    for suite, tables in refs.items():
        for name, table in tables.items():
            if set(docs[suite]) != set(table):
                raise ValueError(f"{suite}/{name}: partial reference coverage")
            for key, ref in table.items():
                validate_reference(docs[suite][key], ref)
    return docs, contrast, refs


def score(v, root, out, bootstrap, seed, selected):
    docs, contrast, refs = load_keys(v, root)
    summary = {
        "method": {
            "primary": "maximum one-to-one positive overlap IoU >= 0.5",
            "touching": "at least one shared code point; adjacent half-open intervals do not count",
            "search": "identical candidate locations deduplicated across channels; final duplicates remain distinct",
            "labels": "flag-only metrics; uncertain locations and decisions also reported separately",
            "zero_denominator": "null",
            "interval": "95% percentile; undefined replicates omitted and counted",
            "seed": seed,
            "bootstrap": bootstrap,
            "clusters": "documents for genre; contrast families and before/after external revisions stay paired",
            "complete": "all intended search channels covered, no search error, valid final/quorum when requested",
            "end_to_end": "use any actually delivered flags; unavailable empty output is never a correct clean decision",
            "target": "IoU >= 0.5 at supplied locus; a valid free search with no proposal there implies keep",
            "accuracy": "three-way exact; technical unavailability counts as incorrect in end-to-end denominators",
            "filter_missed": "candidate maximum matches minus final maximum matches; includes merge/quorum losses",
        },
        "runs": {},
    }
    annot, annotation_items = reference_agreement(
        docs["genre"], refs["genre"]["a"], refs["genre"]["b"], bootstrap, seed
    )
    summary["independent_annotation_agreement"] = annot
    per_doc, all_ballots, targets, overview, spans, proposals = [], [], [], [], [], []
    comparisons = defaultdict(dict)
    folders = (
        [v / "runs" / r for r in selected]
        if selected
        else sorted(x.parent for x in (v / "runs").glob("*/config.json"))
    )
    for folder in folders:
        config_bytes = (folder / "config.json").read_bytes()
        config = json.loads(config_bytes)
        suite, mode = config["suite"], config["mode"]
        if suite not in docs or mode in ("rewrite", "fidelity"):
            continue
        name = folder.name
        planned = config["ids"]
        hashes = config["input_hashes"]
        if len(set(planned)) != len(planned) or len(planned) != len(hashes):
            raise ValueError(f"{name}: duplicate configured id or bad length")
        prediction_path = folder / "predictions.jsonl"
        prediction_bytes = (
            prediction_path.read_bytes() if prediction_path.exists() else b""
        )
        rows = index(
            [json.loads(line) for line in prediction_bytes.splitlines() if line.strip()]
        )
        if set(rows) - set(planned):
            raise ValueError(f"{name}: predictions outside configured IDs")
        info = {
            "suite": suite,
            "mode": mode,
            "predictions_sha256": hashlib.sha256(prediction_bytes).hexdigest(),
            "config_sha256": hashlib.sha256(config_bytes).hexdigest(),
            "planned": len(planned),
            "suite_size": len(docs[suite]),
            "stored": len(rows),
            "unplanned": sorted(set(docs[suite]) - set(planned)),
            "by_reference": {},
        }
        summary["runs"][name] = info
        records_by_ref = defaultdict(list)
        statuses, search_statuses = Counter(), Counter()
        channel_counts = defaultdict(lambda: [0, 0])
        for key, expected_hash in zip(planned, hashes):
            doc = docs[suite][key]
            if digest(doc["text"]) != expected_hash:
                raise ValueError(
                    f"{name}/{key}: configuration does not match current inputs"
                )
            state = decode(doc, rows.get(key), mode)
            statuses[state["status"]] += 1
            search_statuses[state["search_status"]] += 1
            for channel, char_count in state["coverage"].items():
                channel_counts[channel][0] += char_count
                channel_counts[channel][1] += state["available_chars"]
            if suite in ("contrast", "lockbox"):
                panel = (
                    config.get("panel", [])
                    if mode not in ("baseline", "offline")
                    else []
                )
                target_key = (
                    contrast[key]
                    if suite == "contrast"
                    else refs["lockbox"]["lockbox"][key]
                )
                raw, target_rows = target_verdicts(doc, target_key, state, mode, panel)
                all_ballots.extend(dict(run=name, **r) for r in raw)
                targets.extend(dict(run=name, **r) for r in target_rows)
            if mode == "fixed":
                continue
            for ref_name, table in refs[suite].items():
                ref = table[key]
                cluster = ref.get("cluster", ref.get("family", key))
                genre = (
                    key.split("-")[0]
                    if suite == "genre"
                    else ref.get("repository", suite)
                )
                record = doc_record(doc, ref, state, cluster, genre)
                records_by_ref[ref_name].append(record)
                per_doc.append(
                    dict(run=name, suite=suite, reference=ref_name, **record)
                )
                gold = intervals(ref)
                for stage, locs in (
                    ("proposal", state["candidates"]),
                    ("final_flag", state["flags"]),
                ):
                    pairs = matching(locs, gold)
                    paired_pred = {i: j for i, j in pairs}
                    paired_ref = {j for _, j in pairs}
                    for i, (start, end) in enumerate(locs):
                        j = paired_pred.get(i)
                        spans.append(
                            {
                                "run": name,
                                "reference": ref_name,
                                "id": key,
                                "stage": stage,
                                "status": state["status"],
                                "outcome": "matched"
                                if j is not None
                                else "unmatched_proposal"
                                if stage == "proposal"
                                else "false_positive",
                                "start": start,
                                "end": end,
                                "reference_start": gold[j][0]
                                if j is not None
                                else None,
                                "reference_end": gold[j][1] if j is not None else None,
                            }
                        )
                    for j, (start, end) in enumerate(gold):
                        if j not in paired_ref:
                            spans.append(
                                {
                                    "run": name,
                                    "reference": ref_name,
                                    "id": key,
                                    "stage": stage,
                                    "status": state["status"],
                                    "outcome": "missed",
                                    "reference_start": start,
                                    "reference_end": end,
                                }
                            )
        info["status_counts"] = dict(statuses)
        info["search_status_counts"] = dict(search_statuses)
        eligible_chars = sum(
            sum(n for _, n in blocks_for(docs[suite][k]["text"]).values())
            for k in planned
        )
        info["complete_rate"] = ratio(statuses["ok"], len(planned))
        info["search_complete_rate"] = (
            ratio(search_statuses["ok"], len(planned)) if mode != "fixed" else None
        )
        info["reported_channel_character_coverage"] = {
            ch: {
                "covered": a,
                "eligible_in_rows_with_map": b,
                "conditional_rate": ratio(a, b),
                "planned_chars": eligible_chars,
                "end_to_end_rate": ratio(a, eligible_chars),
            }
            for ch, (a, b) in channel_counts.items()
        }
        for ref_name, records in records_by_ref.items():
            comparisons[suite, ref_name][name] = records
            info["by_reference"][ref_name] = {}
            for group in [
                "all",
                *(sorted({r["genre"] for r in records}) if suite != "contrast" else []),
            ]:
                subset = [r for r in records if group == "all" or r["genre"] == group]
                for scope, chosen in (
                    ("end_to_end", subset),
                    ("complete", [r for r in subset if r["status"] == "ok"]),
                    (
                        "search_complete",
                        [r for r in subset if r["search_status"] == "ok"],
                    ),
                ):
                    metrics = summarize_detection(chosen, bootstrap, seed)
                    if scope in ("end_to_end", "search_complete"):
                        proposal_metrics = {
                            k: v
                            for k, v in metrics.items()
                            if k.startswith("candidate_")
                            or k
                            in (
                                "documents",
                                "positives",
                                "proposed",
                                "raw_proposed",
                                "search_missed",
                                "search_status_counts",
                            )
                        }
                        ci = cluster_bootstrap(
                            chosen,
                            ("candidate_iou50", "positives", "proposed"),
                            lambda h, g, p: (
                                ratio(h, p),
                                ratio(h, g),
                                ratio(2 * h, p + g),
                            ),
                            bootstrap,
                            seed,
                        )
                        proposal_metrics["intervals"] = dict(
                            zip(("precision", "recall", "f1"), ci)
                        )
                        info.setdefault("proposal_by_reference", {}).setdefault(
                            ref_name, {}
                        )[group + "/" + scope] = proposal_metrics
                        proposals.append(
                            dict(
                                run=name,
                                suite=suite,
                                reference=ref_name,
                                genre=group,
                                scope=scope,
                                **proposal_metrics,
                            )
                        )
                    if scope == "search_complete":
                        continue
                    info["by_reference"][ref_name][group + "/" + scope] = metrics
                    flat = {k: v for k, v in metrics.items() if not isinstance(v, dict)}
                    for k, ci in metrics["intervals"].items():
                        flat[k] = ci["value"]
                        flat[k + "_lo"], flat[k + "_hi"] = ci["ci95"]
                    overview.append(
                        dict(
                            run=name,
                            suite=suite,
                            reference=ref_name,
                            genre=group,
                            scope=scope,
                            **flat,
                        )
                    )
    summary["target_classification"] = verdict_summaries(targets, bootstrap, seed)
    summary["ballot_agreement"] = judge_agreement(all_ballots)
    summary["paired_method_differences"] = paired_differences(
        comparisons, bootstrap, seed
    )
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    for file, items in (
        ("detection", overview),
        ("proposals", proposals),
        ("documents", per_doc),
        ("locations", spans),
        ("annotations", annotation_items),
        ("ballots", all_ballots),
        ("target_decisions", targets),
        ("target_scores", summary["target_classification"]),
        ("judge_agreement", summary["ballot_agreement"]),
        ("paired_methods", summary["paired_method_differences"]),
    ):
        write_csv(out / (file + ".csv"), items)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--bootstrap", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=71129)
    parser.add_argument(
        "--run",
        action="append",
        help="Score only this run; repeat to compare aligned runs",
    )
    args = parser.parse_args()
    if args.bootstrap < 0:
        parser.error("bootstrap must be nonnegative")
    result = score(
        args.root / "evaluation/v1",
        args.root,
        args.out,
        args.bootstrap,
        args.seed,
        args.run,
    )
    print(f"Scored {len(result['runs'])} runs. Results: {args.out / 'summary.json'}")
