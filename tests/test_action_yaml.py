"""Structure and safety checks for action.yml and the GitHub workflows (offline, stdlib only).

The checks are line based so they run without PyYAML; when PyYAML is importable the files are
also parsed and checked structurally. The checks skip when the files are absent (an sdist does
not ship `.github/`).
"""
import contextlib
import io
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

ROOT = Path(__file__).resolve().parent.parent
ACTION = ROOT / "action.yml"
WORKFLOWS = ROOT / ".github" / "workflows"
DEPENDABOT = ROOT / ".github" / "dependabot.yml"

try:
    import yaml  # optional
except ImportError:  # pragma: no cover - depends on the environment
    yaml = None

SHA_REF = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")
USES_LINE = re.compile(r"^\s*(?:-\s+)?uses:\s*(?P<ref>[^\s#]+)")
RUN_LINE = re.compile(r"^(?P<indent>\s*)(?P<dash>-\s+)?run:\s*(?P<rest>.*)$")
UNSAFE_EXPRESSION = re.compile(
    r"\$\{\{[^}]*(?:\binputs\.|\bgithub\.event\b|\bgithub\.head_ref\b|\bsteps\.[^}]*\boutputs\b|"
    r"\bneeds\.[^}]*\boutputs\b|\bsecrets\.|\bgithub\.token\b)[^}]*\}\}")


def uses_refs(text):
    """Every `uses:` target in a workflow or action file."""
    return [m.group("ref") for line in text.splitlines() if (m := USES_LINE.match(line))]


def unpinned_uses(text):
    """`uses:` targets that are neither local (`./`) nor pinned to a full 40-hex commit SHA."""
    return [ref for ref in uses_refs(text) if not ref.startswith("./") and not SHA_REF.match(ref)]


def run_blocks(text):
    """The shell text of every `run:` step (inline value or block scalar)."""
    lines = text.splitlines()
    blocks = []
    i = 0
    while i < len(lines):
        m = RUN_LINE.match(lines[i])
        if not m:
            i += 1
            continue
        key_col = len(m.group("indent")) + (len(m.group("dash")) if m.group("dash") else 0)
        rest = m.group("rest")
        body = []
        if rest[:1] in ("|", ">"):
            i += 1
            while i < len(lines):
                line = lines[i]
                if line.strip() and len(line) - len(line.lstrip()) <= key_col:
                    break
                body.append(line)
                i += 1
        else:
            body.append(rest)
            i += 1
        blocks.append("\n".join(body))
    return blocks


def unsafe_run_expressions(text):
    """Untrusted-context expressions interpolated into shell text."""
    return [hit.group(0) for block in run_blocks(text) for hit in UNSAFE_EXPRESSION.finditer(block)]


class ScannerSelfTest(unittest.TestCase):
    """The scanners must catch what they claim to catch."""

    def test_unpinned_detection(self):
        text = ("steps:\n"
                "  - uses: actions/checkout@v4\n"
                "  - uses: actions/setup-python@" + "a" * 40 + " # v5\n"
                "  - uses: ./\n"
                "  - uses: docker://alpine:3\n"
                "    with: {}\n")
        self.assertEqual(unpinned_uses(text), ["actions/checkout@v4", "docker://alpine:3"])

    def test_short_sha_is_unpinned(self):
        self.assertEqual(unpinned_uses("  - uses: a/b@" + "a" * 39), ["a/b@" + "a" * 39])

    def test_run_block_extraction(self):
        text = ("steps:\n"
                "  - name: x\n"
                "    run: |\n"
                "      echo one\n"
                "\n"
                "      echo two\n"
                "    env:\n"
                "      A: ${{ inputs.a }}\n"
                "  - run: echo inline\n"
                "  - name: y\n"
                "    run: >\n"
                "      folded\n")
        self.assertEqual(run_blocks(text), ["      echo one\n\n      echo two", "echo inline", "      folded"])

    def test_unsafe_expression_detection(self):
        bad = ("steps:\n"
               "  - run: echo ${{ inputs.repo }}\n"
               "  - run: |\n"
               "      echo '${{ github.event.pull_request.title }}'\n"
               "      echo ${{ steps.a.outputs.b }} ${{ secrets.X }}\n")
        self.assertEqual(len(unsafe_run_expressions(bad)), 4)

    def test_env_use_is_not_flagged(self):
        good = ("steps:\n"
                "  - run: echo \"$REPO\"\n"
                "    env:\n"
                "      REPO: ${{ inputs.repo }}\n"
                "      TITLE: ${{ github.event.pull_request.title }}\n")
        self.assertEqual(unsafe_run_expressions(good), [])

    def test_harmless_expression_in_run_is_allowed(self):
        self.assertEqual(unsafe_run_expressions("  - run: echo ${{ matrix.os }}\n"), [])


