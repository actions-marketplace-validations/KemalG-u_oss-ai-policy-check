import unittest

import _support  # noqa: F401  (puts src/ on sys.path)
from oss_ai_policy_check.classify import (_boundaries, _Rules, body_flags, classify_documents, load_patterns,
                                          strip_code)


class ClassificationTests(unittest.TestCase):
    def result(self, text):
        return classify_documents({"AI_POLICY.md": text})

    def test_ban(self):
        self.assertEqual(self.result("We do not accept AI-generated contributions.")["verdict"], "banned")

    def test_qemu_wrapped(self):
        r = self.result("**Current QEMU project policy is to DECLINE any contributions which are\nbelieved to include or derive from AI generated content. This includes\nChatGPT, Claude, Copilot, Llama and similar tools.**")
        self.assertEqual(r["verdict"], "banned")
        self.assertEqual(r["evidence"][0]["line"], 1)

    def test_disclosure(self):
        self.assertEqual(self.result("All AI usage in any form must be disclosed.")["verdict"], "disclosure-required")

    def test_empty(self):
        self.assertEqual(self.result("")["verdict"], "no-policy-found")

    def test_no_policy_not_ban(self):
        # an explicit statement of absence is neither a ban nor policy text to review
        self.assertEqual(self.result("We do not have an AI policy.")["verdict"], "no-policy-found")
        self.assertEqual(self.result("There is no AI policy yet.")["verdict"], "no-policy-found")

    def test_heading_without_blank_line_does_not_hide_ban(self):
        self.assertEqual(self.result("# Policy\nAI contributions are not allowed.")["verdict"], "banned")

    def test_disclosure_not_required(self):
        # No rule decides, but the sentence is AI-policy text: the tool abstains instead of staying silent.
        r = self.result("We do not require disclosure of AI usage.")
        self.assertEqual(r["verdict"], "needs-review")
        self.assertNotIn("disclosure-required", r["signals"])

    def test_disclosure_prohibited(self):
        self.assertEqual(self.result("AI use must not be disclosed.")["verdict"], "restricted")

    def test_specific_tool_sentence_does_not_hide_general_ban(self):
        self.assertEqual(self.result("DeepSeek contributions are forbidden. AI-generated contributions are forbidden.")["verdict"], "banned")

    def test_specific_tool_not_general_ban(self):
        self.assertEqual(self.result("AI contributions made using DeepSeek or Grammarly are not allowed.")["verdict"], "restricted")

    def test_llvm_good_first(self):
        self.assertEqual(self.result('AI tools must not be used to fix issues labelled "good first\nissue".')["verdict"], "restricted")

    def test_ghostty_vouch(self):
        self.assertEqual(self.result("If you aren't vouched, any pull requests you open will be\nautomatically closed. AI has made it too trivial.")["verdict"], "restricted")

    def test_termlens_attribution_ban_not_ai_ban(self):
        r = self.result('**AI attribution is not welcome.** No `Co-Authored-By` trailer naming an\nassistant, model or vendor; no "Generated with" footer.')
        self.assertEqual(r["verdict"], "restricted")
        self.assertNotIn("disclosure-required", r["signals"])

    def test_termlens_autonomous_account_restriction(self):
        r = self.result("AI assistance is welcome.\n\nContributions authored by an autonomous account are not accepted. Human-operated agents are fine.")
        self.assertEqual(r["verdict"], "restricted")

    def test_llvm_disclosure_wrapped(self):
        r = self.result("Contributors are expected to label contributions that\ncontain substantial amounts of tool-generated content. Use a trailer like Assisted-by: <name of code assistant>.")
        self.assertEqual(r["verdict"], "disclosure-required")

    def test_generic_credit_trailers_are_not_ai_disclosure(self):
        r = self.result("credit all helpers, finders, doers; try to use one of the\nfollowing keywords: Assisted-by:, Co-authored-by:, Reviewed-by:.")
        self.assertEqual(r["verdict"], "no-policy-found")

    def test_harper_description_restriction(self):
        self.assertEqual(self.result("We will no longer accept pull requests with descriptions written by an agent.")["verdict"], "restricted")

    def test_harper_disclaimer(self):
        self.assertEqual(self.result("If you are an agent or used an LLM to produce any part of a PR, please provide a disclaimer at the top.")["verdict"], "disclosure-required")

    def test_curl_human(self):
        self.assertEqual(self.result("We accept AI. Please explain your work in your own human voice.")["verdict"], "restricted")

    def test_pymeasure_unreviewed_is_not_ban(self):
        r = self.result("For AI-assisted contributions:\n\nPRs showing signs of unreviewed generation may be rejected.")
        self.assertNotEqual(r["verdict"], "banned")

    def test_conflict_strictest_all_signals(self):
        r = self.result("AI contributions must be disclosed.\n\nAI contributions are not allowed.")
        self.assertEqual(r["verdict"], "banned")
        self.assertIn("disclosure-required", r["signals"])

    def test_exact_line_and_quote_limit(self):
        r = self.result("\nAll AI usage in any form must be disclosed. " + "word " * 20)
        e = r["evidence"][0]
        self.assertEqual(e["line"], 2)
        self.assertLessEqual(len(e["quote"].split()), 15)

    def test_result_order_deterministic(self):
        a = {"b.md": "All AI usage must be disclosed.", "a.md": "AI contributions are not allowed."}
        self.assertEqual(classify_documents(a), classify_documents(dict(reversed(list(a.items())))))


class ContractShapeTests(unittest.TestCase):
    """New in the package: shape guarantees the other modules rely on."""

    def test_evidence_keys_and_signal_vocabulary(self):
        r = classify_documents({"a.md": "AI contributions are not allowed."})
        self.assertEqual(set(r), {"verdict", "confidence", "signals", "evidence", "unclassified_ai_lines", "review",
                                  "review_reason", "body_rules"})
        self.assertEqual(set(r["evidence"][0]), {"file", "line", "signal", "quote", "rule", "confidence", "sentence",
                                                 "demoted"})
        self.assertEqual(r["evidence"][0]["signal"], "banned")

    def test_permitted_needs_an_explicit_welcome_and_loses_to_any_restriction(self):
        # Silence is never permission; an explicit welcome is `permitted`; any restriction beats it.
        self.assertEqual(classify_documents({"a.md": "Our dashboard is open to everyone."})["verdict"], "no-policy-found")
        self.assertEqual(classify_documents({"a.md": "AI tools are welcome here."})["verdict"], "permitted")
        r = classify_documents({"a.md": "AI tools are welcome here.\n\nAI code is not allowed."})
        self.assertEqual(r["verdict"], "needs-review")  # a welcome next to a full ban is a conflict, not a winner
        r = classify_documents({"a.md": "AI tools are welcome here.\n\nDo not add a Co-Authored-By trailer naming an AI."})
        self.assertEqual(r["verdict"], "restricted")  # a firm restriction still beats a welcome
        custom = {"ai_term": r"\bAI\b", "rules": [{"id": "welcome", "class": "permitted", "regex": "AI .{0,20}welcome"}]}
        self.assertEqual(classify_documents({"a.md": "AI tools are welcome here."}, custom)["verdict"], "permitted")

    def test_body_flags_trailer_and_voice(self):
        r = classify_documents({"a.md": "AI use is fine.\n\nDo not add a Co-Authored-By trailer. Use your own human voice."})
        flags = body_flags(r)
        self.assertTrue(flags["ai_trailer_ban"])
        self.assertTrue(flags["human_voice"])


