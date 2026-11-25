"""Replay the two-reviewer arm from evidence_arbiter predictions (no provider or evaluation keys).

python3 tools/detection_ablation.py path/to/predictions.jsonl > pair-only.jsonl
"""

import argparse
import json
from collections import Counter
from pathlib import Path


def pair_view(row):
    if row.get("failure"):
        return dict(row, ablation="without_arbiter")
    consensus = row["consensus"]
    pair_modes = {"evidence_arbiter": "evidence", "evidence_arbiter_v2": "evidence_v2",
                  "case_arbiter": "case_pair_replay"}
    pair_mode = pair_modes.get(consensus.get("verification"))
    if pair_mode is None:
        raise ValueError("Expected an evidence_arbiter or evidence_arbiter_v2 prediction")
    verdicts = []
    for verdict in row["verdicts"]:
        votes = [v for v in verdict["votes"] if v["role"] == "reviewer"]
        valid = [v for v in votes if v["status"] == "ok"]
        if len(votes) > 2 or len({v["family"] for v in votes}) != len(votes):
            raise ValueError("Expected at most two distinct reviewing families")
        counts = Counter(v["decision"] for v in valid)
        decision = next((d for d in ("flag", "keep") if counts[d] == 2), "uncertain")
        if decision != verdict["without_arbiter"]:
            raise ValueError("Stored pair decision does not match primary ballots")
        projected = {k: verdict[k] for k in ("id", "block_id", "start", "end", "quote")}
        projected.update(
            decision=decision,
            votes=votes,
            quorum_met=len(valid) == 2,
            architecture_diversity_confirmed=len({v["architecture"] for v in valid if v["architecture"]}) == 2,
        )
        verdicts.append(projected)
    status = consensus["status"]
    if verdicts:
        available = sum(v["quorum_met"] for v in verdicts)
        status = "complete" if available == len(verdicts) else "partial" if available else "unavailable"
    # Preserve the underlying search, hashes and errors. Costs of excluded calls
    # remain in the measured run's ledger. This derived row has no measured runtime.
    result = dict(
        row,
        ablation="without_arbiter",
        consensus=dict(consensus, families=consensus["families"][:2], verification=pair_mode, status=status),
        stages=[s for s in row["stages"] if s["stage"] != "arbiter"],
        verdicts=verdicts,
        findings=[v for v in verdicts if v["decision"] == "flag"],
    )
    for k in ("config_hash", "usage", "elapsed", "elapsed_s", "summary"):
        result.pop(k, None)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("predictions", type=Path)
    with parser.parse_args().predictions.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                print(json.dumps(pair_view(json.loads(line)), ensure_ascii=False))