def _workflow_files():
    return sorted(WORKFLOWS.glob("*.yml")) if WORKFLOWS.is_dir() else []


@unittest.skipUnless(ACTION.is_file(), "action.yml is not part of this tree")
class ActionFilesTest(unittest.TestCase):
    def _files(self):
        return [ACTION, *_workflow_files()]

    def test_expected_workflows_exist(self):
        for name in ("ci.yml", "self-test.yml", "release.yml", "scorecard.yml"):
            self.assertTrue((WORKFLOWS / name).is_file(), name)
        self.assertTrue(DEPENDABOT.is_file())

    def test_every_remote_uses_is_pinned_to_a_full_sha(self):
        for path in self._files():
            text = path.read_text(encoding="utf-8")
            self.assertTrue(uses_refs(text), f"{path.name}: no uses: found, scanner is blind")
            self.assertEqual(unpinned_uses(text), [], path.name)

    def test_release_build_tools_are_pinned(self):
        text = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
        self.assertRegex(text, r"pip install [^\n]*\bbuild==\d+\.\d+\.\d+\b")
        self.assertRegex(text, r"pip install [^\n]*\btwine==\d+\.\d+\.\d+\b")

    def test_self_test_accepts_every_verdict(self):
        from oss_ai_policy_check.contract import VERDICTS
        text = (WORKFLOWS / "self-test.yml").read_text(encoding="utf-8")
        accepted = re.search(r"case \"\$\{VERDICT\}\" in\n\s+([a-z|-]+\|[a-z|-]+)\) ;;", text)
        self.assertIsNotNone(accepted)
        self.assertEqual(set(accepted.group(1).split("|")), set(VERDICTS))

    def test_no_untrusted_expression_in_run(self):
        for path in self._files():
            text = path.read_text(encoding="utf-8")
            if path.name != "scorecard.yml":  # the Scorecard workflow only has uses: steps
                self.assertTrue(run_blocks(text), f"{path.name}: no run: found, scanner is blind")
            self.assertEqual(unsafe_run_expressions(text), [], path.name)

    def test_workflows_default_to_read_only_contents(self):
        for path in _workflow_files():
            text = path.read_text(encoding="utf-8")
            self.assertRegex(text, r"(?m)^permissions:\n  contents: read\n", path.name)
            self.assertNotRegex(text, r"(?m)^\s+(?:contents|security-events|actions): write", path.name)
            if path.name not in ("release.yml", "scorecard.yml"):
                self.assertNotRegex(text, r"(?m)^\s+id-token: write", path.name)

    def test_scorecard_workflow_is_least_privilege_and_publishes(self):
        text = (WORKFLOWS / "scorecard.yml").read_text(encoding="utf-8")
        self.assertEqual(len(re.findall(r"(?m)^\s+id-token: write", text)), 1)
        self.assertIn("publish_results: true", text)
        self.assertRegex(text, r"ossf/scorecard-action@[0-9a-f]{40}")

    def test_release_workflow_gives_id_token_only_to_the_publish_job(self):
        text = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
        self.assertEqual(len(re.findall(r"(?m)^\s+id-token: write", text)), 1)
        publish = text.split("\n  publish:\n", 1)[1]
        self.assertIn("id-token: write", publish)
        self.assertIn("name: pypi", publish)
        self.assertIn("pypa/gh-action-pypi-publish@", publish)

    def test_checkout_does_not_persist_credentials(self):
        for path in _workflow_files():
            text = path.read_text(encoding="utf-8")
            for match in re.finditer(r"uses: actions/checkout@\S+[^\n]*\n((?:[ ]+(?!-)[^\n]*\n)*)", text):
                self.assertIn("persist-credentials: false", match.group(1), path.name)

    def test_token_is_only_given_through_env_or_with(self):
        text = ACTION.read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), 1):
            if "inputs.github-token" in line:
                self.assertRegex(line, r"^\s+(GH_TOKEN|GITHUB_TOKEN|token):", f"action.yml:{number}")


    def test_action_runs_in_isolation_from_the_workspace(self):
        """A pull request controls the workspace (the current directory); the token-holding step must not import from it."""
        text = ACTION.read_text(encoding="utf-8")
        self.assertNotRegex(text, r"pip\s+install", "the action must not install anything")
        self.assertNotIn("Install oss-ai-policy-check", text)
        self.assertRegex(text, r"(?m)^\s+ACTION_PATH: \$\{\{ github\.action_path \}\}\s*$")
        invocations = [line.strip() for block in run_blocks(text) for line in block.splitlines()
                       if re.search(r"(?:^|\s)python3?\s", line)]
        self.assertTrue(invocations)
        for line in invocations:
            self.assertRegex(line, r'^PYTHONPATH="\$\{ACTION_PATH\}/src" python -P ', line)
        # `-P` needs Python 3.11; the action pins its own interpreter
        pinned = re.search(r'python-version: "(\d+)\.(\d+)"', text)
        self.assertIsNotNone(pinned)
        self.assertGreaterEqual((int(pinned.group(1)), int(pinned.group(2))), (3, 11))

    def test_no_step_after_the_token_step_installs_from_the_workspace(self):
        text = ACTION.read_text(encoding="utf-8")
        self.assertNotRegex(text, r"(?i)pip")
        self.assertNotRegex(text, r"github\.workspace")

    def test_failure_step_runs_on_enforce_code(self):
        text = ACTION.read_text(encoding="utf-8")
        self.assertIn("if: steps.check.outputs.enforce-code != '0'", text)

    def test_ci_matrix_and_commands(self):
        text = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
        for needle in ("ubuntu-latest", "windows-latest", "macos-latest", '"3.10"', '"3.11"', '"3.12"', '"3.13"',
                       "unittest discover -s tests", "pip install", "oss-ai-policy-check --help"):
            self.assertIn(needle, text)

    def test_self_test_uses_local_action(self):
        text = (WORKFLOWS / "self-test.yml").read_text(encoding="utf-8")
        self.assertRegex(text, r"(?m)^\s+uses: \./\s*$")
        self.assertIn("pymeasure/pymeasure", text)
        self.assertRegex(text, r'fail-on: ""')

    def test_dependabot_covers_actions_and_pip(self):
        text = DEPENDABOT.read_text(encoding="utf-8")
        for ecosystem in ("github-actions", "pip"):
            self.assertIn(f"package-ecosystem: {ecosystem}", text)
        self.assertEqual(text.count("interval: weekly"), 2)


