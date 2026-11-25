"""Agent instruction files: operating procedure for the agent versus the repository's rule for contributors.

tests/golden/agent_file_cases.json holds short excerpts (the sentence and its paragraph, with the repository, the
pinned commit and the source lines) of real repositories that the classifier got wrong or must keep getting right
(25 Nov 2026). `false-positive` rows were reported as restricted or banned for text that restricts nobody;
`false-negative` rows are real closures it missed (outside pull requests closed); `kept` rows are real limits and
conditions (autonomous agents, AI attribution trailers, a disclosure template, "never create a PR") that the same
change must not lose. The excerpts are the reproducible part of the evidence; the whole-repository
verdicts are measured with tests/golden/run_gold.py. gold.jsonl is not touched: its published hash stays valid."""
import json
import unittest
from pathlib import Path

import _support  # noqa: F401  (puts src/ on sys.path)
from oss_ai_policy_check.classify import classify_documents

CASES = Path(__file__).resolve().parent / "golden" / "agent_file_cases.json"
ROWS = json.loads(CASES.read_text(encoding="utf-8"))


class AgentFileCases(unittest.TestCase):
    def test_file_is_well_formed(self):
        ids = [row["id"] for row in ROWS]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreaterEqual(sum(row["kind"] == "false-positive" for row in ROWS), 7)
        self.assertGreaterEqual(sum(row["kind"] == "kept" for row in ROWS), 15)
        self.assertGreaterEqual(sum(row["kind"] == "false-negative" for row in ROWS), 2)
        for row in ROWS:
            self.assertRegex(row["commit_sha"], r"^[0-9a-f]{40}$", row["id"])
            self.assertTrue(row["documents"], row["id"])
            self.assertTrue(("expected" in row and row["expected"]) or row.get("not_verdicts"), row["id"])

    def test_every_case(self):
        for row in ROWS:
            with self.subTest(row["id"]):
                result = classify_documents(row["documents"], owner=row["repo"].split("/")[0])
                rules = sorted({f'{e["file"]}:{e["line"]} {e["rule"]}->{e["signal"]}' for e in result["evidence"]})
                if row.get("expected"):
                    self.assertEqual(result["verdict"], row["expected"], f'{row["repo"]}: {rules}')
                else:  # an abstention is acceptable; a wrong restriction is not
                    self.assertNotIn(result["verdict"], row["not_verdicts"], f'{row["repo"]}: {rules}')

    def test_defect_rows_were_wrong_before(self):
        # the rows document a real defect: the verdict the old rules gave is recorded and differs from the label
        for row in ROWS:
            if row["kind"] == "false-positive":
                self.assertIn(row["before"], ("restricted", "banned"), row["id"])
                self.assertNotEqual(row["before"], row.get("expected"), row["id"])
            if row["kind"] == "false-negative":
                self.assertEqual(row["before"], "no-policy-found", row["id"])
                self.assertEqual(row["expected"], "restricted", row["id"])


if __name__ == "__main__":
    unittest.main()
