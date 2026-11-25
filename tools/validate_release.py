"""Validate the Po Delu evaluation release without a model or network access."""

import argparse
import json
import re
from collections import Counter
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
V = ROOT / "evaluation/v1"
DATA = ROOT / "data"


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def digest(t):
    return sha256(t.encode() if isinstance(t, str) else t).hexdigest()


def keyed(rs):
    result = {r["id"]: r for r in rs}
    if len(result) != len(rs):
        raise ValueError("Duplicate id")
    return result


def check(ok, msg):
    if not ok:
        raise ValueError(msg)


def shingles(text, n=5):
    words = re.findall(r"[\w]+", text.casefold())
    return {tuple(words[i : i + n]) for i in range(len(words) - n + 1)}


def notes(p):
    return keyed(rows(p))


def spans(doc, review):
    check(digest(doc) == review["source_sha256"], "Wrong source digest " + review["id"])
    for s in review["spans"]:
        check(
            0 <= s["start"] < s["end"] <= len(doc)
            and doc[s["start"] : s["end"]] == s["quote"],
            review["id"] + " quote",
        )
        check(
            s["decision"] in ("flag", "uncertain") and s["reason"] and s["normal"],
            review["id"] + " invalid review",
        )
    ds = {s["decision"] for s in review["spans"]}
    check(
        review["decision"]
        == ("flag" if "flag" in ds else "uncertain" if ds else "keep"),
        review["id"] + " wrong verdict",
    )