@unittest.skipUnless(ACTION.is_file() and yaml is not None, "needs action.yml and PyYAML")
class ParsedYamlTest(unittest.TestCase):
    def test_all_files_parse(self):
        for path in [ACTION, DEPENDABOT, *_workflow_files()]:
            with self.subTest(path=path.name):
                self.assertIsInstance(yaml.safe_load(path.read_text(encoding="utf-8")), dict)

    def test_action_shape(self):
        data = yaml.safe_load(ACTION.read_text(encoding="utf-8"))
        self.assertEqual(data["runs"]["using"], "composite")
        self.assertEqual(set(data["inputs"]),
                         {"repo", "ref", "path", "format", "fail-on", "upload-sarif", "github-token"})
        self.assertEqual(data["inputs"]["repo"]["default"], "${{ github.repository }}")
        self.assertEqual(data["inputs"]["format"]["default"], "markdown")
        self.assertEqual(data["inputs"]["github-token"]["default"], "${{ github.token }}")
        for name in ("verdict", "complete", "json-file", "report-file", "sarif-file", "exit-code"):
            self.assertIn(name, data["outputs"])
        for step in data["runs"]["steps"]:
            if "run" in step:
                self.assertIn("shell", step, step.get("name"))
        ids = {step.get("id") for step in data["runs"]["steps"]}
        self.assertIn("check", ids)

    def test_sarif_upload_is_optional(self):
        data = yaml.safe_load(ACTION.read_text(encoding="utf-8"))
        upload = [s for s in data["runs"]["steps"] if str(s.get("uses", "")).startswith("github/codeql-action/upload-sarif@")]
        self.assertEqual(len(upload), 1)
        self.assertIn("inputs.upload-sarif == 'true'", upload[0]["if"])
        self.assertEqual(data["inputs"]["upload-sarif"]["default"], "false")

    def test_workflow_jobs_have_timeouts_and_ci_matrix(self):
        for path in _workflow_files():
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            for name, job in data["jobs"].items():
                self.assertIn("timeout-minutes", job, f"{path.name}:{name}")
        ci = yaml.safe_load((WORKFLOWS / "ci.yml").read_text(encoding="utf-8"))
        matrix = ci["jobs"]["test"]["strategy"]["matrix"]
        self.assertEqual(matrix["python-version"], ["3.10", "3.11", "3.12", "3.13"])
        self.assertEqual(len(matrix["os"]), 3)