class RuleTests(unittest.TestCase):
    """One positive and one negative (or negated) case for every rule in rules/patterns.json.

    Texts are short invented sentences in the style of real policies; none is copied from a repository."""

    def verdict(self, text, path="AI_POLICY.md"):
        return classify_documents({path: text})["verdict"]

    def rules(self, text, path="AI_POLICY.md"):
        return {e["rule"] for e in classify_documents({path: text})["evidence"]}

    # --- general-ban, project-ban, no-ai
    def test_ban_will_not_be_accepted(self):
        self.assertEqual(self.verdict("Pull requests, bug reports, and other contributions made with LLM / generative AI\ntechnology will not be accepted."), "banned")

    def test_ban_closed_on_sight(self):
        self.assertEqual(self.verdict("AI-generated PRs and issues are closed on sight, without review or discussion."), "banned")

    def test_ban_expressly_forbidden(self):
        self.assertEqual(self.verdict("It is expressly forbidden to contribute material generated by AI, LLMs, and similar technologies."), "banned")

    def test_ban_should_not_be_used_in_any_way(self):
        self.assertEqual(self.verdict("Generative AI, including large language models (LLMs), should not be used in any way when contributing."), "banned")

    def test_ban_refrain_from_submitting(self):
        self.assertEqual(self.verdict("Please refrain from submitting issues or pull requests that have been generated by an LLM."), "banned")

    def test_project_ban_sentence(self):
        r = self.result_rules("The use of any and all AI tools for this project is prohibited.")
        self.assertIn("general-ban", r)
        self.assertEqual(self.verdict("The use of any and all AI tools for this project is prohibited."), "banned")
        self.assertEqual(self.verdict("AI-generated/assisted content is forbidden in this project's development."), "banned")

    def test_no_ai_no_llm_policy(self):
        self.assertEqual(self.verdict("- No AI / no LLMs for patches or pull requests."), "banned")

    def result_rules(self, text):
        return self.rules(text)

    def test_ban_must_not_include_survives_emphasis(self):
        self.assertEqual(self.verdict("Contributions **must not** include content generated by large language models."), "banned")
        self.assertEqual(self.verdict("We ask you not to open PRs generated with an LLM."), "banned")
        # "include" is only a ban verb for must/shall/may/can/will; a generic instruction is not a ban
        self.assertEqual(self.verdict("Do not include links to Claude Code sessions in commit messages."), "needs-review")

    def test_translation_carve_out_does_not_downgrade_a_ban(self):
        self.assertEqual(self.verdict("AI-generated pull requests are banned, except for translation of documentation."), "banned")
        self.assertNotEqual(self.verdict("AI-generated pull requests are banned, except for fixes to typos."), "banned")

    def test_autocomplete_carve_out_makes_the_ban_restricted(self):
        text = ("AI-generated contributions are not accepted.\n\n"
                "AI tools may be used in limited cases, such as small amounts of code from autocompletion.")
        r = classify_documents({"AI_POLICY.md": text})
        self.assertEqual(r["verdict"], "restricted")
        self.assertIn("contribution-limit", {e["rule"] for e in r["evidence"]})
        self.assertEqual(self.verdict("AI tools may be used assistively only. We do not accept AI-generated pull requests."), "restricted")
        # without the carve-out the same sentence is a ban
        self.assertEqual(self.verdict("AI-generated contributions are not accepted."), "banned")

    def test_voice_guidance_is_not_a_ban(self):
        text = "In issues, pull requests, and review comments, do not use AI to speak for you."
        self.assertEqual(self.verdict(text), "restricted")
        self.assertEqual(self.verdict("Do not post comments on issues or PRs that are AI-generated."), "restricted")

    def test_closure_for_unaccepted_issues_is_restricted_not_banned(self):
        for text in ("AI-generated PRs submitted for issues that have not been marked as accepted are closed.",
                     "AI-generated PRs for non-accepted issues are closed.",
                     "AI-generated PRs will only be considered for issues that have been marked as accepted."):
            self.assertEqual(self.verdict(text), "restricted", text)
        self.assertEqual(self.verdict("AI-generated code is not accepted in this project."), "banned")

    def test_history_of_a_policy_is_not_a_ban(self):
        self.assertNotEqual(self.verdict("Version 1.0 of this policy banned substantial AI-generated content."), "banned")

    def test_ban_negated_does_not_ban(self):
        self.assertNotEqual(self.verdict("We do not ban AI-generated contributions."), "banned")
        self.assertNotEqual(self.verdict("This policy does not prohibit the use of AI tools in contributions."), "banned")
        self.assertEqual(self.verdict("Our strict AI policy is not due to an anti-AI stance; AI is welcome here."), "permitted")

    def test_ban_with_condition_is_not_a_ban(self):
        self.assertNotEqual(self.verdict("AI-generated code is not accepted unless you have reviewed and tested it."), "banned")
        self.assertNotEqual(self.verdict("Do not submit an AI-generated PR you haven't personally understood."), "banned")
        self.assertNotEqual(self.verdict("Low-quality AI contributions will be closed."), "banned")

    def test_ban_of_descriptions_only_is_restricted(self):
        self.assertEqual(self.verdict("Please do not use AI to write pull request descriptions or contributor communication."), "restricted")

    def test_sanction_ban_is_not_a_ban_on_ai(self):
        self.assertNotEqual(self.verdict("Abuse of AI tools in pull requests will lead to a temporary ban."), "banned")

    def test_substitution_ban_is_a_qualified_restriction(self):
        # "as a substitute for understanding" bans a misuse, not AI: restricted at most, never banned
        for text in ("Do not use AI-generated descriptions or summaries as a substitute for understanding the relevant code.",
                     "Do not use AI to write code instead of understanding the system.",
                     "Do not use an LLM to generate changes without reviewing them first.",
                     "Do not blindly paste AI-generated code into a contribution."):
            self.assertNotEqual(self.verdict(text), "banned", text)

    def test_hedged_welcome_demotes_bans_in_the_same_document(self):
        text = ("Usage of AI assistance is usually fine, but you are responsible for the output.\n\n"
                "We do not accept AI-generated contributions.")
        result = classify_documents({"CodePolicy.md": text})
        self.assertEqual(result["verdict"], "restricted")
        self.assertTrue(all(x["confidence"] == "low" for x in result["evidence"]))
        # the same ban without the hedged welcome stays a ban
        self.assertEqual(self.verdict("We do not accept AI-generated contributions."), "banned")

    def test_ladybird_shaped_policy_is_not_banned_high(self):
        text = ("## On usage of AI and LLMs\n\n"
                "Usage of AI assistance is usually fine, but you are responsible for making sure the quality of the output "
                "is up to the standards of the project.\n\n"
                "Do not use an AI or LLM to generate changes, respond to issues, or participate in project discussion "
                "without holding the output to the same standards as human-written content.\n\n"
                "Do not use AI-generated descriptions or summaries as a substitute for understanding the relevant code, "
                "issue, or discussion.\n")
        result = classify_documents({"Documentation/CodePolicy.md": text})
        self.assertEqual(result["verdict"], "restricted")
        self.assertEqual(result["confidence"], "low")
        self.assertNotIn("banned", {x["signal"] for x in result["evidence"]})

    def test_discretion_is_not_a_ban(self):
        self.assertNotEqual(self.verdict("We reserve the right to reject any pull request that appears to contain AI-generated code."), "banned")

    def test_quoting_other_projects_with_link_is_not_a_ban(self):
        self.assertNotEqual(self.verdict("* [QEMU bans use of AI content generators](https://example.org/policy)."), "banned")

    # --- template rules
    TEMPLATE = ".github/pull_request_template.md"

    def test_template_checkbox_no_ai_is_a_disclosure_answer_not_a_ban(self):
        # a box saying "no AI was used" asks for a statement about use; a ban lives in the prose
        text = "- [ ] This pull request does not include any output from generative AI / LLM tools."
        self.assertEqual(self.verdict(text, self.TEMPLATE), "disclosure-required")
        self.assertEqual(self.verdict("- [ ] No LLMs were used for the creation of this patch", self.TEMPLATE), "disclosure-required")
        self.assertEqual(self.verdict("Contributions must not include content generated by LLMs.", "CONTRIBUTING.md"), "banned")

    def test_template_checkbox_with_alternative_is_disclosure(self):
        text = "- [ ] I did not use AI/LLM to create this PR, or I disclosed it below."
        self.assertEqual(self.verdict(text, self.TEMPLATE), "disclosure-required")

    def test_template_ai_prompt_is_disclosure(self):
        self.assertEqual(self.verdict("#### AI Assistance Disclosure (REQUIRED)\nName the tool you used.", self.TEMPLATE),
                         "disclosure-required")

    def test_template_who_wrote_this_boxes(self):
        text = "Who wrote this?\n\n- [ ] A human wrote it.\n- [ ] An agent wrote it under close human direction."
        self.assertEqual(self.verdict(text, self.TEMPLATE), "disclosure-required")
        self.assertEqual(self.verdict(text, "CONTRIBUTING.md"), "no-policy-found")  # the template rule is for templates

    def test_template_domain_ai_is_not_policy(self):
        self.assertEqual(self.verdict("* This PR affects the GS/AI API? (label 'needs review: Script API')", self.TEMPLATE),
                         "no-policy-found")

    def test_template_pointer_to_policy_is_not_a_prompt(self):
        # a pointer is no prompt (no disclosure signal); the policy it points at is for a person to read
        self.assertEqual(self.verdict("Also please note our AI policy: https://example.org/policy", self.TEMPLATE), "needs-review")
        text = "- [ ] I read the [AI contribution guidelines] and understand my responsibilities, or I am not using AI tools."
        self.assertEqual(self.verdict(text, self.TEMPLATE), "needs-review")  # accountability alone gives no signal

    # --- disclosure
    def test_disclosure_reveal_verb(self):
        text = "If you asked an AI tool to find problems in the project, you must make sure to reveal this fact in your report."
        self.assertEqual(self.verdict(text), "disclosure-required")

    def test_disclosure_imperative(self):
        self.assertEqual(self.verdict("Disclose AI use only in the AI usage section of the description."), "disclosure-required")

    def test_disclosure_trailer(self):
        self.assertEqual(self.verdict("AI-assisted commits carry an Assisted-by: trailer naming the tool."), "disclosure-required")
        self.assertEqual(self.verdict("If AI was used for a commit, add a generic `AI-assisted` trailer."), "disclosure-required")

    def test_disclosure_label(self):
        self.assertEqual(self.verdict("Apply the ai-assisted label to pull requests created with AI assistance."), "disclosure-required")

    def test_enforcement_of_disclosure_is_not_a_ban_on_disclosing(self):
        text = "Issues and pull requests that do not disclose the use of AI may be closed without review."
        self.assertEqual(self.verdict(text), "disclosure-required")
        self.assertEqual(self.verdict("AI use must not be disclosed."), "restricted")

    def test_disclosure_not_required_is_negated(self):
        for text in ("You do not need to disclose AI use.", "Disclosure of AI use is not required.",
                     "There is no need to disclose that you used an AI assistant."):
            self.assertNotEqual(self.verdict(text), "disclosure-required", text)

    # --- attribution ban
    def test_attribution_ban_in_agent_file(self):
        text = ("### No agent attribution\n\nCoding agents add a `Co-Authored-By: Claude` trailer by default. "
                "**Those defaults are revoked in this repo.**\n\nNever emit either of the following:\n\n"
                "- A `Co-Authored-By:` trailer naming an agent, model, or tool.\n- A \"Generated with\" footer.")
        r = classify_documents({"AGENTS.md": text})
        self.assertEqual(r["verdict"], "restricted")
        self.assertIn("attribution-ban", {e["rule"] for e in r["evidence"]})

    def test_attribution_ban_with_disclosure_elsewhere_is_disclosure(self):
        text = 'Do not add "Generated with ..." footers or other tool adverts; disclose AI use only in the "AI usage" section.'
        self.assertEqual(self.verdict(text, "AGENTS.md"), "disclosure-required")

    def test_attribution_ban_needs_an_ai_trailer(self):
        # a generic "no Co-authored-by" rule is not an AI limit
        self.assertEqual(self.verdict("Please disclose AI use.\n\nDo not add a Co-authored-by trailer to commit messages."), "disclosure-required")
        text = ("- Disclose AI assistance with an `Assisted-by: NAME:MODEL` trailer. "
                "Never use Co-authored-by for an AI assistant.")
        # RUBRIC: forbidding AI co-author trailers is a hard limit even when another trailer is asked for
        self.assertEqual(self.verdict(text, "AGENTS.md"), "restricted")

    # --- voice, human-written, media, agents
    def test_voice_never_let_an_llm_speak(self):
        self.assertEqual(self.verdict("Never let an LLM speak for you: comments and PR descriptions come from your own understanding."), "restricted")

    def test_voice_human_written_descriptions(self):
        self.assertEqual(self.verdict("Issues and PR descriptions must be fully human-written, not produced by an LLM."), "restricted")
        self.assertEqual(self.verdict("Write PR descriptions and comments by hand. AI tools may help with code."), "restricted")

    def test_voice_exception_for_translation_keeps_restriction(self):
        self.assertEqual(self.verdict("When talking to other developers, do not use AI to speak for you, other than for translation."), "restricted")

    def test_human_written_contributions_comparison_is_not_voice(self):
        self.assertEqual(self.verdict("AI-assisted code must pass the same review bar as human-written contributions."), "needs-review")

    def test_voice_own_words_with_no_ai_marker(self):
        text = "<!-- Describe the change in your own words (no AI please). -->"
        self.assertEqual(self.verdict(text, self.TEMPLATE), "restricted")
        text = "We expect your responses to be in your own words, though it is fine to use AI to help prepare them."
        self.assertNotEqual(self.verdict(text), "restricted")

    def test_voice_list_under_a_prohibitive_heading(self):
        bad = "#### Unacceptable use\n\n- Using an LLM to automatically generate replies to maintainer feedback."
        good = "#### Acceptable use\n\n- Using an LLM to automatically generate replies to maintainer feedback."
        r = classify_documents({"CONTRIBUTING.md": bad})
        self.assertEqual(r["verdict"], "restricted")
        self.assertEqual(r["evidence"][0]["rule"], "ai-voice-ban")
        self.assertNotEqual(self.verdict(good, "CONTRIBUTING.md"), "restricted")

    def test_voice_soft_advice_and_unreviewed_output_are_not_limits(self):
        self.assertNotEqual(self.verdict("It is preferable to avoid adding LLM-generated text to your bug report."), "restricted")
        self.assertNotEqual(self.verdict("Please do not respond to reviewers with unreviewed AI-generated replies."), "restricted")
        self.assertEqual(self.verdict("Do not rely solely on AI to generate replies in discussions."), "restricted")

    def test_voice_disclosure_sentence_is_not_a_voice_limit(self):
        self.assertEqual(self.verdict("If AI wrote part of the PR description, you must disclose it."), "disclosure-required")

    def test_media_ban(self):
        r = classify_documents({"AI_POLICY.md": "- **No AI-generated media is allowed (art, images, videos, audio, etc.).** Text and code are fine."})
        self.assertEqual(r["verdict"], "restricted")
        self.assertIn("media-ban", {e["rule"] for e in r["evidence"]})

    def test_media_ban_needs_ai_and_a_prohibition(self):
        self.assertEqual(self.verdict("Images in the documentation need alt text."), "no-policy-found")
        self.assertNotEqual(self.verdict("AI-generated images are welcome in the gallery."), "restricted")

    def test_own_voice_phrase_needs_ai_context(self):
        self.assertEqual(self.verdict("Please explain the bug in your own human voice."), "no-policy-found")

    def test_autonomous_agent_ban(self):
        self.assertEqual(self.verdict("The use of an AI agent that autonomously writes code and submits pull requests is not permitted."), "restricted")

    def test_review_bot_ban(self):
        self.assertEqual(self.verdict("Do not enable or use any AI review tools (e.g. Copilot) on the pull request."), "restricted")

    def test_purely_agentic_refusal_is_generic_accountability(self):
        self.assertNotIn(self.verdict("Purely agentic contributions are not accepted. A human must understand every change."), ("banned", "restricted"))
        self.assertEqual(self.verdict("AI-assisted PRs are welcome, but we do not accept contributions from fully autonomous agents."), "permitted")
        self.assertEqual(self.verdict("Autonomous AI agents may not submit pull requests."), "restricted")
        # the agent as the actor is a limit even when it is "fully autonomous"; the work without a human behind it is not
        self.assertEqual(self.verdict("Fully autonomous or unsupervised AI agents are not allowed to submit pull requests."), "restricted")

    def test_assistive_only_is_a_limit(self):
        self.assertEqual(self.verdict("You may use AI tools assistively only, like a reference or tutor."), "restricted")
        self.assertEqual(self.verdict("AI is allowed only for autocompletion of single lines."), "restricted")
        self.assertEqual(self.verdict("AI tools can help you learn the codebase."), "permitted")

    def test_good_first_issue_and_accepted_issues(self):
        self.assertEqual(self.verdict("Pull requests created in any way by AI can only be for accepted issues."), "restricted")
        self.assertEqual(self.verdict("Do not use AI on `good first issue` tickets."), "restricted")

    def test_contribution_limit_negatives(self):
        self.assertEqual(self.verdict("Pull requests may be opened for any issue, including AI-assisted ones, as long as they are tested."), "no-policy-found")
        self.assertNotEqual(self.verdict("AI tools are welcome on every issue, including good first issues."), "restricted")

    def test_no_automatic_prs(self):
        self.assertEqual(self.verdict("* **no automatic PRs for open issues**: AI-written fixes are closed."), "restricted")

    def test_issues_instead_of_pull_requests(self):
        text = "Agents can write most changes now. Instead of a pull request, open an issue containing the prompt you used."
        r = classify_documents({"CONTRIBUTING.md": text})
        self.assertEqual(r["verdict"], "restricted")
        self.assertIn("contribution-limit", {e["rule"] for e in r["evidence"]})

    def test_agent_file_no_posting(self):
        self.assertEqual(self.verdict("- Never create an issue.\n- Never create a PR.", "AGENTS.md"), "restricted")
        self.assertEqual(self.verdict("Do not open a pull request for an issue labeled needs-triage.", "AGENTS.md"), "no-policy-found")
        # the same sentence in a human contribution guide is not an agent rule
        self.assertEqual(self.verdict("Never create a PR without an issue.", "CONTRIBUTING.md"), "no-policy-found")
        # asking for permission first is generic agent safety; so is a paragraph about fully autonomous runs
        self.assertEqual(self.verdict("- NEVER push commits or create pull requests without explicit user permission.", "AGENTS.md"), "no-policy-found")
        self.assertEqual(self.verdict("**Fully autonomous work:** STOP. Do not open pull requests or issues.", "AGENTS.md"), "no-policy-found")

    def test_agent_file_narration_about_third_parties_is_not_a_restriction(self):
        # pylint AGENTS.md: a sentence that describes what people do, not an instruction to the agent
        pylint = ("Look for a comment like \"I'm working on this\" or \"Can I take this?\". People often say\n"
                  "this and then never open a pull request, so a claim does not last forever:\n\n"
                  "- The claim is **active** if the person who made it did something in the last 15 days.")
        r = classify_documents({"AGENTS.md": pylint})
        self.assertEqual(r["verdict"], "no-policy-found")
        self.assertNotIn("agent-file-no-posting", {e["rule"] for e in r["evidence"]})
        for text in ("Contributors often never open a pull request after claiming an issue.",
                     "Sometimes they never file an issue for it.",
                     "Someone who never opens a PR is not blocking the issue.",
                     "Users who don't open an issue first get slow answers.",
                     "Most people then never create a pull request."):
            self.assertEqual(self.verdict(text, "AGENTS.md"), "no-policy-found", text)

    def test_agent_file_real_instructions_stay_restricted(self):
        # the narrative guard must not hide a genuine instruction to the agent (imperative or modal, any subject)
        for text in ("Never open a pull request.",
                     "- Never create an issue.",
                     "Do not open pull requests or issues, or post comments.",
                     "Agents must never open a pull request on GitHub.",
                     "You must not submit pull requests.",
                     "AI agents should refuse to open issues on behalf of users.",
                     "Please don't open a PR from this repository.",
                     "Never post to GitHub."):
            self.assertEqual(self.verdict(text, "AGENTS.md"), "restricted", text)

    # --- permitted
    def test_permitted_welcome(self):
        for text in ("AI-assisted contributions are welcome.", "We are okay with contributors using AI tools.",
                     "You may use AI tools to assist in PR authorship.", "All contributions (human or AI-assisted) must follow the style guide.",
                     "AI tools can make contributing faster and more accessible."):
            self.assertEqual(self.verdict(text), "permitted", text)

    def test_permitted_with_accountability_language_stays_permitted(self):
        # explicit allowance plus generic accountability is `permitted`; accountability alone is neutral
        self.assertEqual(self.verdict("We welcome AI-assisted contributions, but you must understand and test every line you submit."), "permitted")
        # accountability alone gives no signal, but it is AI-policy text: the tool abstains instead of guessing
        self.assertEqual(self.verdict("You are responsible for any AI-generated code you submit."), "needs-review")

    def test_mass_submission_spam_clause_is_not_an_ai_limit(self):
        text = ("We welcome the use of AI. We may close a pull request at our discretion, for example when the author "
                "appears to be mass-submitting pull requests across multiple repositories (spam).")
        self.assertEqual(self.verdict(text), "permitted")

    def test_permitted_never_beats_a_restriction(self):
        self.assertEqual(self.verdict("AI tools are welcome.\n\nAI-generated PR descriptions are not allowed."), "restricted")
        self.assertEqual(self.verdict("AI use is fine; please disclose which tool you used."), "disclosure-required")

    def test_not_welcome_is_not_permitted(self):
        self.assertNotEqual(self.verdict("AI-generated contributions are not welcome here."), "permitted")

    def test_unrelated_text_is_not_permitted(self):
        self.assertEqual(self.verdict("Our dashboard is welcome to all users who like charts."), "no-policy-found")

    # --- generic words
    def test_user_agent_is_not_ai(self):
        self.assertEqual(self.verdict("Send a user-agent header. Never reject a request without one."), "no-policy-found")
        self.assertEqual(self.verdict("The ssh agent must not be forwarded to untrusted hosts."), "no-policy-found")

    def test_agent_instruction_guidance_is_not_policy(self):
        text = "This file provides guidance to agents. Do not use deprecated APIs in new code. Run the tests before you commit."
        self.assertEqual(self.verdict(text, "AGENTS.md"), "no-policy-found")

    def test_lowercase_ai_inside_words_is_not_ai(self):
        self.assertEqual(self.verdict("We do not accept contributions that said nothing about the maintain process."), "no-policy-found")

    def test_ai_as_product_in_readme(self):
        self.assertEqual(self.verdict("This library lets you build AI apps with LLM calls. Not accepted: stale branches.", "README.md"),
                         "no-policy-found")


