import json
import re
import unittest

import _support
from oss_ai_policy_check.classify import classify_documents
from oss_ai_policy_check.contract import FORMATS, Snapshot, build_report
from oss_ai_policy_check.report import (render, render_json, render_markdown, render_sarif,
                                        render_text)

SHA = "b" * 40


def make_report(**kw):
    docs = {"AI_POLICY.md": "AI contributions are not allowed.\n\nAI is mentioned here in passing."}
    s = Snapshot(repo="o/r", ref="main", commit_sha=SHA, documents=docs,
                 files_checked=[{"path": "AI_POLICY.md", "status": "read"}], **kw)
    return build_report(s, classify_documents(docs))


def doc_with(evidence=(), unclassified=(), **kw):
    """A report with hand-made evidence (the classifier is not under test here)."""
    s = Snapshot(repo="o/r", ref="main", commit_sha=SHA,
                 files_checked=[{"path": "AI_POLICY.md", "status": "read"}], **kw)
    verdict = evidence[0]["signal"] if evidence else "no-policy-found"
    return build_report(s, {"verdict": verdict, "evidence": list(evidence),
                            "unclassified_ai_lines": list(unclassified)})


def ev(file="AI_POLICY.md", line=1, signal="banned", quote="AI is banned", confidence="high"):
    return {"file": file, "line": line, "signal": signal, "quote": quote, "confidence": confidence}


def review_report(paragraphs=(), evidence=(), reason="no-rule-decision", **kw):
    """A needs-review report: AI-policy paragraphs no rule decided on, or conflicting classified lines."""
    s = Snapshot(repo="o/r", ref="main", commit_sha=SHA,
                 files_checked=[{"path": "CONTRIBUTING.md", "status": "read"}], **kw)
    return build_report(s, {"verdict": "needs-review", "confidence": "low", "review_reason": reason,
                            "evidence": list(evidence), "review": list(paragraphs),
                            "unclassified_ai_lines": []})


def sarif_errors(doc):
    """Structural SARIF 2.1.0 check. The OASIS schema file is not vendored (its repository carries
    no plain permissive licence), so this covers every property the renderer emits plus what
    GitHub code scanning needs."""
    errors = []

    def need(ok, msg):
        if not ok:
            errors.append(msg)

    need(doc.get("version") == "2.1.0", "version")
    need(str(doc.get("$schema", "")).endswith("sarif-schema-2.1.0.json"), "$schema")
    need(isinstance(doc.get("runs"), list) and len(doc["runs"]) == 1, "runs")
    for run in doc.get("runs", []):
        driver = run.get("tool", {}).get("driver", {})
        need(all(isinstance(driver.get(k), str) and driver[k] for k in ("name", "version", "informationUri")), "driver")
        need(driver.get("informationUri", "").startswith("https://"), "informationUri")
        rules = driver.get("rules", [])
        ids = [r.get("id") for r in rules]
        need(len(ids) == len(set(ids)), "duplicate rule ids")
        need(all(r["defaultConfiguration"]["level"] in ("none", "note", "warning", "error") for r in rules), "rule level")
        need(isinstance(run.get("results"), list), "results")
        for res in run.get("results", []):
            need(res.get("level") in ("none", "note", "warning", "error"), "level")
            need(isinstance(res.get("message", {}).get("text"), str), "message.text")
            index = res.get("ruleIndex", -1)
            need(0 <= index < len(rules) and rules[index]["id"] == res.get("ruleId"), "ruleIndex")
            locations = res.get("locations", [])
            need(len(locations) == 1, "locations")
            physical = locations[0]["physicalLocation"]
            uri = physical["artifactLocation"]["uri"]
            need(not re.match(r"^([A-Za-z][A-Za-z0-9+.-]*:|/)", uri) and ".." not in uri.split("/")
                 and "\\" not in uri, "uri must be relative: " + uri)
            need(isinstance(physical["region"]["startLine"], int) and physical["region"]["startLine"] >= 1, "startLine")
        for invocation in run.get("invocations", []):
            need(isinstance(invocation.get("executionSuccessful"), bool), "executionSuccessful")
    return errors