def _check_script():
    """The Python program of the `Read the AI policy` step (the heredoc body), dedented."""
    text = ACTION.read_text(encoding="utf-8")
    match = re.search(r"<<'PY'\n(.*?)\n\s+PY\n", text, re.S)
    assert match, "heredoc not found in action.yml"
    return textwrap.dedent(match.group(1)) + "\n"


def _report(verdict="banned", complete=True, evidence=(), unclassified=()):
    from oss_ai_policy_check.contract import Snapshot, build_report
    snapshot = Snapshot(repo="o/r", ref="main", commit_sha="a" * 40, complete=complete,
                        files_checked=[{"path": "AI_POLICY.md", "status": "read"}])
    return build_report(snapshot, {"verdict": verdict, "evidence": list(evidence),
                                   "unclassified_ai_lines": list(unclassified)})


def _run_step(report, **inputs):
    """Run the step's program in-process with `analyze` replaced; returns (stdout, outputs)."""
    script = _check_script()
    with tempfile.TemporaryDirectory() as tmp:
        output = Path(tmp, "github_output")
        env = {"IN_REPO": "o/r", "IN_REF": "", "IN_PATH": "", "IN_FORMAT": "markdown", "IN_FAIL_ON": "",
               "RUNNER_TEMP": tmp, "GITHUB_OUTPUT": str(output), "GITHUB_STEP_SUMMARY": str(Path(tmp, "summary.md"))}
        env.update(inputs)
        stdout = io.StringIO()
        with mock.patch.dict(os.environ, env), \
                mock.patch("oss_ai_policy_check.cli.analyze", return_value=report), \
                contextlib.redirect_stdout(stdout):
            try:
                exec(compile(script, "action.yml", "exec"), {"__name__": "__main__"})
            except SystemExit:
                pass
        outputs = dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines())
    return stdout.getvalue(), outputs