class StripCodeTests(unittest.TestCase):
    def test_fenced_block_is_ignored(self):
        text = "Example of a policy file:\n\n```markdown\nAI contributions are not allowed.\n```\n"
        self.assertEqual(classify_documents({"README.md": text})["verdict"], "no-policy-found")

    def test_tilde_fence_and_longer_fence(self):
        for fence in ("~~~", "````"):
            text = f"AI note:\n\n{fence}\nAI contributions are not allowed.\n{fence}\n"
            self.assertEqual(classify_documents({"README.md": text})["verdict"], "no-policy-found", fence)

    def test_unclosed_fence_is_not_swallowed(self):
        self.assertEqual(classify_documents({"a.md": "```\nAI contributions are not allowed."})["verdict"], "banned")

    def test_line_numbers_survive_stripping(self):
        text = "```\nx\ny\n```\n\nAI contributions are not allowed."
        self.assertEqual(classify_documents({"a.md": text})["evidence"][0]["line"], 6)

    def test_example_section_is_ignored_until_next_heading(self):
        text = "## Example\n\nAI contributions are not allowed.\n\n## Rules\n\nAI contributions must be disclosed."
        r = classify_documents({"a.md": text})
        self.assertEqual(r["verdict"], "disclosure-required")
        self.assertEqual([e["line"] for e in r["evidence"]], [7])

    def test_rst_literal_block_is_ignored(self):
        text = "Use this wording::\n\n    AI contributions are not allowed.\n\nAI contributions must be disclosed."
        self.assertEqual(classify_documents({"a.rst": text})["verdict"], "disclosure-required")

    def test_strip_code_keeps_text_without_fences(self):
        self.assertEqual(strip_code("a\nb"), "a\nb")