class RenderTests(unittest.TestCase):
    def test_json_round_trips_and_conforms(self):
        report = make_report()
        data = json.loads(render_json(report))
        self.assertEqual(data, report)
        self.assertEqual(_support.schema_errors(data), [])

    def test_text_contains_verdict_evidence_url_and_disclaimer(self):
        text = render_text(make_report())
        self.assertEqual(text.splitlines()[0], "VERDICT banned - o/r @ main (bbbbbbbbbbbb)")
        self.assertIn("banned (high): AI_POLICY.md:1", text)
        self.assertIn("/blob/" + SHA + "/AI_POLICY.md#L1", text)
        self.assertIn("never permission", text)
        self.assertIn("1 read, 0 missing, 0 error", text)

    def test_text_is_ascii_safe_labels_and_marks_incomplete(self):
        text = render_text(make_report(complete=False, warnings=["read failed: x (HTTP 500)"]))
        self.assertIn("INCOMPLETE", text)
        self.assertIn("WARNING: read failed: x (HTTP 500)", text)

    def test_no_policy_incomplete_says_no_decision(self):
        s = Snapshot(repo="o/r", ref=None, commit_sha=None, complete=False, warnings=["repository discovery failed (HTTP 404)"])
        text = render_text(build_report(s, classify_documents({})))
        self.assertIn("no decision", text)
        self.assertEqual(text.splitlines()[0], "VERDICT no-policy-found - o/r")

    def test_text_strips_terminal_escapes_from_quotes(self):
        report = make_report()
        report["evidence"][0]["quote"] = "ok \x1b[31mred\x07 text"
        text = render_text(report)
        self.assertNotIn("\x1b", text)
        self.assertNotIn("\x07", text)
        self.assertIn("ok [31mred text", text)

    def test_text_never_starts_a_line_with_untrusted_text(self):
        # a policy file named like a workflow command must not put `::...` at the start of a line
        hostile = "::error title=x::ai-policy.md"
        report = doc_with(
            evidence=[ev(file=hostile, quote="::warning::AI is banned")],
            unclassified=[{"file": hostile, "line": 3, "quote": "::set-output name=a::b"}])
        report["warnings"] = ["::error::boom"]
        report["ref"] = "::stop-commands::x"
        text = render_text(report)
        for line in text.splitlines():
            self.assertFalse(line.lstrip().startswith("::"), line)
        self.assertIn("  - banned (high): : :error title=x::ai-policy.md:1", text)
        self.assertIn("  - : :error title=x::ai-policy.md:3 - : :set-output name=a::b", text)
        self.assertIn("WARNING: : :error::boom", text)
        plain = make_report()
        self.assertEqual(render_text(plain).count("\n  - "),
                         len(plain["evidence"]) + len(plain["unclassified_ai_lines"]))

    def test_bidi_and_c1_controls_are_removed_from_every_format(self):
        hostile = ("\u202a", "\u202b", "\u202c", "\u202d", "\u202e", "\u2066", "\u2067", "\u2068", "\u2069",
                   "\u200e", "\u200f", "\u061c", "\u0080", "\u0085", "\u009b", "\u009f")
        bad = "ab" + "".join(c + "x" for c in hostile) + "cd"
        report = doc_with(evidence=[ev(quote=bad)], unclassified=[{"file": "A.md", "line": 2, "quote": bad}])
        for name, rendered in (("text", render_text(report)), ("markdown", render_markdown(report)),
                               ("sarif", render_sarif(report)), ("json", render_json(report))):
            for ch in hostile:
                self.assertNotIn(ch, rendered, (name, hex(ord(ch))))
            self.assertIn("x x x", rendered, name)

    def test_json_quotes_are_cleaned_and_json_stays_valid(self):
        report = doc_with(evidence=[ev(quote='say "no" \x1b[31m\u202e\\ path')],
                          unclassified=[{"file": "A.md", "line": 2, "quote": "tab\there x"}])
        data = json.loads(render_json(report))
        self.assertEqual(data["evidence"][0]["quote"], 'say "no" [31m \\ path')
        self.assertEqual(data["unclassified_ai_lines"][0]["quote"], "tab here x")
        self.assertEqual(report["evidence"][0]["quote"], 'say "no" \x1b[31m\u202e\\ path')  # input untouched
        self.assertEqual(_support.schema_errors(data), [])

    def test_unknown_format(self):
        with self.assertRaises(ValueError):
            render(make_report(), "xml")
        self.assertEqual(FORMATS, ("text", "json", "markdown", "sarif"))

    def test_dispatch(self):
        self.assertEqual(render(make_report(), "sarif"), render_sarif(make_report()))
        self.assertEqual(render(make_report(), "markdown"), render_markdown(make_report()))


