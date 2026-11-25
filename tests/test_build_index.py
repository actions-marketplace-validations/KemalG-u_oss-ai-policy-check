"""Offline tests of scripts/build_index.py: selection, decision summary, week-over-week comparison, partial runs."""
import importlib.util
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

import _support
from _fakes import SHA, FakeGithub
from oss_ai_policy_check import cli
from oss_ai_policy_check.classify import classify_documents
from oss_ai_policy_check.fetch import FetchError, RateLimitedError

_SPEC = importlib.util.spec_from_file_location("build_index", _support.ROOT / "scripts" / "build_index.py")
build_index = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(build_index)

TODAY = date(2026, 11, 25)
BASE = {"pushed_within_days": 30, "min_stars": 1000}


def item(name, stars, language="Python", **extra):
    return {"full_name": name, "stargazers_count": stars, "language": language, "default_branch": "main", **extra}


def entry(repo, category="no-policy-found", **extra):
    base = {"repo": repo, "stars": 10, "language": "Python", "status": "ok", "category": category, "verdict": category,
            "confidence": "high", "review_reason": None, "complete": True, "commit_sha": SHA, "source_file": None,
            "evidence_line": None, "evidence_quote": None, "evidence_url": None, "evidence_count": 0}
    base.update(extra)
    return base


def document(entries, day="2026-11-25", stopped=None, requested=200):
    config = {"selection": {"pushed_within_days": 30, "min_stars": 1000}}
    facts = {"query": "q", "matching_repositories": 5000, "requested": requested, "max_per_language": 30, "excluded_languages": ["Markdown"], "languages": {}}
    return build_index.build_document(day, config, facts, entries, stopped, (12, 30))


class SelectionTests(unittest.TestCase):
    def test_query_has_window_stars_and_exclusions(self):
        q = build_index.search_query({"pushed_within_days": 30, "min_stars": 1000}, TODAY)
        self.assertEqual(q, "pushed:>=2026-10-26 stars:>=1000 archived:false fork:false")

    def test_skips_archived_forks_duplicates_and_excluded(self):
        pages = {1: {"total_count": 9, "items": [
            item("a/one", 90), item("a/old", 80, archived=True), item("a/fork", 70, fork=True),
            item("A/One", 60), item("a/skip", 50), item("a/two", 40)]}}
        chosen, facts = build_index.select_repos(lambda q, p: pages[p], dict(BASE, count=10, exclude=["a/skip"]), TODAY)
        self.assertEqual([c["repo"] for c in chosen], ["a/one", "a/two"])
        self.assertEqual(facts["matching_repositories"], 9)
        self.assertEqual(chosen[0]["default_branch"], "main")

    def test_language_cap_and_count(self):
        items = [item(f"a/py{i}", 100 - i, "Python") for i in range(5)] + [item("a/go", 1, "Go"), item("a/none", 1, None)]
        chosen, facts = build_index.select_repos(lambda q, p: {"items": items}, dict(BASE, count=4, max_per_language=2), TODAY)
        self.assertEqual([c["repo"] for c in chosen], ["a/py0", "a/py1", "a/go", "a/none"])
        self.assertEqual(facts["languages"], {"(none)": 1, "Go": 1, "Python": 2})

    def test_excluded_languages_are_left_out(self):
        items = [item("a/list", 9, "Markdown"), item("a/bare", 8, None), item("a/py", 7, "Python")]
        selection = dict(BASE, count=5, exclude_languages=["Markdown", "(none)"])
        chosen, facts = build_index.select_repos(lambda q, p: {"items": items}, selection, TODAY)
        self.assertEqual([c["repo"] for c in chosen], ["a/py"])
        self.assertEqual(facts["excluded_languages"], ["(none)", "Markdown"])

    def test_reads_next_page_only_while_pages_are_full_and_count_is_open(self):
        calls = []

        def search(query, page):
            calls.append(page)
            return {"items": [item(f"a/p{page}-{i}", 1000 - i) for i in range(100)]}
        chosen, _ = build_index.select_repos(search, dict(BASE, count=150), TODAY)
        self.assertEqual((len(chosen), calls), (150, [1, 2]))