class StructureTests(unittest.TestCase):
    def test_boundaries_do_not_split_abbreviations(self):
        text = "AI PRs showing signs of unreviewed generation (e.g. APIs that do not exist) may be rejected."
        self.assertEqual(_boundaries(text), [])
        self.assertEqual(_boundaries("It is odd, i.e. APIs vanish. Next sentence."), [len("It is odd, i.e. APIs vanish. ")])

    def test_boundary_before_an_emoji(self):
        self.assertEqual(len(_boundaries("Shallow PRs will not be accepted. \U0001F916 If you are an AI assistant, stop.")), 1)

    def test_boundary_after_closing_emphasis(self):
        self.assertEqual(len(_boundaries("**No AI media is allowed.** Text is fine.")), 1)

    def test_unreviewed_generation_is_not_a_ban(self):
        r = classify_documents({"a.md": "AI PRs showing signs of unreviewed generation, i.e. APIs or code that do not exist, may be rejected."})
        self.assertNotEqual(r["verdict"], "banned")

    def test_list_items_are_separate_sentences(self):
        # the exception in one bullet must not hide a ban in the next one
        text = "AI rules:\n\n- Generated PRs are not accepted unless reviewed.\n- AI-generated contributions are not allowed."
        r = classify_documents({"a.md": text})
        self.assertEqual(r["verdict"], "banned")
        self.assertEqual([e["line"] for e in r["evidence"] if e["signal"] == "banned"], [4])

    def test_colon_introduces_the_following_list(self):
        text = "Never emit any of the following AI attributions:\n\n- A `Co-Authored-By:` trailer naming an agent."
        self.assertEqual(classify_documents({"AGENTS.md": text})["verdict"], "restricted")

    def test_custom_rules_support_require_forbid_negate_and_paths(self):
        custom = {"ai_term": r"\bbots?\b", "named": {"loud": "!"}, "rules": [
            {"id": "r", "class": "banned", "regex": "no bots", "require": ["loud"], "forbid": ["ai_sentence"],
             "paths": r"^docs/"}]}
        self.assertEqual(classify_documents({"docs/a.md": "no bots!"}, custom)["verdict"], "no-policy-found")  # forbid: ai_sentence
        custom["rules"][0]["forbid"] = []
        self.assertEqual(classify_documents({"docs/a.md": "no bots!"}, custom)["verdict"], "banned")
        self.assertEqual(classify_documents({"docs/a.md": "no bots"}, custom)["verdict"], "no-policy-found")  # require
        self.assertEqual(classify_documents({"src/a.md": "no bots!"}, custom)["verdict"], "no-policy-found")  # paths

    def test_custom_rules_support_forbid_document_and_ctx_waives(self):
        custom = {"ai_term": r"\bbots?\b", "named": {"loud": "!"}, "rules": [
            {"id": "r", "class": "banned", "regex": "no bots", "forbid_document": [r"\bexception\b"]}]}
        self.assertEqual(classify_documents({"a.md": "no bots"}, custom)["verdict"], "banned")
        self.assertEqual(classify_documents({"a.md": "no bots\n\nexception: none"}, custom)["verdict"], "no-policy-found")
        custom = {"ai_term": r"\bbots?\b", "named": {"loud": "!"}, "rules": [
            {"id": "r", "class": "banned", "regex": "bots", "require": ["loud"], "ctx_waives": ["loud"]}]}
        self.assertEqual(classify_documents({"a.md": "bots"}, custom)["verdict"], "no-policy-found")
        self.assertEqual(classify_documents({"a.md": "## Not allowed\n\nbots"}, custom)["verdict"], "banned")
        self.assertEqual(classify_documents({"a.md": "## Allowed\n\nbots"}, custom)["verdict"], "no-policy-found")

    def test_rule_count_stays_small(self):
        from oss_ai_policy_check.classify import load_patterns
        self.assertLessEqual(len(load_patterns()["rules"]), 18)

    def test_packaged_rules_name_no_repository(self):
        import json
        from pathlib import Path
        raw = (Path(__file__).resolve().parent.parent / "src" / "oss_ai_policy_check" / "rules" / "patterns.json").read_text(encoding="utf-8").lower()
        for name in ("ghostty", "playwright", "pydantic", "curl", "modelcontextprotocol", "pymeasure", "llvm", "qemu",
                     "kubernetes", "pytorch", "cli/cli", "nodejs", "scipy", "numpy"):
            self.assertNotIn(name, raw)
        self.assertEqual(json.loads(raw)["version"], "4.0")