def validate():
    source = notes(DATA / "genre_blind.jsonl")
    provenance = notes(DATA / "genre_provenance.jsonl")
    check(
        len(source) == len(provenance) == 90 and source.keys() == provenance.keys(),
        "incomplete genre corpus",
    )
    for name in [
        V / "review_a.jsonl",
        V.parent / "review_b.jsonl",
        V / "adjudicated.jsonl",
    ]:
        r = notes(name)
        check(r.keys() == source.keys(), "Missing reference " + name.name)
        for i in r:
            spans(source[i]["text"], r[i])
    history = []
    for f in [
        "ru_annotated",
        "random40_reviews",
        "real_revision_requests",
        "ru_user_feedback",
        "register_pairs",
    ]:
        for j, r in enumerate(rows(DATA / (f + ".jsonl"))):
            t = r.get("text") or r.get("original")
            if t:
                history.append(
                    (
                        f,
                        r.get("id", j),
                        r.get("hash") or r.get("conversation_hash"),
                        digest(t),
                        shingles(t),
                    )
                )
    overlaps = []
    near = []
    docs = [
        (i, provenance[i]["hash"], digest(r["text"]), shingles(r["text"]))
        for i, r in source.items()
    ]
    for n, (i, h, sha, sh) in enumerate(docs):
        targets = history + [("within", j, hh, ss, q) for j, hh, ss, q in docs[:n]]
        for f, j, other, oldhash, q in targets:
            if h == other or sha == oldhash:
                overlaps.append([i, f, j, "conversation" if h == other else "text"])
            common = len(sh & q)
            union = len(sh) + len(q) - common
            if union and common / union >= 0.30:
                near.append([i, f, j, round(common / union, 4)])
    for n in ("contrast", "fidelity"):
        d = notes(V / (n + "_inputs.jsonl"))
        k = notes(V / (n + "_keys.jsonl"))
        check(d.keys() == k.keys(), n)
        for i, r in d.items():
            kk = k[i]
            check(digest(r["text"]) == kk["source_sha256"], i)
            if n == "contrast":
                check(r["text"][kk["start"] : kk["end"]] == kk["quote"], i)
            else:
                check({c["id"] for c in r["candidates"]} == set(kk["variants"]), i)
                check(len({c["after"] for c in r["candidates"]}) == 3, i)
                check(all(t in r["text"] for t in r["locked"]), i)
    ext = rows(V / "external.jsonl")
    for r in ext:
        check(
            r["before"]["sha"] != r["after"]["sha"]
            and r["source"]
            and r["scope"] == "partial_pair",
            r["id"],
        )
        for side in ("before", "after"):
            s = r[side]
            check(
                digest(s["text"]) == s["text_sha256"]
                and s["end_in_file"] - s["start_in_file"] == len(s["text"]),
                r["id"],
            )
            for x in s["spans"]:
                check(s["text"][x["start"] : x["end"]] == x["quote"], r["id"])
    personal = notes(V / "reader_feedback.jsonl")
    original = notes(DATA / "ru_user_feedback.jsonl")
    check(original.keys() == personal.keys() and len(original) == 37, "reader coverage")
    for i, k in personal.items():
        row = original[i]
        check(
            k["source_sha256"] == digest(row["text"])
            and k["user_feedback_sha256"] == digest(row["user_feedback"]),
            i,
        )
        check(
            k["direct_evidence"] in row["user_feedback"]
            and not k["is_span_gold"]
            and not k["external_egress"],
            i,
        )
        if k.get("possible_original_quote"):
            check(k["possible_original_quote"] in row["text"], i)
    for d in rows(V / "rewrite_inputs.jsonl"):
        k = notes(V / "rewrite_keys.jsonl")[d["id"]]
        check(
            d["text"][k["allowed_start"] : k["allowed_end"]] == d["target"]["quote"]
            and digest(d["text"]) == k["source_sha256"],
            d["id"],
        )
    group = V.parent / "lockbox"
    for line in (group / "SHA256SUMS").read_text().splitlines():
        sha, f = line.split()
        check(digest((group / f).read_bytes()) == sha, f)
    lo, lk, lt = [
        notes(group / f) for f in ("inputs.jsonl", "keys.jsonl", "targets.jsonl")
    ]
    check(lo.keys() == lt.keys() == lk.keys(), "prospective ids")
    for i, t in lt.items():
        check(lo[i]["text"][t["start"] : t["end"]] == t["quote"], i)
        for span in lk[i]["spans"]:
            check(lo[i]["text"][span["start"] : span["end"]] == span["quote"], i)
    rewrite_lockbox = V.parent / "rewrite_lockbox"
    for line in (rewrite_lockbox/"SHA256SUMS").read_text().splitlines():
        checksum, filename = line.split()
        check(digest((rewrite_lockbox/filename).read_bytes())==checksum,filename)
    rw = notes(rewrite_lockbox/"inputs.jsonl"); rk=notes(rewrite_lockbox/"keys.jsonl")
    check(len(rw)==8 and rw.keys()==rk.keys(),"independent rewrite coverage")
    for i,r in rw.items():
        check(digest(r["text"])==rk[i]["source_sha256"] and {c["id"] for c in r["candidates"]}==set(rk[i]["variants"]),i)
    # Known highlights in the legacy partial Wiki layer use their original independent coordinates.
    wiki = rows(DATA / "wiki_full_marks.jsonl")
    for r in wiki:
        for s in r["spans"]:
            check(r["text"][s["start"] : s["end"]] == s["quote"], r["id"])
    return {
        "genre_docs": len(docs),
        "history_rows_checked": len(history),
        "exact_overlap": overlaps,
        "word_5gram_jaccard_at_least_0.30": near,
        "synthetic_contrast": len(rows(V / "contrast_keys.jsonl")),
        "fidelity_contracts": len(rows(V / "fidelity_keys.jsonl")),
        "style_requests": len(rows(V / "style_inputs.jsonl")),
        "targeted_rewrites": len(rows(V / "rewrite_keys.jsonl")),
        "prospective_docs": len(lo),
        "independent_rewrite_contracts":len(rw),
        "reader_signals": len(personal),
        "external_revision_pairs": len(ext),
        "legacy_wiki_known_spans": sum(len(r["spans"]) for r in wiki),
        "adjudicated": dict(
            Counter(r["decision"] for r in rows(V / "adjudicated.jsonl"))
        ),
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--freeze", action="store_true")
    a = p.parse_args()
    report = validate()
    manifest = V / "MANIFEST.json"
    paths = list(V.glob("*.jsonl")) + [
        DATA / "genre_blind.jsonl",
        DATA / "genre_provenance.jsonl",
        V.parent / "review_b.jsonl",
        V.parent / "lockbox" / "SHA256SUMS",
        V.parent / "rewrite_lockbox" / "SHA256SUMS",
        V / "PROSPECTIVE.json",
        ROOT / "SKILL.md",
    ]
    snap = {
        str(path.relative_to(ROOT)): digest(path.read_bytes()) for path in sorted(paths)
    }
    if a.freeze:
        manifest.write_text(
            json.dumps({"files": snap, "checks": report}, ensure_ascii=False, indent=2)
            + "\n"
        )
    else:
        pinned = json.loads(manifest.read_text())
        check(
            pinned == {"files": snap, "checks": report}, "Release differs from manifest"
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
