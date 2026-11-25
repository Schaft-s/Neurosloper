"""Score fidelity variants, author-selected spans, abstention and literal invariants."""

import csv
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
V = ROOT / "evaluation/v1"
SCORES = V / "scores"


def read(p):
    return [json.loads(l) for l in p.read_text().splitlines() if l]


def indexed(p):
    return {r["id"]: r for r in read(p)}


def main():
    SCORES.mkdir(exist_ok=True)
    summary = {}
    decisions = []
    changes = []
    for folder in sorted((V / "runs").iterdir()):
        if (
            not (folder / "config.json").exists()
            or not (folder / "predictions.jsonl").exists()
        ):
            continue
        cfg = json.loads((folder / "config.json").read_text())
        rs = read(folder / "predictions.jsonl")
        suite, mode = cfg["suite"], cfg["mode"]
        if mode == "fidelity":
            refs = indexed(V.parent / "rewrite_lockbox" / "keys.jsonl" if suite == "rewrite-lockbox" else V / "fidelity_keys.jsonl")
            stats = Counter()
            for r in rs:
                ref = refs[r["id"]]
                got = {row["id"]: row for row in r.get("results", [])}
                for vid, expected in ref["variants"].items():
                    row = got.get(vid, {})
                    label = expected["variant"]
                    value = row.get("status", "failed")
                    stats[label + "/" + value] += 1
                    decisions.append(
                        dict(
                            run=folder.name,
                            case=r["id"],
                            variant=label,
                            error_class=ref["error"],
                            expected=expected["expected"],
                            decision=value,
                            literal=row.get("literal_status"),
                            semantic=row.get("semantic_status"),
                            reason=row.get("reason"),
                        )
                    )
            summary[folder.name] = dict(total_contracts=len(rs), counts=dict(stats))
        elif mode == "rewrite":
            suite_file = (
                "style_inputs.jsonl"
                if suite == "style"
                else "rewrite_inputs.jsonl"
                if suite == "rewrite"
                else "fidelity_inputs.jsonl"
            )
            source = indexed(V / suite_file)
            stats = Counter()
            for r in rs:
                d = source[r["id"]]
                text = d["text"]
                q = d["target"]["quote"]
                a = text.index(q)
                b = a + len(q)
                if not r.get("changes"):
                    status = "failed" if r.get("failure") else "refused"
                    reason = r.get("failure") or " / ".join(
                        x.get("reason", "") for x in r.get("refusals", [])
                    )
                    after = None
                else:
                    after = r["changes"][0]["after"]
                    prefix, tail = text[:a], text[b:]
                    ok = (
                        after.startswith(prefix)
                        and after.endswith(tail)
                        and len(after) >= len(prefix) + len(tail)
                        and all(
                            text.count(x) == after.count(x) for x in d.get("locked", [])
                        )
                    )
                    status = "offered" if ok else "out_of_scope"
                    reason = r["changes"][0].get("reason")
                stats[status] += 1
                changes.append(
                    dict(
                        run=folder.name,
                        case=r["id"],
                        style=d["style"],
                        status=status,
                        original=text,
                        candidate=after,
                        reason=reason,
                    )
                )
            summary[folder.name] = {"cases": len(rs), "counts": dict(stats)}
    for name, rs in [("fidelity", decisions), ("rewrite", changes)]:
        if rs:
            with (SCORES / (name + ".csv")).open("w", newline="") as f:
                t = csv.DictWriter(f, fieldnames=list(rs[0]))
                t.writeheader()
                t.writerows(rs)
    (SCORES / "rewriting.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
