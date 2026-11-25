import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _support
from _fakes import ORG_SHA, SHA, FakeGithub
from oss_ai_policy_check import cli


def run(argv, client=None):
    """Run cli.main offline: the GitHub transport is replaced by `client`."""
    out, err = io.StringIO(), io.StringIO()
    patched = mock.patch.object(cli, "fetch_snapshot",
                                lambda repo, ref=None, c=None, **kw: cli_fetch(repo, ref, client, **kw))
    with patched, contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(argv)
        except SystemExit as exc:  # argparse usage errors
            code = exc.code
    return code, out.getvalue(), err.getvalue()


def cli_fetch(repo, ref, client, **kw):
    from oss_ai_policy_check.fetch import fetch_snapshot
    return fetch_snapshot(repo, ref, client or FakeGithub(), **kw)


class AnalyzeTests(unittest.TestCase):
    """End-to-end (fake transport): the four original collect() scenarios plus verdict mapping."""

    def analyze(self, fake, repo="a/b"):
        with mock.patch.object(cli, "fetch_snapshot", lambda r, ref=None, c=None, **kw: cli_fetch(r, ref, fake, **kw)):
            return cli.analyze(repo)

    def test_failed_read_visible(self):
        r = self.analyze(FakeGithub(fail=True))
        self.assertFalse(r["complete"])
        self.assertTrue(r["warnings"])
        self.assertEqual(r["verdict"], "no-policy-found")

    def test_truncated_visible(self):
        self.assertFalse(self.analyze(FakeGithub(truncated=True))["complete"])

    def test_pinned_commit_and_success(self):
        r = self.analyze(FakeGithub())
        self.assertEqual(r["commit_sha"], SHA)
        self.assertTrue(r["complete"])
        self.assertEqual(r["verdict"], "disclosure-required")
        self.assertEqual(r["evidence"][0]["url"], f"https://github.com/a/b/blob/{SHA}/.github/AI_POLICY.md#L1")
        self.assertEqual(_support.schema_errors(r), [])

    def test_invalid_repo(self):
        with self.assertRaises(ValueError):
            self.analyze(FakeGithub(), repo="a/b;bad")