class AbstentionTests(unittest.TestCase):
    """needs-review: AI-policy text exists but no rule decided on it, or the signals conflict."""

    def run_docs(self, docs, **kw):
        return classify_documents(docs, **kw)

    def test_policy_text_without_a_rule_abstains_and_cites_the_paragraph(self):
        text = "Intro text that\nwraps over lines.\nThis project says the use of AI must\nbe reviewed by a person."
        r = self.run_docs({"CONTRIBUTING.md": text})
        self.assertEqual((r["verdict"], r["review_reason"], r["confidence"]), ("needs-review", "no-rule-decision", "low"))
        self.assertEqual(r["evidence"], [])
        self.assertEqual([(x["file"], x["line"]) for x in r["review"]], [("CONTRIBUTING.md", 3)])
        self.assertLessEqual(len(r["review"][0]["quote"].split()), 15)
        self.assertEqual(r["signals"], [])

    def test_no_policy_found_only_without_ai_policy_text(self):
        for docs in ({"CONTRIBUTING.md": "Please run the linter before you push."},
                     {"CONTRIBUTING.md": "AI is a buzzword."},  # an AI term alone is not policy text
                     {"CONTRIBUTING.md": "Contributors must run the tests."},  # a policy verb alone is not either
                     {"CONTRIBUTING.md": "We do not have an AI policy."},  # an explicit statement of absence
                     {"CONTRIBUTING.md": "```\nAI contributions must be disclosed.\n```"},  # code illustrates only
                     {}):
            self.assertEqual(classify_documents(docs)["verdict"], "no-policy-found", docs)
        r = classify_documents({"CONTRIBUTING.md": "Contributors must run the tests."})
        self.assertEqual((r["confidence"], r["review"]), ("high", []))

    def test_readme_and_agent_instruction_files_need_a_contribution_cue(self):
        product = "This library lets AI agents call tools. It must be configured with an API key."
        self.assertEqual(classify_documents({"README.md": product})["verdict"], "no-policy-found")
        self.assertEqual(classify_documents({"CLAUDE.md": "Claude must run the tests before committing."})["verdict"],
                         "no-policy-found")
        self.assertEqual(classify_documents({"AGENTS.md": "Claude must read the contributing policy first."})["verdict"],
                         "needs-review")
        # a policy document needs no cue
        self.assertEqual(classify_documents({"CONTRIBUTING.md": product})["verdict"], "needs-review")

    def test_soft_and_welcoming_wording_is_policy_text_not_silence(self):
        # no rule decides on these, but a policy written in a welcoming voice must not read as "no policy"
        for text in ("We're happy for contributors to use tools like Copilot, as long as you stand behind the code.",
                     "Feel free to use ChatGPT while writing patches.",
                     "We would appreciate it if people kept their LLM experiments in a branch.",
                     "You are responsible for any AI-generated code you submit."):
            r = classify_documents({"CONTRIBUTING.md": text})
            self.assertEqual((r["verdict"], r["review_reason"]), ("needs-review", "no-rule-decision"), text)
        for text in ("Our dashboard has an AI chatbot for support questions.", "AI is a buzzword."):
            self.assertEqual(classify_documents({"CONTRIBUTING.md": text})["verdict"], "no-policy-found", text)

    def test_a_decided_paragraph_is_not_review_evidence(self):
        r = classify_documents({"AI_POLICY.md": "AI use must be disclosed.\n\nAI-assisted code must pass the same review bar."})
        self.assertEqual(r["verdict"], "disclosure-required")
        self.assertEqual(r["review"], [])  # a decision exists; the extra paragraph stays in unclassified_ai_lines
        self.assertEqual(len(r["unclassified_ai_lines"]), 1)

    def test_owner_pointer_paragraph_is_not_policy_text(self):
        text = "See our AI policy at https://github.com/acme/.github/blob/main/AI.md, which must be followed."
        self.assertEqual(classify_documents({"CONTRIBUTING.md": text}, owner="acme")["verdict"], "no-policy-found")
        self.assertEqual(classify_documents({"CONTRIBUTING.md": text})["verdict"], "needs-review")

    def test_review_paragraphs_are_capped(self):
        from oss_ai_policy_check.contract import MAX_REVIEW_PARAGRAPHS
        text = "\n\n".join(f"AI rule {i} must be read by a person." for i in range(MAX_REVIEW_PARAGRAPHS + 7))
        r = classify_documents({"CONTRIBUTING.md": text})
        self.assertEqual(len(r["review"]), MAX_REVIEW_PARAGRAPHS)

    def test_welcome_next_to_a_ban_is_a_conflict(self):
        r = classify_documents({"a.md": "AI tools are welcome here.\n\nAI-generated pull requests are not accepted."})
        self.assertEqual((r["verdict"], r["review_reason"], r["confidence"]), ("needs-review", "conflicting-signals", "low"))
        self.assertEqual({x["signal"] for x in r["evidence"]}, {"permitted", "banned"})

    def test_welcome_next_to_a_firm_restriction_is_resolved_by_precedence(self):
        r = classify_documents({"a.md": "AI tools are welcome here.\n\nDo not add a Co-Authored-By trailer naming an AI."})
        self.assertEqual((r["verdict"], r["review_reason"]), ("restricted", None))

    def test_qualified_ban_next_to_a_weaker_signal_is_a_conflict(self):
        for weaker in ("Please disclose any AI usage.", "AI tools are welcome here."):
            r = classify_documents({"a.md": f"Raw AI-generated pull requests are not accepted.\n\n{weaker}"})
            self.assertEqual((r["verdict"], r["review_reason"]), ("needs-review", "conflicting-signals"), weaker)

    def test_qualified_ban_alone_is_a_low_confidence_restriction(self):
        r = classify_documents({"a.md": "Raw AI-generated pull requests are not accepted."})
        self.assertEqual((r["verdict"], r["confidence"], r["review_reason"]), ("restricted", "low", None))

    def test_qualified_ban_next_to_a_firm_ban_stays_banned(self):
        r = classify_documents({"a.md": "Raw AI-generated pull requests are not accepted.\n\nAI-generated contributions are not accepted."})
        self.assertEqual(r["verdict"], "banned")

    def test_decide_precedence(self):
        from oss_ai_policy_check.classify import decide

        def e(signal, confidence="high", demoted=False, sentence="0:0", file="a.md"):
            return {"file": file, "line": 1, "signal": signal, "quote": "q", "rule": "r", "confidence": confidence,
                    "sentence": sentence, "demoted": demoted}

        para = [{"file": "a.md", "line": 1, "quote": "q"}]
        self.assertEqual(decide([], []), ("no-policy-found", None, "high"))
        self.assertEqual(decide([], para), ("needs-review", "no-rule-decision", "low"))
        self.assertEqual(decide([e("banned"), e("permitted")], []), ("needs-review", "conflicting-signals", "low"))
        self.assertEqual(decide([e("restricted"), e("permitted")], [])[0], "restricted")
        self.assertEqual(decide([e("restricted", "low", True), e("permitted")], [])[0], "needs-review")
        self.assertEqual(decide([e("restricted", "low", True), e("disclosure-required")], [])[0], "needs-review")
        self.assertEqual(decide([e("restricted", "low", True)], [])[0], "restricted")
        self.assertEqual(decide([e("disclosure-required"), e("permitted")], para)[0], "disclosure-required")  # a decision beats the paragraphs