@unittest.skipUnless(ACTION.is_file(), "action.yml is not part of this tree")
class ActionStepBehaviourTest(unittest.TestCase):
    def test_incomplete_read_fails_even_without_fail_on(self):
        """Exit 3 means nothing could be decided; a green check would read as 'no AI policy'."""
        for verdict in ("no-policy-found", "permitted"):
            for fail_on in ("", "banned", "needs-review"):
                _, out = _run_step(_report(verdict, complete=False), IN_FAIL_ON=fail_on)
                self.assertEqual((out["exit-code"], out["enforce-code"]), ("3", "3"), (verdict, fail_on))
                self.assertEqual(out["complete"], "false")

    def test_report_only_still_passes_for_decided_verdicts(self):
        for verdict, code in (("banned", "20"), ("restricted", "10"), ("disclosure-required", "0"),
                              ("needs-review", "0"), ("permitted", "0"), ("no-policy-found", "0")):
            _, out = _run_step(_report(verdict, complete=True))
            self.assertEqual(out["enforce-code"], "0", verdict)
            self.assertEqual(out["exit-code"], code, verdict)
        # a ban stands even when some other file could not be read: not an exit 3
        _, out = _run_step(_report("banned", complete=False))
        self.assertEqual((out["exit-code"], out["enforce-code"]), ("20", "0"))

    def test_fail_on_still_enforces_its_threshold(self):
        _, out = _run_step(_report("banned"), IN_FAIL_ON="restricted")
        self.assertEqual(out["enforce-code"], "20")
        _, out = _run_step(_report("disclosure-required"), IN_FAIL_ON="restricted")
        self.assertEqual(out["enforce-code"], "0")

    def test_report_is_wrapped_in_stop_commands_with_a_random_token(self):
        hostile = "::error title=x::ai-policy.md"
        report = _report(evidence=[{"file": hostile, "line": 1, "signal": "banned", "confidence": "high",
                                    "quote": "::warning::AI is banned"}],
                         unclassified=[{"file": hostile, "line": 2, "quote": "::set-output name=a::b"}])
        stdout, _ = _run_step(report)
        lines = stdout.splitlines()
        match = re.fullmatch(r"::stop-commands::([0-9a-f]{32})", lines[0])
        self.assertIsNotNone(match, lines[0])
        self.assertEqual(lines[-1], f"::{match.group(1)}::")
        for line in lines[1:-1]:
            self.assertFalse(line.lstrip().startswith("::"), line)
        self.assertEqual([line for line in lines if line.startswith("::")], [lines[0], lines[-1]])
        again, _ = _run_step(report)
        self.assertNotEqual(again.splitlines()[0], lines[0])  # a fresh token per run

    def test_error_messages_are_single_line(self):
        stdout, out = _run_step(_report(), IN_FAIL_ON="nonsense")
        self.assertEqual(out["exit-code"], "2")
        self.assertEqual(len(stdout.strip().splitlines()), 1)
        self.assertTrue(stdout.startswith("::error::fail-on must be one of"))

    @unittest.skipIf(sys.version_info < (3, 11), "python -P needs Python 3.11 or newer")
    def test_workspace_package_is_not_imported(self):
        """A pull request can add `oss_ai_policy_check/__init__.py` to the workspace; `-P` keeps it off sys.path."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp, "workspace")
            (workspace / "oss_ai_policy_check").mkdir(parents=True)
            marker = Path(tmp, "pwned")
            (workspace / "oss_ai_policy_check" / "__init__.py").write_text(
                f"open({str(marker)!r}, 'w').write('x')\n", encoding="utf-8")
            (workspace / "AI_POLICY.md").write_text("AI contributions are not allowed.\n", encoding="utf-8")
            output = Path(tmp, "github_output")
            env = {key: value for key, value in os.environ.items()
                   if key in ("SYSTEMROOT", "PATH", "TEMP", "TMP", "HOME", "LOCALAPPDATA", "USERPROFILE")}
            env.update({"PYTHONPATH": str(ROOT / "src"), "IN_REPO": "o/r", "IN_REF": "", "IN_PATH": str(workspace),
                        "IN_FORMAT": "text", "IN_FAIL_ON": "", "RUNNER_TEMP": tmp, "GITHUB_OUTPUT": str(output)})
            proc = subprocess.run([sys.executable, "-P", "-X", "utf8", "-B", "-"], input=_check_script(),
                                  text=True, encoding="utf-8", cwd=workspace, env=env, capture_output=True,
                                  timeout=120)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertFalse(marker.exists(), "the workspace package was imported")
            self.assertIn("verdict=banned", output.read_text(encoding="utf-8"))
            # control: without -P the same workspace package would win over PYTHONPATH
            control = subprocess.run([sys.executable, "-X", "utf8", "-B", "-"], input=_check_script(), text=True,
                                     encoding="utf-8", cwd=workspace, env=env, capture_output=True, timeout=120)
            self.assertTrue(marker.exists(), "control run did not import the workspace package; test is blind")
            self.assertNotEqual(control.returncode, 0)


if __name__ == "__main__":
    unittest.main()