class DecisionTests(unittest.TestCase):
    def test_entry_from_a_real_report_keeps_line_file_and_sha(self):
        report = cli.analyze("a/b", None, client=FakeGithub(), no_cache=True)
        e = build_index.entry_from_report({"repo": "a/b", "stars": 7, "language": "Go"}, report)
        self.assertEqual((e["category"], e["source_file"], e["commit_sha"]), ("disclosure-required", ".github/AI_POLICY.md", SHA))
        self.assertTrue(e["evidence_url"].startswith("https://github.com/a/b/blob/" + SHA))
        self.assertEqual(e["evidence_line"], 1)

    def test_unreadable_is_not_no_policy(self):
        report = cli.analyze("a/b", None, client=FakeGithub(fail=True), no_cache=True)
        e = build_index.entry_from_report({"repo": "a/b", "stars": 1, "language": "Go"}, report)
        self.assertEqual((report["verdict"], report["complete"], e["category"]), ("no-policy-found", False, "unreadable"))

    def test_best_evidence_prefers_own_signal_and_high_confidence(self):
        evidence = [{"signal": "disclosure-required", "confidence": "high", "line": 1},
                    {"signal": "restricted", "confidence": "low", "line": 2},
                    {"signal": "restricted", "confidence": "high", "line": 3}]
        self.assertEqual(build_index.best_evidence(evidence, "restricted")["line"], 3)
        self.assertIsNone(build_index.best_evidence([], "no-policy-found"))

    def test_summarize_counts_every_category(self):
        counts = build_index.summarize([entry("a/1", "banned"), entry("a/2"), entry("a/3"), entry("a/4", "unreadable")])
        self.assertEqual((counts["banned"], counts["no-policy-found"], counts["unreadable"], counts["permitted"]), (1, 2, 1, 0))


def report_for(repo_row):
    """A report shaped like cli.analyze's, built from a golden excerpt by the real classifier."""
    result = classify_documents(repo_row["documents"], owner=repo_row["repo"].split("/")[0])
    evidence = [{**e, "url": f"https://github.com/{repo_row['repo']}/blob/{repo_row['commit_sha']}/{e['file']}#L{e['line']}"}
                for e in result["evidence"]]
    return {"verdict": result["verdict"], "confidence": result["confidence"], "evidence": evidence,
            "review_reason": result["review_reason"], "complete": True, "commit_sha": repo_row["commit_sha"], "warnings": []}


GOLDEN = {row["id"]: row for row in json.loads((_support.ROOT / "tests" / "golden" / "agent_file_cases.json")
                                               .read_text(encoding="utf-8"))}