class MarkdownTests(unittest.TestCase):
    def test_header_badge_table_and_link(self):
        md = render_markdown(make_report())
        self.assertIn("<!-- oss-ai-policy-check -->", md)
        self.assertIn("## AI policy check: BANNED", md)
        self.assertIn("> [!CAUTION]", md)
        self.assertIn("| Location | Signal | Confidence | Quote |", md)
        self.assertIn("[AI\\_POLICY.md:1](https://github.com/o/r/blob/" + SHA + "/AI_POLICY.md#L1) | `banned` | ", md)
        self.assertIn("**Confidence:**", md)
        self.assertIn("never permission", md)

    def test_unclassified_lines_are_collapsed(self):
        md = render_markdown(make_report())
        start = md.index("<details>")
        self.assertIn("AI mentions without a classified rule (1)", md[start:start + 120])
        self.assertEqual(md.count("<details>"), md.count("</details>"))

    def test_incomplete_warning_and_no_decision_badge(self):
        s = Snapshot(repo="o/r", ref=None, commit_sha=None, complete=False,
                     warnings=["read failed: a.md (HTTP 500)"])
        md = render_markdown(build_report(s, classify_documents({})))
        self.assertIn("NO-POLICY-FOUND (NO DECISION: INCOMPLETE READ)", md)
        self.assertIn("Incomplete read", md)
        self.assertIn("read failed: a.md (HTTP 500)", md)
        self.assertIn("No quoted evidence line was found.", md)

    def test_complete_report_has_no_incomplete_warning(self):
        self.assertNotIn("Incomplete read", render_markdown(make_report()))

    def test_escapes_pipes_markup_html_and_mentions(self):
        quote = "a | b *bold* _it_ `c` <script>x</script> [l](u) @octocat | # & $m$"
        md = render_markdown(doc_with([ev(quote=quote)]))
        row = next(line for line in md.splitlines() if line.startswith("| [AI"))
        self.assertEqual(len(re.findall(r"(?<!\\)\|", row)), 5)  # leading bar + 4 separators only
        for raw in ("<script>", "*bold*", "[l](u)", "`c`", "@octocat"):
            self.assertNotIn(raw, md)
        self.assertIn("\\<script\\>", md)
        self.assertIn("a \\| b", md)
        self.assertIn("@\u200boctocat", md)

    def test_hostile_path_cannot_break_the_table(self):
        md = render_markdown(doc_with([ev(file="a|b](x)<i>.md")]))
        self.assertNotIn("<i>", md)
        self.assertIn("a\\|b\\](x)\\<i\\>.md:1", md)

    def test_without_sha_there_are_no_links(self):
        s = Snapshot(repo="o/r", ref=None, commit_sha=None)
        report = build_report(s, {"verdict": "banned", "evidence": [ev()], "unclassified_ai_lines": []})
        row = next(line for line in render_markdown(report).splitlines() if "`banned`" in line)
        self.assertNotIn("](", row)

    def test_no_emoji_only_the_mention_guard_is_non_ascii(self):
        self.assertTrue(render_markdown(make_report()).isascii())


class NeedsReviewTests(unittest.TestCase):
    PARAGRAPHS = [{"file": "CONTRIBUTING.md", "line": 12, "quote": "AI-assisted code must pass the same review bar"},
                  {"file": "docs/AI.md", "line": 3, "quote": "We do not require disclosure of AI usage <b>"}]

    def test_text_names_the_reason_and_lists_the_paragraphs(self):
        text = render_text(review_report(self.PARAGRAPHS))
        self.assertTrue(text.startswith("VERDICT needs-review - o/r"))
        self.assertIn("confidence: low", text)
        self.assertIn("NEEDS HUMAN REVIEW (no-rule-decision)", text)
        self.assertIn("needs-review (low): CONTRIBUTING.md:12 - AI-assisted code must pass the same review bar", text)
        self.assertIn("https://github.com/o/r/blob/" + SHA + "/docs/AI.md#L3", text)

    def test_markdown_has_a_needs_human_review_heading_listing_paragraphs(self):
        md = render_markdown(review_report(self.PARAGRAPHS))
        self.assertIn("## AI policy check: NEEDS-REVIEW", md)
        self.assertIn("> [!WARNING]", md)
        self.assertIn("### Needs human review (2)", md)
        self.assertIn("[CONTRIBUTING.md:12](https://github.com/o/r/blob/" + SHA + "/CONTRIBUTING.md#L12) - AI-assisted", md)
        self.assertNotIn("### Evidence", md)  # the paragraphs are not shown twice
        self.assertNotIn("<b>", md)  # untrusted quote text is escaped
        self.assertTrue(md.isascii())
        self.assertEqual(md.count("<details>"), md.count("</details>"))

    def test_markdown_conflict_lists_each_signal(self):
        md = render_markdown(review_report(evidence=[ev(signal="permitted", line=1, quote="AI is welcome"),
                                                      ev(signal="banned", line=3, quote="AI is not allowed")],
                                           reason="conflicting-signals"))
        self.assertIn("### Needs human review (2)", md)
        self.assertIn("(`permitted`, high confidence) - AI is welcome", md)
        self.assertIn("(`banned`, high confidence) - AI is not allowed", md)
        self.assertIn("different directions", md)

    def test_sarif_marks_every_paragraph_as_a_warning_with_confidence(self):
        doc = json.loads(render_sarif(review_report(self.PARAGRAPHS)))
        self.assertEqual(sarif_errors(doc), [])
        results = doc["runs"][0]["results"]
        self.assertEqual([r["ruleId"] for r in results], ["needs-review", "needs-review"])
        self.assertEqual([r["level"] for r in results], ["warning", "warning"])
        self.assertEqual(results[0]["properties"]["confidence"], "low")
        props = doc["runs"][0]["properties"]
        self.assertEqual((props["verdict"], props["confidence"], props["reviewReason"]),
                         ("needs-review", "low", "no-rule-decision"))

    def test_json_conforms_to_the_schema(self):
        data = json.loads(render_json(review_report(self.PARAGRAPHS)))
        self.assertEqual(_support.schema_errors(data), [])
        self.assertEqual((data["verdict"], data["review_reason"]), ("needs-review", "no-rule-decision"))

    def test_decided_reports_carry_confidence(self):
        self.assertIn("confidence: low", render_text(make_report()))
        data = json.loads(render_json(make_report()))
        self.assertEqual((data["confidence"], data["review_reason"], data["evidence"][0]["confidence"]),
                         ("low", None, "high"))


