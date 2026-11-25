"""run_gold.py pinning of org rows. The two label files disagree on what `commit_sha` means for source "org";
reading the wrong one measured 37 of 60 on gold4 instead of the true 42 of 60 (24 Nov 2026)."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import _support  # noqa: F401  (puts src/ on sys.path)

RUN_GOLD = Path(__file__).resolve().parent / "golden" / "run_gold.py"
spec = importlib.util.spec_from_file_location("run_gold", RUN_GOLD)
run_gold = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run_gold)

REPO_SHA = "a" * 40
ORG_SHA = "b" * 40


def org_row(commit_sha: str, note: str, repo: str = "acme/widget") -> dict:
    return {"repo": repo, "commit_sha": commit_sha, "verdict": "banned", "source": "org", "evidence": [],
            "confidence": "high", "note": note}


class OrgRowShas(unittest.TestCase):
    def test_gold_convention_commit_sha_is_the_org_sha(self):
        row = org_row(ORG_SHA, f"org policy (repo sha {REPO_SHA[:10]})")
        self.assertEqual(run_gold.org_row_shas(row), (REPO_SHA[:10], ORG_SHA))

    def test_gold4_convention_commit_sha_is_the_repository_sha(self):
        row = org_row(REPO_SHA, f"org policy (org sha {ORG_SHA})")
        self.assertEqual(run_gold.org_row_shas(row), (REPO_SHA, ORG_SHA))
        self.assertEqual(run_gold.target_ref(row), REPO_SHA)
        self.assertEqual(run_gold.org_pins([row])["acme"], ORG_SHA)  # not the repository SHA

    def test_gold_convention_pins_the_owner_to_commit_sha(self):
        row = org_row(ORG_SHA, f"(repo sha {REPO_SHA})")
        self.assertEqual(run_gold.target_ref(row), REPO_SHA)
        self.assertEqual(run_gold.org_pins([row])["acme"], ORG_SHA)

    def test_a_note_without_a_marker_is_an_error_not_a_guess(self):
        with self.assertRaisesRegex(run_gold.GoldFormatError, "acme/widget.*commit_sha alone cannot say"):
            run_gold.org_row_shas(org_row(REPO_SHA, "org policy, no sha in the note"))
        with self.assertRaises(run_gold.GoldFormatError):
            run_gold.target_ref(org_row(REPO_SHA, ""))

    def test_both_markers_are_an_error(self):
        with self.assertRaisesRegex(run_gold.GoldFormatError, "both"):
            run_gold.org_row_shas(org_row(REPO_SHA, f"(repo sha {REPO_SHA}) (org sha {ORG_SHA})"))

    def test_plain_rows_ignore_the_markers(self):
        row = {**org_row(REPO_SHA, ""), "source": "repo"}
        self.assertEqual(run_gold.target_ref(row), REPO_SHA)
        self.assertNotIn("acme", run_gold.org_pins([row]))

    def test_two_rows_of_one_owner_must_agree(self):
        a = org_row(REPO_SHA, f"(org sha {ORG_SHA})", "acme/one")
        b = org_row(REPO_SHA, f"(org sha {'c' * 40})", "acme/two")
        with self.assertRaisesRegex(run_gold.GoldFormatError, "two different"):
            run_gold.org_pins([a, b])
        same = org_row(REPO_SHA, f"(org sha {ORG_SHA})", "acme/two")
        self.assertEqual(run_gold.org_pins([a, same])["acme"], ORG_SHA)

    def test_validate_rejects_duplicate_repositories(self):
        row = {**org_row(REPO_SHA, ""), "source": "repo"}
        with self.assertRaisesRegex(run_gold.GoldFormatError, "duplicate"):
            run_gold.validate_gold([row, dict(row)])


class RealLabelFile(unittest.TestCase):
    def test_every_org_row_of_the_published_gold_file_resolves(self):
        rows = run_gold.load_gold()
        run_gold.validate_gold(rows)
        orgs = [r for r in rows if r["source"] == "org"]
        self.assertTrue(orgs)
        for row in orgs:
            repo_sha, org_sha = run_gold.org_row_shas(row)
            self.assertEqual(org_sha, row["commit_sha"])  # this file's convention
            self.assertNotEqual(repo_sha, org_sha, row["repo"])


class MainRefusesAmbiguousFile(unittest.TestCase):
    def test_main_exits_with_an_error_before_any_fetch(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gold.jsonl"
            path.write_text(json.dumps(org_row(REPO_SHA, "no marker")) + "\n", encoding="utf-8")
            import contextlib
            import io
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                code = run_gold.main(["--gold", str(path), "--offline", "--cache", str(Path(tmp) / "c")])
            self.assertEqual(code, 2)
            self.assertIn("error: acme/widget", err.getvalue())
            self.assertFalse((Path(tmp) / "c").exists())


if __name__ == "__main__":
    unittest.main()