class PublicationRuleTests(unittest.TestCase):
    """A weak banned/restricted read is never shown under that name: it is `possible`, with its quote."""

    def test_headline_needs_high_confidence_and_a_policy_file(self):
        h = build_index.headline_category
        self.assertEqual(h("banned", "high", "CONTRIBUTING.md"), "banned")
        self.assertEqual(h("restricted", "high", "org:.github/AI_POLICY.md"), "restricted")
        self.assertEqual(h("restricted", "high", "AGENTS.md"), "restricted")
        self.assertEqual(h("banned", "high", ".github/PULL_REQUEST_TEMPLATE.md"), "banned")
        self.assertEqual(h("banned", "low", "CONTRIBUTING.md"), "possible")
        self.assertEqual(h("restricted", "low", "AGENTS.md"), "possible")
        self.assertEqual(h("restricted", "high", "README.md"), "possible")
        self.assertEqual(h("restricted", "high", "docs/providers/vercel-ai-gateway.md"), "possible")
        self.assertEqual(h("banned", "high", None), "possible")
        for other in ("disclosure-required", "needs-review", "permitted", "no-policy-found"):
            self.assertEqual(h(other, "low", "README.md"), other)

    def test_entry_keeps_the_rules_verdict_but_counts_a_weak_ban_as_possible(self):
        # a single, unambiguous ban sentence is one deciding sentence: confidence low
        row = GOLDEN["alacritty-ban"]
        report = report_for(row)
        self.assertEqual((report["verdict"], report["confidence"]), ("banned", "low"))
        e = build_index.entry_from_report({"repo": row["repo"], "stars": 1, "language": "Rust"}, report)
        self.assertEqual((e["category"], e["verdict"], e["confidence"]), ("possible", "banned", "low"))
        self.assertIn("large language models", e["evidence_quote"])

    def test_firm_ban_stays_banned(self):
        row = GOLDEN["yt-dlp-ban"]
        report = report_for(row)
        self.assertEqual((report["verdict"], report["confidence"]), ("banned", "high"))
        e = build_index.entry_from_report({"repo": row["repo"], "stars": 1, "language": "Python"}, report)
        self.assertEqual(e["category"], "banned")

    def test_real_bans_are_not_lost_and_wrong_ones_are_not_banned(self):
        # the rows of the 25 Nov 2026 index: the six real bans stay listed (banned or possible, never silent), the
        # wrong ones are neither banned nor possible-banned
        for cid in ("yt-dlp-ban", "free-for-dev-ban", "alacritty-ban", "meilisearch-ban", "typst-ban", "seclists-ban"):
            row = GOLDEN[cid]
            e = build_index.entry_from_report({"repo": row["repo"], "stars": 1, "language": "x"}, report_for(row))
            self.assertIn(e["category"], ("banned", "possible"), cid)
            self.assertEqual(e["verdict"], "banned", cid)
            self.assertTrue(e["evidence_quote"], cid)
        row = GOLDEN["diagram-design-version-bump"]  # no AI rule at all
        e = build_index.entry_from_report({"repo": row["repo"], "stars": 1, "language": "x"}, report_for(row))
        self.assertNotIn(e["category"], ("banned", "restricted", "possible"))
        row = GOLDEN["rxjava-fire-and-forget"]  # a disclosure duty, not a ban
        e = build_index.entry_from_report({"repo": row["repo"], "stars": 1, "language": "x"}, report_for(row))
        self.assertNotEqual((e["verdict"], e["category"]), ("banned", "banned"))
        self.assertNotEqual(e["verdict"], "banned")

    def test_evidence_line_prefers_a_line_that_names_ai(self):
        row = GOLDEN["microsoft-terminal-ai-policy"]
        e = build_index.entry_from_report({"repo": row["repo"], "stars": 1, "language": "C++"}, report_for(row))
        self.assertEqual(e["verdict"], "restricted")
        self.assertIn("AI", e["evidence_quote"])
        self.assertNotEqual(e["evidence_quote"], "Banned:")
        self.assertEqual(e["category"], "possible")

    def test_counts_and_readme_list_possible_separately_with_its_quote(self):
        doc = document([
            entry("a/firm", "banned", confidence="high", source_file="CONTRIBUTING.md", evidence_line=4,
                  evidence_quote="No AI for patches.", evidence_url="https://x/firm#L4"),
            entry("a/weak", "possible", verdict="banned", confidence="low", source_file="CONTRIBUTING.md", evidence_line=9,
                  evidence_quote="Contributions must not include LLM content.", evidence_url="https://x/weak#L9")])
        self.assertEqual((doc["counts"]["banned"], doc["counts"]["possible"]), (1, 1))
        text = build_index.render_readme(doc, None)
        self.assertIn("| `banned` | 1 |", text)
        self.assertIn("| `possible` | 1 |", text)
        banned_part, possible_part = text.split("### `possible` - needs review (1)")
        self.assertIn("a/firm", banned_part)
        self.assertNotIn("a/weak", banned_part)
        self.assertIn("a/weak", possible_part)
        self.assertIn("rules read: `banned`", possible_part)
        self.assertIn("must not include LLM content", possible_part)


class ScanTests(unittest.TestCase):
    repos = [{"repo": f"a/r{i}", "stars": 5, "language": "Go", "default_branch": "main"} for i in range(4)]

    def test_complete_run_with_an_unreadable_repository(self):
        def analyze(info):
            if info["repo"] == "a/r1":
                raise FetchError("HTTP 500")
            return cli.analyze("a/b", None, client=FakeGithub(), no_cache=True)
        entries, stopped = build_index.scan(self.repos, analyze, lambda: 900, 40)
        self.assertIsNone(stopped)
        self.assertEqual([e["status"] for e in entries], ["ok", "error", "ok", "ok"])
        self.assertEqual(entries[1]["category"], "unreadable")

    def test_stops_when_the_budget_is_nearly_used(self):
        left = iter([500, 300, 39, 39])
        entries, stopped = build_index.scan(self.repos, lambda i: cli.analyze("a/b", None, client=FakeGithub(), no_cache=True),
                                            lambda: next(left), 40)
        self.assertEqual(len(entries), 2)
        self.assertIn("rate limit: 39 requests left", stopped)

    def test_rate_limited_read_is_not_recorded_as_unreadable(self):
        def analyze(info):
            if info["repo"] == "a/r2":
                report = cli.analyze("a/b", None, client=FakeGithub(), no_cache=True)
                report["warnings"] = ["repository discovery failed (rate limited (resets in 5 min))"]
                return report
            return cli.analyze("a/b", None, client=FakeGithub(), no_cache=True)
        entries, stopped = build_index.scan(self.repos, analyze, lambda: None, 40)
        self.assertEqual(len(entries), 2)
        self.assertIn("rate limit at a/r2", stopped)

    def test_refused_read_is_retried_after_the_advertised_wait(self):
        waits, tries = [], []

        def analyze(info):
            tries.append(info["repo"])
            report = cli.analyze("a/b", None, client=FakeGithub(), no_cache=True)
            if tries.count("a/r0") == 1:
                report["warnings"] = ["read failed: x (rate limited (resets in 2 min, at 04:07 UTC))"]
            return report
        entries, stopped = build_index.scan(self.repos[:2], analyze, lambda: None, 40, retries=2, sleep=waits.append)
        self.assertEqual((len(entries), stopped, waits), (2, None, [125.0]))
        self.assertEqual(tries, ["a/r0", "a/r0", "a/r1"])

    def test_wait_defaults_and_caps(self):
        self.assertEqual(build_index.refusal_wait("rate limited"), 60.0)
        self.assertEqual(build_index.refusal_wait("rate limited (resets in 59 min, at 05:00 UTC)"), 600.0)

    def test_raised_rate_limit_stops(self):
        def analyze(info):
            raise RateLimitedError("rate limited")
        entries, stopped = build_index.scan(self.repos, analyze, lambda: None, 40)
        self.assertEqual((entries, stopped), ([], "rate limit at a/r0: rate limited"))