class SarifTests(unittest.TestCase):
    def test_structure_and_level_mapping(self):
        evidence = [ev(signal="banned"), ev(line=2, signal="restricted", quote="r"),
                    ev(line=3, signal="disclosure-required", quote="d"), ev(line=4, signal="permitted", quote="p")]
        doc = json.loads(render_sarif(doc_with(evidence)))
        self.assertEqual(sarif_errors(doc), [])
        results = doc["runs"][0]["results"]
        self.assertEqual([r["level"] for r in results], ["error", "warning", "note", "none"])
        self.assertEqual([r["ruleId"] for r in results], ["banned", "restricted", "disclosure-required", "permitted"])
        self.assertEqual([r["locations"][0]["physicalLocation"]["region"]["startLine"] for r in results], [1, 2, 3, 4])

    def test_tool_driver_and_provenance(self):
        run = json.loads(render_sarif(make_report()))["runs"][0]
        driver = run["tool"]["driver"]
        self.assertEqual((driver["name"], driver["version"]), ("oss-ai-policy-check", "0.3.1"))
        self.assertTrue(driver["informationUri"].startswith("https://github.com/"))
        self.assertEqual(run["versionControlProvenance"][0]["revisionId"], SHA)
        self.assertEqual(run["results"][0]["properties"]["permalink"],
                         "https://github.com/o/r/blob/" + SHA + "/AI_POLICY.md#L1")

    def test_one_result_per_evidence_and_empty_report_is_valid(self):
        report = doc_with([ev(), ev(file="docs/AI.md", line=7)])
        self.assertEqual(len(json.loads(render_sarif(report))["runs"][0]["results"]), 2)
        empty = json.loads(render_sarif(doc_with()))
        self.assertEqual(empty["runs"][0]["results"], [])
        self.assertEqual(sarif_errors(empty), [])
        self.assertEqual(empty["runs"][0]["properties"]["verdict"], "no-policy-found")

    def test_uris_are_relative_and_escaped(self):
        report = doc_with([ev(file="docs\\AI policy.md"), ev(file="./../etc/AI.md"), ev(file="/abs/AI.md")])
        doc = json.loads(render_sarif(report))
        uris = [r["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] for r in doc["runs"][0]["results"]]
        self.assertEqual(uris, ["docs/AI%20policy.md", "etc/AI.md", "abs/AI.md"])
        self.assertEqual(sarif_errors(doc), [])

    def test_warnings_become_notifications_and_fingerprint_survives_line_moves(self):
        a = json.loads(render_sarif(doc_with([ev(line=1)], warnings=["w1"], complete=False)))["runs"][0]
        b = json.loads(render_sarif(doc_with([ev(line=9)])))["runs"][0]
        self.assertEqual(a["invocations"][0]["toolExecutionNotifications"][0]["message"]["text"], "w1")
        self.assertFalse(a["properties"]["complete"])
        self.assertEqual(a["results"][0]["partialFingerprints"], b["results"][0]["partialFingerprints"])

    def test_checker_rejects_a_broken_document(self):
        doc = json.loads(render_sarif(make_report()))
        doc["runs"][0]["results"][0]["level"] = "fatal"
        doc["runs"][0]["results"][0]["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] = "/abs/x.md"
        self.assertGreaterEqual(len(sarif_errors(doc)), 2)


if __name__ == "__main__":
    unittest.main()