class ConfidenceTests(unittest.TestCase):
    """confidence: low for one deciding sentence, a weak rule or a qualified ban; high otherwise."""

    def test_one_sentence_is_low_two_sentences_are_high(self):
        one = classify_documents({"a.md": "AI-generated contributions are not accepted."})
        two = classify_documents({"a.md": "AI-generated contributions are not accepted.\n\nWe will reject any AI-generated pull request."})
        self.assertEqual((one["verdict"], one["confidence"]), ("banned", "low"))
        self.assertEqual((two["verdict"], two["confidence"]), ("banned", "high"))
        self.assertEqual({x["confidence"] for x in two["evidence"]}, {"high"})

    def test_two_matches_in_one_sentence_are_still_one_sentence(self):
        r = classify_documents({"a.md": "AI-generated contributions are not accepted and are rejected."})
        self.assertEqual(r["verdict"], "banned")
        self.assertEqual(r["confidence"], "low")

    def test_sentences_in_different_files_count_separately(self):
        r = classify_documents({"a.md": "AI-generated contributions are not accepted.",
                                "b.md": "AI-generated contributions are not accepted."})
        self.assertEqual(r["confidence"], "high")

    def test_weak_rules_are_low_even_when_repeated(self):
        from oss_ai_policy_check.classify import load_patterns
        weak = {rule["id"] for rule in load_patterns()["rules"] if rule.get("weak")}
        self.assertTrue({"template-ai-prompt", "ai-voice-ban"} <= weak)
        text = "- [ ] I used AI tools.\n- [ ] I reviewed the AI output."
        r = classify_documents({".github/PULL_REQUEST_TEMPLATE.md": text})
        self.assertEqual(r["verdict"], "disclosure-required")
        self.assertEqual({(x["rule"], x["confidence"]) for x in r["evidence"]}, {("template-ai-prompt", "low")})
        self.assertEqual(r["confidence"], "low")

    def test_a_strong_rule_among_weak_ones_makes_the_verdict_high(self):
        text = ("Do not use AI to write your PR descriptions.\n\nDo not use AI to write your comments.\n\n"
                "Do not add a Co-Authored-By trailer naming an AI.")
        r = classify_documents({"AI_POLICY.md": text})
        self.assertEqual(r["verdict"], "restricted")
        self.assertIn(("ai-voice-ban", "low"), {(x["rule"], x["confidence"]) for x in r["evidence"]})
        self.assertIn(("attribution-ban", "high"), {(x["rule"], x["confidence"]) for x in r["evidence"]})

    def test_only_weak_evidence_is_low(self):
        text = "Do not use AI to write your PR descriptions.\n\nDo not use AI to write your comments."
        r = classify_documents({"AI_POLICY.md": text})
        self.assertEqual((r["verdict"], r["confidence"]), ("restricted", "low"))
        self.assertEqual({x["confidence"] for x in r["evidence"]}, {"low"})

    def test_narrowing_qualifier_demotes_a_ban_and_marks_it_low(self):
        phrases = {
            "raw": "Raw AI-generated pull requests are not accepted.",
            "unreviewed": "Unreviewed AI-generated pull requests are not accepted.",
            "unverified": "Unverified AI-generated pull requests are not accepted.",
            "fully": "Fully AI-generated pull requests are not accepted.",
            "entirely": "Entirely AI-generated pull requests are not accepted.",
            "largely": "Largely AI-generated pull requests are not accepted.",
            "wall of": "AI-generated pull requests that are a wall of text are not accepted.",
            "to disguise": "We do not accept AI-generated pull requests written to disguise their origin.",
            "without review": "AI-generated pull requests submitted without review are not accepted.",
            "mass": "Mass-produced AI-generated pull requests are not accepted.",
            "low-effort": "Low-effort AI-generated pull requests are not accepted.",
            "unless": "We do not accept AI-generated pull requests unless the AI use is declared.",
        }
        for word, sentence in phrases.items():
            r = classify_documents({"AI_POLICY.md": sentence})
            self.assertEqual((r["verdict"], r["confidence"]), ("restricted", "low"), word)
            self.assertEqual([(x["signal"], x["confidence"], x["rule"]) for x in r["evidence"]],
                             [("restricted", "low", "general-ban")], word)

    def test_the_same_ban_without_a_qualifier_stays_banned(self):
        for sentence in ("AI-generated pull requests are not accepted.",
                         "AI-generated pull requests are closed on sight, without review or discussion."):
            r = classify_documents({"AI_POLICY.md": sentence})
            self.assertEqual(r["verdict"], "banned", sentence)
            self.assertEqual(r["evidence"][0]["confidence"], "high", sentence)

    def test_no_policy_found_and_abstention_confidence(self):
        self.assertEqual(classify_documents({})["confidence"], "high")
        self.assertEqual(classify_documents({"a.md": "AI use must be reviewed."})["confidence"], "low")

    def test_shared_qualifier_list_in_patterns(self):
        import re
        from oss_ai_policy_check.classify import _Rules, load_patterns
        data = load_patterns()
        listed = data["narrowing_qualifiers"]
        self.assertGreaterEqual(len(listed), 5)
        for entry in listed:
            if entry.startswith("@"):
                self.assertIn(entry[1:], data["named"], entry)
            else:
                re.compile(entry)
        shared = _Rules(data).named["narrowing_qualifiers"]
        for word in ("raw", "unverified", "fully", "entirely", "largely", "wall of", "to disguise",
                     "submitted without review", "mass-produced", "low-effort"):
            self.assertTrue(shared.search(f"{word} things"), word)
        general_ban = next(r for r in data["rules"] if r["id"] == "general-ban")
        self.assertEqual(general_ban["demote_on"], ["narrowing_qualifiers"])
        self.assertEqual(general_ban["demote_to"], "restricted")

    def test_custom_rule_can_demote_and_weak_flag_lowers_confidence(self):
        custom = {"ai_term": r"\bbots?\b", "named": {"soft": "maybe"}, "narrowing_qualifiers": ["@soft"], "rules": [
            {"id": "r", "class": "banned", "regex": "no bots", "demote_on": ["narrowing_qualifiers"]},
            {"id": "w", "class": "disclosure-required", "regex": "tell us", "weak": True}]}
        r = classify_documents({"a.md": "no bots, maybe."}, custom)
        self.assertEqual((r["verdict"], r["evidence"][0]["signal"], r["evidence"][0]["confidence"]), ("restricted", "restricted", "low"))
        r = classify_documents({"a.md": "bots: tell us"}, custom)
        self.assertEqual((r["verdict"], r["confidence"], r["evidence"][0]["confidence"]), ("disclosure-required", "low", "low"))