class CompareTests(unittest.TestCase):
    def test_first_run_has_no_comparison(self):
        self.assertIsNone(build_index.compare(None, document([entry("a/1")])))

    def test_changed_entered_left(self):
        previous = build_index.compact(document([entry("a/1", "banned"), entry("a/2"), entry("a/3"), entry("a/4")], day="2026-11-18"))
        current = document([entry("a/1", "restricted"), entry("a/2"), entry("a/3", "unreadable"), entry("a/5")])
        diff = build_index.compare(previous, current)
        self.assertEqual(diff["changed"], [{"repo": "a/1", "from": "banned", "to": "restricted"}])
        self.assertEqual((diff["entered"], diff["left"], diff["since"]), (["a/5"], ["a/4"], "2026-11-18"))
        self.assertEqual(diff["compared_repositories"], 3)

    def test_partial_run_does_not_claim_departures(self):
        previous = build_index.compact(document([entry("a/1"), entry("a/2")], day="2026-11-18"))
        current = document([entry("a/1")], stopped="rate limit: 3 requests left")
        diff = build_index.compare(previous, current)
        self.assertEqual((diff["left"], diff["left_unknown"]), ([], True))


class OutputTests(unittest.TestCase):
    def test_two_runs_write_files_and_the_second_reports_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "index"
            first = document([entry("a/1", "banned", source_file="AI.md", evidence_line=3, evidence_quote="no AI", evidence_url="https://x/y#L3"),
                              entry("a/2")], day="2026-11-18")
            self.assertIsNone(build_index.write_outputs(out, first))
            self.assertIn("first run", (out / "README.md").read_text(encoding="utf-8"))
            second = document([entry("a/1", "restricted"), entry("a/2")], day="2026-11-25")
            diff = build_index.write_outputs(out, second)
            self.assertEqual(diff["changed"][0]["to"], "restricted")
            self.assertEqual(sorted(p.name for p in (out / "history").iterdir()), ["2026-11-18.json", "2026-11-25.json"])
            latest = json.loads((out / "latest.json").read_text(encoding="utf-8"))
            self.assertEqual((latest["date"], latest["tool"]["classifier"], latest["partial"]), ("2026-11-25", "rules", False))
            readme = (out / "README.md").read_text(encoding="utf-8")
            self.assertIn("Measured accuracy is 70%", readme)
            self.assertIn("1 changed class", readme)
            history = json.loads((out / "history" / "2026-11-25.json").read_text(encoding="utf-8"))
            self.assertNotIn("evidence_quote", history["repos"][0])

    def test_same_day_rerun_compares_with_the_older_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            build_index.write_outputs(out, document([entry("a/1")], day="2026-11-18"))
            build_index.write_outputs(out, document([entry("a/1", "banned")], day="2026-11-25"))
            diff = build_index.write_outputs(out, document([entry("a/1", "banned")], day="2026-11-25"))
            self.assertEqual((diff["since"], len(diff["changed"])), ("2026-11-18", 1))

    def test_partial_run_is_labelled_and_hostile_quotes_are_neutralised(self):
        bad = entry("a/1", "banned", source_file="AI|X.md", evidence_line=1, evidence_url="https://x/y#L1",
                    evidence_quote="ping @someone | <b>bold</b>")
        text = build_index.render_readme(document([bad], stopped="rate limit: 3 requests left, reserve 40"), None)
        self.assertIn("**partial**", text)
        self.assertNotIn("@someone", text)
        self.assertNotIn("<b>", text)


if __name__ == "__main__":
    unittest.main()