class MainTests(unittest.TestCase):
    def test_json_format_and_exit_code(self):
        code, out, _ = run(["a/b", "--format", "json"])
        self.assertEqual(code, 0)  # disclosure-required exits 0 without --fail-on
        self.assertEqual(json.loads(out)["verdict"], "disclosure-required")

    def test_fail_on_disclosure(self):
        self.assertEqual(run(["a/b", "--fail-on", "disclosure-required"])[0], 11)
        self.assertEqual(run(["a/b", "--fail-on", "restricted"])[0], 0)

    def test_banned_and_restricted_codes(self):
        banned = FakeGithub(files={".github/AI_POLICY.md": b"We do not accept AI-generated contributions."})
        restricted = FakeGithub(files={".github/AI_POLICY.md": b"We accept AI. Please explain your work in your own human voice."})
        self.assertEqual(run(["a/b"], banned)[0], 20)
        self.assertEqual(run(["a/b"], restricted)[0], 10)

    def test_needs_review_exit_code_12_only_with_fail_on(self):
        text = b"AI-assisted code must pass the same review bar as human-written contributions."
        fake = lambda: FakeGithub(files={".github/AI_POLICY.md": text})  # noqa: E731
        code, out, _ = run(["a/b", "--format", "json"], fake())
        data = json.loads(out)
        self.assertEqual((code, data["verdict"], data["review_reason"]), (0, "needs-review", "no-rule-decision"))
        self.assertEqual(_support.schema_errors(data), [])
        self.assertEqual(run(["a/b", "--fail-on", "needs-review"], fake())[0], 12)
        self.assertEqual(run(["a/b", "--fail-on", "disclosure-required"], fake())[0], 0)
        code, out, _ = run(["a/b", "--fail-on", "needs-review", "--format", "markdown"], fake())
        self.assertEqual(code, 12)
        self.assertIn("### Needs human review", out)

    def test_fail_on_needs_review_still_fails_on_stricter_verdicts(self):
        self.assertEqual(run(["a/b", "--fail-on", "needs-review"])[0], 11)  # the fake repo is disclosure-required

    def test_incomplete_undecided_exits_3(self):
        self.assertEqual(run(["a/b"], FakeGithub(fail=True))[0], 3)

    def test_usage_errors_exit_2(self):
        self.assertEqual(run(["not-a-repo"])[0], 2)
        self.assertEqual(run(["a/b", "--format", "xml"])[0], 2)
        self.assertEqual(run(["a/b", "--fail-on", "permitted"])[0], 2)
        self.assertEqual(run(["a/b", "--path", ".", "--ref", "main"])[0], 2)
        self.assertEqual(run(["a/b", "--path", "does/not/exist"])[0], 2)

    def test_markdown_and_sarif_formats_via_cli(self):
        code, out, err = run(["a/b", "--format", "markdown"])
        self.assertEqual((code, err), (0, ""))
        self.assertIn("## AI policy check: DISCLOSURE-REQUIRED", out)
        code, out, err = run(["a/b", "--format", "sarif", "--fail-on", "disclosure-required"])
        self.assertEqual((code, err), (11, ""))  # output is written, the exit code still follows the verdict
        doc = json.loads(out)
        self.assertEqual(doc["version"], "2.1.0")
        self.assertEqual(doc["runs"][0]["results"][0]["level"], "note")

    def test_local_path_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "AGENTS.md").write_text("All AI usage must be disclosed.", encoding="utf-8")
            Path(tmp, ".git").mkdir()
            Path(tmp, ".git", "HEAD").write_text(SHA + "\n", encoding="utf-8")
            code, out, _ = run(["a/b", "--path", tmp, "--format", "json"])
        data = json.loads(out)
        self.assertEqual((code, data["verdict"], data["commit_sha"]), (0, "disclosure-required", SHA))
        self.assertEqual(data["files_checked"], [{"path": "AGENTS.md", "status": "read"}])
        self.assertEqual(_support.schema_errors(data), [])

    def test_no_cache_and_no_org_reach_fetch_snapshot(self):
        seen = []

        def spy(repo, ref=None, client=None, **kw):
            seen.append(kw)
            return cli_fetch(repo, ref, None, **kw)

        for argv, expected in ((["a/b"], dict(no_cache=False, org=True)),
                               (["a/b", "--no-cache"], dict(no_cache=True, org=True)),
                               (["a/b", "--no-org"], dict(no_cache=False, org=False)),
                               (["a/b", "--no-cache", "--no-org"], dict(no_cache=True, org=False))):
            seen.clear()
            with mock.patch.object(cli, "fetch_snapshot", spy), contextlib.redirect_stdout(io.StringIO()):
                cli.main(argv)
            self.assertEqual(seen, [expected], argv)

    def test_no_org_skips_org_repository_end_to_end(self):
        org = {"AI_POLICY.md": b"We do not accept AI-generated contributions."}
        files = {"CONTRIBUTING.md": b"Be kind."}
        code, out, _ = run(["a/b", "--format", "json"], FakeGithub(files=dict(files), org_files=dict(org)))
        with_org = json.loads(out)
        self.assertEqual((code, with_org["verdict"]), (20, "banned"))
        self.assertEqual(with_org["org_commit_sha"], ORG_SHA)
        self.assertEqual(with_org["evidence"][0]["file"], "org:.github/AI_POLICY.md")
        self.assertEqual(with_org["evidence"][0]["url"],
                         f"https://github.com/a/.github/blob/{ORG_SHA}/AI_POLICY.md#L1")
        self.assertEqual(_support.schema_errors(with_org), [])
        code, out, _ = run(["a/b", "--format", "json", "--no-org"],
                           FakeGithub(files=dict(files), org_files=dict(org)))
        without = json.loads(out)
        self.assertEqual((code, without["verdict"], without["org_commit_sha"]), (0, "no-policy-found", None))

    def test_json_report_has_nullable_org_commit_sha(self):
        data = json.loads(run(["a/b", "--format", "json"])[1])
        self.assertIn("org_commit_sha", data)
        self.assertIsNone(data["org_commit_sha"])

    def test_version_flag(self):
        code, out, _ = run(["--version"])
        self.assertEqual(code, 0)
        self.assertIn("oss-ai-policy-check 0.3.1", out)


if __name__ == "__main__":
    unittest.main()