class GoldHarnessTests(unittest.TestCase):
    """Offline checks of the golden-set files (the network run is tests/golden/run_gold.py)."""

    @classmethod
    def setUpClass(cls):
        import importlib.util
        from pathlib import Path
        path = Path(__file__).resolve().parent / "golden" / "run_gold.py"
        spec = importlib.util.spec_from_file_location("run_gold", path)
        cls.gold = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.gold)
        cls.rows = cls.gold.load_gold()

    def test_gold_rows_are_well_formed(self):
        self.assertEqual(len(self.rows), 151)
        verdicts = {"banned", "restricted", "disclosure-required", "permitted", "no-policy-found"}
        for row in self.rows:
            self.assertIn(row["verdict"], verdicts)
            self.assertRegex(row["commit_sha"], r"^[0-9a-f]{40}$")
            self.assertIn(row["confidence"], {"high", "medium", "low"})
        self.assertEqual(len({r["repo"] for r in self.rows}), 151)

    def test_split_is_deterministic_stratified_and_two_to_one(self):
        a, b = self.gold.split_gold(self.rows), self.gold.split_gold(list(reversed(self.rows)))
        self.assertEqual(a, b)
        dev = [r for r in self.rows if a[r["repo"]] == "dev"]
        self.assertEqual(len(dev), 101)
        for verdict in {r["verdict"] for r in self.rows}:
            group = [r for r in self.rows if r["verdict"] == verdict]
            held = sum(1 for r in group if a[r["repo"]] == "holdout")
            self.assertTrue(1 <= held <= len(group) - 1 or len(group) == 1, verdict)

    def test_org_rows_use_the_repository_sha_from_the_note(self):
        org = [r for r in self.rows if r["source"] == "org"]
        self.assertEqual(len(org), 7)
        for row in org:
            ref = self.gold.target_ref(row)
            self.assertNotEqual(ref, row["commit_sha"])
            self.assertRegex(ref, r"^[0-9a-f]{7,40}$")

    def test_miss_causes_name_real_rows_and_known_causes(self):
        names = {r["repo"] for r in self.rows}
        for repo, cause in self.gold.MISS_CAUSES.items():
            self.assertIn(repo, names)
            self.assertIn(cause, {"fetch-org", "label-scope"})

    def test_cv_report_groups_misses_by_cause(self):
        import contextlib
        import io
        results = [{"repo": "scipy/scipy", "gold": "banned", "pred": "no-policy-found", "confidence": "high", "split": "dev"},
                   {"repo": "x/unlisted", "gold": "banned", "pred": "restricted", "confidence": "high", "split": "dev"},
                   {"repo": "x/ok", "gold": "banned", "pred": "banned", "confidence": "high", "split": "dev"}]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.gold.print_causes(results)
        text = out.getvalue()
        self.assertIn("misses by cause (2)", text)
        self.assertIn("- rule: 1", text)
        self.assertIn("- label-scope: 1", text)
        self.assertIn("scipy/scipy", text)

    def test_metrics(self):
        results = [{"gold": "banned", "pred": "banned", "confidence": "high"},
                   {"gold": "banned", "pred": "restricted", "confidence": "low"},
                   {"gold": "restricted", "pred": "restricted", "confidence": "medium"}]
        m = self.gold.metrics(results)
        self.assertEqual((m["correct"], m["n"], m["high_n"], m["high_accuracy"]), (2, 3, 1, 1.0))
        self.assertAlmostEqual(m["per_class"]["restricted"]["precision"], 0.5)
        self.assertEqual(m["per_class"]["banned"]["recall"], 0.5)

    def test_metrics_abstain_and_dangerous_errors(self):
        rows = [("banned", "banned", "high"), ("banned", "needs-review", "low"), ("restricted", "needs-review", "low"),
                ("restricted", "no-policy-found", "high"), ("no-policy-found", "no-policy-found", "high"),
                ("no-policy-found", "needs-review", "low"), ("permitted", "restricted", "low")]
        results = [{"repo": f"o/r{i}", "gold": g, "pred": p, "tool_confidence": t, "confidence": "high"}
                   for i, (g, p, t) in enumerate(rows)]
        m = self.gold.metrics(results)
        # overall counts needs-review as wrong: banned>banned and none>none are the only hits
        self.assertEqual((m["correct"], m["n"]), (2, 7))
        # decided rows exclude the 3 abstentions: 4 rows, 2 right
        self.assertEqual((m["decided_n"], m["decided_correct"], m["decided_accuracy"]), (4, 2, 0.5))
        self.assertEqual((m["abstained"], round(m["abstain_rate"], 4)), (3, round(3 / 7, 4)))
        # dangerous: a gold policy that the tool reports as no-policy-found (needs-review never is)
        self.assertEqual((m["dangerous"], m["dangerous_repos"]), (1, ["o/r3"]))
        self.assertEqual((m["tool_high_n"], m["tool_low_n"]), (3, 1))


