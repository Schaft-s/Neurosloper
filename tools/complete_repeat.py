"""Combine only previously missing records from explicit retry runs.

Original attempts remain in their directories. Costs live in the independent
request ledger; an assembled view does not create new billed requests.
"""

import argparse
import json
from pathlib import Path


def read_run(folder):
    config = json.loads((folder / "config.json").read_text())
    rows = [
        json.loads(line)
        for line in (folder / "predictions.jsonl").read_text().splitlines()
    ]
    if len({r["id"] for r in rows}) != len(rows):
        raise ValueError(f"Duplicate predictions in {folder}")
    return config, rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("original", type=Path)
    parser.add_argument("retry", type=Path)
    parser.add_argument("output", type=Path)
    a = parser.parse_args()
    base, primary = read_run(a.original)
    other, additional = read_run(a.retry)
    for name in (
        "suite",
        "mode",
        "model",
        "panel",
        "backend",
        "protocol",
        "rewriter",
        "detector_variant",
    ):
        if base.get(name) != other.get(name):
            raise ValueError(f"Config differs: {name}")
    sources = dict(zip(base["ids"], base["input_hashes"]))
    requests = dict(zip(base["ids"], base["request_hashes"]))
    seen = {r["id"] for r in primary}
    primary_prompts = {
        (s["stage"], s["prompt_hash"], s["model"])
        for r in primary
        for s in r.get("stages", [])
    }
    for row in additional:
        id = row["id"]
        if (
            id in seen
            or row["input_sha256"] != sources[id]
            or requests[id] != dict(zip(other["ids"], other["request_hashes"]))[id]
        ):
            raise ValueError(id)
        for s in row.get("stages", []):
            if (s["stage"], s["prompt_hash"], s["model"]) not in primary_prompts:
                raise ValueError(f"Effective prompt differs: {id}")
        seen.add(id)
    if seen != set(base["ids"]):
        raise ValueError("Run still incomplete")
    a.output.mkdir(exist_ok=False)
    meta = {
        **base,
        "revision": a.output.name,
        "assembled_from": [a.original.name, a.retry.name],
        "retry_code_hash": other["code_hash"],
        "retry_ids": [r["id"] for r in additional],
    }
    (a.output / "config.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n"
    )
    by_id = {r["id"]: r for r in primary + additional}
    (a.output / "predictions.jsonl").write_text(
        "".join(json.dumps(by_id[i], ensure_ascii=False) + "\n" for i in base["ids"])
    )
    print(f"{len(primary)} original + {len(additional)} retry")


if __name__ == "__main__":
    main()
