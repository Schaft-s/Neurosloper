"""Offline regression tests for the independent benchmark scorer."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import score_benchmark as s


class ScoringTests(unittest.TestCase):
    def setUp(self):
        self.text = "Пролог 😀\r\n\n  конец"
        self.doc = {"id": "unit", "text": self.text}
        self.row = {
            "id": "unit",
            "input_sha256": s.digest(self.text),
            "revision_hash": s.digest(self.text),
            "seen": 2,
            "total": 2,
            "stages": [],
            "errors": [],
            "coverage": {},
            "consensus": {"status": "offline"},
            "block_offsets": {"0": 0, "1": self.text.index("  конец")},
            "candidates": [],
            "verdicts": [],
            "findings": [],
        }
        self.ref = {"decision": "keep", "spans": []}

    def test_greedy_loses_augmenting_path(self):
        # First prediction matches either reference at IoU 0.5, second only the first.
        first = [(0, 10), (0, 5)]
        gold = [(0, 5), (5, 10)]
        self.assertEqual(s.matching(first, gold), [(0, 1), (1, 0)])

    def test_half_open_touching_and_exact(self):
        self.assertEqual(s.matching([(0, 4)], [(4, 6)], "touching"), [])
        self.assertEqual(s.matching([(0, 4)], [(3, 9)], "touching"), [(0, 0)])
        self.assertEqual(s.matching([(0, 4)], [(3, 9)], "exact"), [])

    def test_missing_and_error_never_clean(self):
        for row in (None, {"input_sha256": s.digest(self.text), "failure": "timeout"}):
            state = s.decode(self.doc, row, "offline")
            record = s.doc_record(self.doc, self.ref, state, "doc", "test")
            self.assertEqual(record["pred_doc"], "unavailable")
            self.assertEqual(record["clean_return"], 0)
            self.assertEqual(record["correct_doc"], 0)
            self.assertNotEqual(record["search_status"], "ok")
        state = s.decode(self.doc, self.row, "offline")
        self.assertEqual(s.doc_label(state), "keep")

    def test_repeated_final_counts_but_raw_channel_duplication_does_not(self):
        item = {
            "block_id": "1",
            "start": 2,
            "end": 7,
            "quote": "конец",
            "decision": "flag",
        }
        self.row.update(
            candidates=[item, item], findings=[item, item], verdicts=[item, item]
        )
        state = s.decode(self.doc, self.row, "offline")
        pos = self.text.index("конец")
        ref = {
            "decision": "flag",
            "spans": [{"start": pos, "end": pos + 5, "decision": "flag"}],
        }
        rec = s.doc_record(self.doc, ref, state, "doc", "test")
        self.assertEqual((rec["predicted"], rec["iou50"], rec["fp"]), (2, 1, 1))
        self.assertEqual(
            (rec["raw_proposed"], rec["proposed"], rec["candidate_iou50"]), (2, 1, 1)
        )
        self.assertEqual(state["flags"][0], (pos, pos + 5))

    def test_multiline_reference_and_repeated_text_offsets(self):
        text = "один\nодин\nтри"
        b = s.blocks_for(text)
        pos = s.absolute(
            {"block_id": "1", "start": 0, "end": 4, "quote": "один"}, text, b, {"1": 5}
        )
        self.assertEqual(pos, (5, 9))
        # The reference crosses a line; no splitting or joining before the comparison.
        self.assertEqual(s.matching([pos], [(5, 13)]), [(0, 0)])
        self.assertNotEqual(pos, (0, 4))
        with self.assertRaises(ValueError):
            s.absolute(
                {"block_id": "1", "start": 0, "end": 4, "quote": "один"},
                text,
                b,
                {"1": 0},
            )

    def test_corrupt_anchor_and_duplicate_records(self):
        corrupt = dict(self.row, block_offsets={"0": 0, "1": 1})
        self.assertEqual(s.decode(self.doc, corrupt, "offline")["status"], "invalid")
        with self.assertRaises(ValueError):
            s.index([self.row, self.row])

    def test_missing_disposition_preserves_valid_search(self):
        candidate = {"block_id": "0", "start": 0, "end": 6, "quote": "Пролог"}
        self.row["candidates"] = [candidate]
        state = s.decode(self.doc, self.row, "offline")
        self.assertEqual(state["status"], "invalid")
        self.assertEqual(state["search_status"], "ok")
        self.assertEqual(state["candidates"], [(0, 6)])
        self.assertEqual(s.doc_label(state), "unavailable")

    def test_false_quorum_different_from_uncertain_vote(self):
        candidate = {
            "block_id": "0",
            "start": 0,
            "end": 6,
            "quote": "Пролог",
            "id": "location",
        }
        doc = dict(self.doc, target={"block_id": "0", "quote": "Пролог"})
        failed = dict(
            candidate,
            decision="uncertain",
            quorum_met=False,
            votes=[
                {"model": "a", "status": "ok", "decision": "uncertain"},
                {"model": "b", "status": "error"},
            ],
        )
        row = dict(
            self.row,
            verdicts=[failed],
            candidates=[candidate],
            consensus={"status": "unavailable"},
        )
        st = s.decode(doc, row, "fixed")
        self.assertEqual(st["status"], "no_quorum")
        raw, out = s.target_verdicts(
            doc,
            {"family": "x", "start": 0, "end": 6, "decision": "flag"},
            st,
            "fixed",
            ["a", "b"],
        )
        labels = {r["source"]: r["decision"] for r in out}
        self.assertEqual(
            labels, {"consensus": "unavailable", "a": "uncertain", "b": "unavailable"}
        )
        self.assertEqual(
            next(r for r in raw if r["source"] == "consensus")["delivered"], "uncertain"
        )

    def test_uncovered_channel_and_fallback(self):
        c = {
            "block_id": "0",
            "start": 0,
            "end": 6,
            "quote": "Пролог",
            "decision": "flag",
        }
        self.row.update(
            candidates=[c],
            verdicts=[c],
            findings=[c],
            consensus={"status": "complete"},
            coverage={"semantic": [["0", 0, 2]], "slopotron": []},
        )
        result = s.decode(self.doc, self.row, "union")
        self.assertEqual(result["search_status"], "partial")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(s.doc_label(result), "flag")

    def test_no_denominator_and_clustered_halves(self):
        rows = [{"cluster": "one", "x": 1, "n": 1}, {"cluster": "one", "x": 0, "n": 1}]
        ci = s.cluster_bootstrap(
            rows, ("x", "n"), lambda x, n: (s.ratio(x, n),), 80, 21
        )[0]
        self.assertEqual(ci["ci95"], [0.5, 0.5])
        empty = s.cluster_bootstrap(
            [], ("x", "n"), lambda x, n: (s.ratio(x, n),), 80, 21
        )[0]
        self.assertIsNone(empty["value"])
        self.assertEqual(empty["valid_resamples"], 0)

    def test_reverse_flip_not_full_pair_success(self):
        cases = [
            {
                "run": "r",
                "source": "a",
                "cluster": "pair",
                "id": "x",
                "gold": "flag",
                "decision": "keep",
                "retrieved": True,
            },
            {
                "run": "r",
                "source": "a",
                "cluster": "pair",
                "id": "y",
                "gold": "keep",
                "decision": "flag",
                "retrieved": True,
            },
        ]
        row = s.verdict_summaries(cases, 20, 7)[0]
        self.assertEqual(
            (row["both_correct"], row["reversed"], row["decisive_flip"]), (0, 1, 1)
        )

    def test_prospective_cluster_and_three_way_group(self):
        keys = {}
        targets = {}
        for cluster in ("a", "b"):
            for decision in ("flag", "keep", "uncertain"):
                key = cluster + decision
                keys[key] = {"group": cluster, "family": "common", "decision": decision}
                targets[key] = {"quote": cluster}
        self.assertEqual(s.scenario_field(keys, targets), "group")
        swapped = {
            k: dict(v, group=v["family"], family=v["group"]) for k, v in keys.items()
        }
        self.assertEqual(s.scenario_field(swapped, targets), "family")
        cases = [
            {
                "run": "trial",
                "source": "panel",
                "cluster": v["group"],
                "group_size": 3,
                "id": k,
                "gold": v["decision"],
                "decision": v["decision"],
                "retrieved": True,
            }
            for k, v in keys.items()
        ]
        cases[-1].update(decision="keep")
        score = s.verdict_summaries(cases, 20, 7)[0]
        self.assertEqual((score["groups"], score["three_way_groups_correct"]), (2, 1))
        self.assertEqual(score["uncertain_doc_recall"], 0.5)
        partial = s.verdict_summaries(cases[:2], 20, 7)[0]
        self.assertEqual((partial["groups"], partial["pairs"]), (0, 0))


if __name__ == "__main__":
    unittest.main()