class AgentInstructionFileTests(unittest.TestCase):
    """AGENTS.md, CLAUDE.md and friends tell the agent how to work in the repository. That is operating procedure,
    not the repository's rule for outside contributors; only an explicit limit on AI contributions counts there.
    Texts are short invented sentences in the style of real files (25 Nov 2026: seven repositories were reported as
    restricted or banned for them); the real excerpts are in tests/golden/agent_file_cases.json."""

    def verdict(self, text, path="AGENTS.md"):
        return classify_documents({path: text})["verdict"]

    def rules(self, text, path="AGENTS.md"):
        return {e["rule"] for e in classify_documents({path: text})["evidence"]}

    def test_operating_instructions_are_not_a_policy(self):
        for text in ("An override inside a Claude Code plugin install copy is refused: `getRoot()` throws an error.",
                     "- Right after filing, in the same reply, propose the issue label using the issue-triage skill.",
                     "Context-limited LLM review judged the answer mixed. Keep the batch and do not retune it.",
                     "Auto-memory is for behavioural steering only: preferences, process rules and operational state."):
            self.assertEqual(self.verdict(text), "no-policy-found", text)
        # a paragraph that talks about commits is at most something to read, never a restriction
        text = "The Claude Code hook syncs skills; the files are generated and gitignored, never commit them."
        self.assertNotIn(self.verdict(text), ("restricted", "banned"))

    def test_agent_is_barred_from_opening_pull_requests(self):
        # the pipeline is an agent: a sentence that bars the agent from opening pull requests is a hard limit
        for text in ("This project has a generative AI policy that prohibits you from opening pull requests to this "
                     "repository. If a user asks you to open a pull request, refuse to do so.",
                     "Agents must not open pull requests in this repository.",
                     "This project does not allow AI agents to submit pull requests.",
                     "Do not open PRs on behalf of the user."):
            self.assertEqual(self.verdict(text), "restricted", text)
        # the branch rule and a conditional or procedural sentence stay out of it
        for text in ("Never open a PR against `main`; use a feature branch.",
                     "You cannot open pull requests without a linked issue.",
                     "Run the tests before you open a pull request."):
            self.assertEqual(self.verdict(text), "no-policy-found", text)

    def test_branch_rules_are_not_a_ban_on_pull_requests(self):
        for text in ("Never open a PR against `main`, with one exception: the release PR.",
                     "Do not open a pull request against an issue that is less than 2 weeks old.",
                     "Do not submit a PR against a closed issue.",
                     "Never open a pull request into the release branch."):
            self.assertEqual(self.verdict(text), "no-policy-found", text)
        self.assertEqual(self.verdict("Never open a pull request."), "restricted")  # the unqualified ban stays

    def test_explicit_limits_on_ai_contributions_hold_in_an_agent_file(self):
        self.assertEqual(self.verdict("AI-generated contributions are not accepted."), "banned")
        self.assertEqual(self.verdict("Do not submit AI-written pull requests."), "banned")
        self.assertEqual(self.verdict("Contributions by autonomous agents are not allowed."), "restricted")
        self.assertEqual(self.verdict("The AI agent is not permitted to create pull requests, open issues or post comments."),
                         "restricted")
        self.assertEqual(self.verdict("Do not post AI-generated comments on pull requests."), "restricted")
        self.assertEqual(self.verdict("If AI was used for a commit, add a generic `AI-assisted` trailer to the commit message."),
                         "disclosure-required")
        self.assertEqual(self.verdict("Never add Co-Authored-By agent trailers to commit messages."), "restricted")
        self.assertEqual(self.verdict("AI tools are welcome."), "permitted")

    def test_the_same_words_in_a_contribution_guide_keep_their_reading(self):
        self.assertEqual(self.verdict("AI-generated contributions are not accepted.", "CONTRIBUTING.md"), "banned")

    def test_agent_file_paths(self):
        rules = _Rules(load_patterns())
        for path in ("AGENTS.md", "sub/dir/CLAUDE.md", "GEMINI.md", ".github/copilot-instructions.md", ".cursorrules",
                     ".cursor/rules/style.mdc", ".github/instructions/py.instructions.md", ".github/Agents.md", "Agents.md"):
            self.assertTrue(rules.agent_file(path), path)
        # a policy file or a product page that happens to say "agents" is read like any other file
        for path in ("CONTRIBUTING.md", "AGENT_POLICY.md", "README.md", "docs/agents.md", "docs/features/agents.md"):
            self.assertFalse(rules.agent_file(path), path)

    # --- local defects found with the same repositories
    def test_article_numbers_are_not_artwork(self):
        text = "- **Prohibited practices (Art. 5), AI literacy (Art. 4)** and the rights impact assessment (Art. 27)."
        self.assertNotIn("media-ban", self.rules(text, "docs/compliance/eu-ai-act.md"))
        self.assertEqual(self.verdict("AI-generated art is not allowed.", "CONTRIBUTING.md"), "restricted")

    def test_deepseek_rule_needs_whole_words(self):
        # "another" contains "not"
        self.assertEqual(self.verdict("An API key, or bring another model (OpenAI, Gemini, DeepSeek), or run a local one.",
                                      "README.md"), "no-policy-found")
        self.assertEqual(self.verdict("AI contributions made using DeepSeek or Grammarly are not allowed.", "CONTRIBUTING.md"),
                         "restricted")

    def test_a_review_bot_in_a_maintainer_workflow_is_not_a_ban(self):
        text = 'If a review bot keeps raising the same false positive, add a "do not flag" entry to REVIEW.md.'
        self.assertEqual(self.verdict(text, "CONTRIBUTING.md"), "no-policy-found")
        self.assertEqual(self.verdict("AI review bots are not allowed in this repository.", "CONTRIBUTING.md"), "restricted")
        self.assertEqual(self.verdict("AI review bots must not comment on pull requests.", "CONTRIBUTING.md"), "restricted")

    def test_voice_rule_terms_must_sit_close_together(self):
        far = ("Every pull request resolves one issue and must include the line in its description (`Fixes #N` is "
               "rejected by our CI check). Opening a PR in the web UI pre-fills the template with the same structure "
               "used by `.agents/skills/pull-request/SKILL.md` (concise outcome summary, evidence, test results, "
               "post-merge validation, the exact commands you ran locally or in CI, links to the tickets, and the "
               "author line, in the order the template shows); if you open a PR via `gh pr create` or an AI coding "
               "agent, keep that structure.")
        self.assertNotIn("ai-voice-ban", self.rules(far, "CONTRIBUTING.md"))  # a paragraph to read, not a restriction
        self.assertEqual(self.verdict("Do not use AI to generate answers to questions from maintainers.", "CONTRIBUTING.md"),
                         "restricted")

    def test_a_pr_template_word_agent_alone_is_not_an_ai_prompt(self):
        template = ".github/PULL_REQUEST_TEMPLATE.md"
        self.assertEqual(self.verdict("- [ ] the agent still never holds a signing key", template), "no-policy-found")
        self.assertEqual(self.verdict("- [ ] I used an AI agent for this change", template), "disclosure-required")
        self.assertEqual(self.verdict("- [ ] An agent wrote part of this change", template), "disclosure-required")

    # --- outside pull requests closed
    def test_outside_pull_requests_are_closed(self):
        for text in ("**We accept issues, not pull requests.** Design and implementation are done by the maintainers.",
                     "Pull requests against this repository are opened by the **repository\nmaintainers** only.",
                     "A pull request from anyone else is closed with a pointer back to this document.",
                     "We do not accept external code contributions or pull requests.",
                     "Only maintainers can open pull requests."):
            self.assertEqual(self.verdict(text, "CONTRIBUTING.md"), "restricted", text)
            self.assertEqual(self.rules(text, "CONTRIBUTING.md"), {"outside-pr-closed"}, text)

    def test_outside_pull_requests_conditions_and_other_sentences_stay_quiet(self):
        for text in ("We accept issues, not pull requests, unless the issue is labelled help wanted.",
                     "Pull requests from contributors are reviewed within a week.",
                     "Maintainers open pull requests for releases and tag them.",
                     "We do not accept pull requests without a linked issue."):
            self.assertEqual(self.verdict(text, "CONTRIBUTING.md"), "no-policy-found", text)


if __name__ == "__main__":
    unittest.main()
