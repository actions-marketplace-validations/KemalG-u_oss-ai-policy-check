# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Project health files: `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1) and `CITATION.cff`; an OpenSSF
  Scorecard workflow (`.github/workflows/scorecard.yml`, results published to the Scorecard API); README badges
  for CI, PyPI and Scorecard. The release workflow now states `attestations: true` (PEP 740) for the PyPI upload.
- Rule `agent-pr-prohibited` (`restricted`, agent instruction files only): a sentence that bars the agent from
  opening pull requests (conda: "prohibits you from opening pull requests"; "agents must not open pull requests";
  "do not open PRs on behalf of the user"). The pipeline is an agent, so this is a hard limit. Branch targets
  ("never open a PR against main") and conditions ("without", "unless") stay out. 31 cases now (conda added).
- Rule `outside-pr-closed` (`restricted`): pull requests from outside the maintainers are closed ("We accept
  issues, not pull requests", "pull requests are opened by the maintainers only", "A pull request from anyone else is
  closed", "We do not accept external code contributions or pull requests"). It needs no AI word.
- `tests/golden/agent_file_cases.json` and `tests/test_agent_file_cases.py`: 31 short real excerpts (repository,
  pinned commit, source lines) with the verdict they must get, seven repositories that were wrongly reported as
  restricted or banned, two closures that were missed and 21 real limits that must not be lost. `gold.jsonl` is
  unchanged.

### Fixed

- Agent instruction files (`AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, Copilot and Cursor instructions) no longer turn
  operating instructions for the agent into a restriction on contributors. `patterns.json` has a new `agent_files`
  entry; a rule reads such a file only in a paragraph that names contributing, `disclosure` and `ai-voice-ban` ask
  for more there (`agent_file_require`), and `attribution-ban`, `agent-file-no-posting` and `welcome` stay
  `agent_file: "exempt"`. Seen on F3-Nation/f3-nation, career-ops-hq/career-ops, shuxiachai/academic-commercialization-agent
  and modelcontextprotocol/servers (25 Nov 2026).
- `agent-file-no-posting` ignores an instruction that only names the target of a pull request ("Never open a PR
  against `main`", "Do not submit a PR against a closed issue"); "Never open a pull request." still matches.
- `agent-restriction` reads "the AI agent is not permitted to create pull requests", and its review-bot alternative
  needs the negation to be the predicate of the bot ("If a review bot keeps raising the same false positive, add a
  \"do not flag\" entry" is a maintainer workflow, F3-Nation).
- `media-ban`: "Art. 5" is an article number, not artwork (Cygnux-Labs/Tracekit, EU AI Act note).
- `contribution-limit`: the DeepSeek/Grammarly alternative matched "not" inside "another" (README requirement list of
  DilawarShafiq/nietzschian-debugger); it now needs whole words.
- `ai-voice-ban` (new `window` key): its AI term, voice target, authoring verb and prohibition must lie within
  300 characters, so a run-on Markdown sentence full of file names is no longer a voice ban (neomjs/neo).
- `template-ai-prompt`: the bare word "agent" in a pull request template ("the agent still never holds a signing
  key") is a product word, not a question about AI use; "AI agent", "agent ... used/wrote" still match.
- `agent-file-no-posting` no longer treats a sentence that narrates what other people do as a restriction on the
  agent ("People often say this and then never open a pull request", pylint `AGENTS.md`). Imperative and modal
  instructions ("Never open a pull request.", "Do not open pull requests or issues.", "Agents must never open ...")
  are still read as restrictions.
- `tests/golden/run_gold.py` reads the SHAs of `source: org` rows from the note marker that says what `commit_sha`
  is (`(repo sha ...)` in `gold.jsonl`, `(org sha ...)` in the gold4 file) and stops with an error when a row has
  neither or both, instead of pinning the organization `.github` repository to a repository commit. New option
  `--gold PATH` measures another label file.

## [0.3.0] - 2026-11-22

### Added

- Claude mode, optional and off by default: `--classifier claude [--model ID]` sends only the AI-policy
  paragraphs to a Claude model, which names the deciding paragraphs by id. It needs your own `ANTHROPIC_API_KEY`
  (or credentials the Anthropic SDK finds) and the optional extra `pip install 'oss-ai-policy-check[claude]'`.
  The base install keeps `dependencies = []`: the SDK is imported only when the flag is given, and without it the
  command exits with code 2 and an install hint. Evidence quotes and SHA-pinned links are still built from the
  fetched files; the model cannot invent a line or a link. A failed call (refusal, rate limit, API error,
  malformed answer) keeps the deterministic verdict and sets `complete: false`. Details, request settings and
  cost: `docs/llm-mode.md`.
- The report carries `classifier` (`{"kind": "rules|claude", "model": ...}`); `review_reason` gains
  `llm-low-confidence` and `llm-rules-disagree`, written only in Claude mode.
- Rules/LLM combination: a fixed table (`classify.combine`, no further call) joins the model verdict with the
  deterministic one. A quiet model answer against a rule hit, or a low-confidence disagreement, becomes
  `needs-review`; a confident disagreement keeps the model verdict at `low` confidence. The table is in
  `docs/llm-mode.md`.
- A heading that names AI now carries its body (the lines below it up to the next heading of the same or higher
  level, at most 60 lines and 1800 characters), so the rules written under such a heading reach the classifier;
  a heading is never sent alone.
- Policy-named documents are discovered: files whose name contains `policy` or `policies` (for example
  `Documentation/CodePolicy.md`) in the root, `.github`, `docs`, `doc`, `Documentation` or `policies` are
  extra candidates, ranked after AI-named files and capped at 3. A repository that keeps its AI clause in such a
  file is no longer reported as `no-policy-found`.
- `tests/golden/run_gold.py --classifier claude` measures Claude mode on the gold set, with an on-disk response
  cache.

### Changed

- The README and `docs/limitations.md` report measured accuracy for both modes on a fresh 60-repository blind
  set (see "Measured accuracy" in the README); the older blind-set figures are replaced.
- The GitHub Action stays rules-only: it has no Claude input, so no key is read in a workflow
  (`docs/action.md`).

### Fixed

- Evidence is unique per (file, line, signal): when two rules cited the same line (for example `disclosure` and the weak `template-ai-prompt`), the report listed it twice. One item stays, the one with the highest confidence, and `evidence (N)` counts the deduplicated list in every format.
- A pull request template line about not requesting an automated AI review is no longer weak disclosure evidence.
- A prohibition that only forbids a misuse ("as a substitute for understanding", "instead of reviewing", "without reviewing", "blindly") is a qualified restriction, not a ban; so is a ban in a document that also says AI is "usually fine" (for example `LadybirdBrowser/ladybird` `Documentation/CodePolicy.md`: `restricted`, low confidence, was `banned`).

## [0.2.0] - 2026-11-22

First public release: a command line tool and a composite GitHub Action that find a repository's AI
contribution policy, link each cited line at a commit-pinned URL, and answer `needs-review` when the text
cannot be decided on credibly.

### Report schema version 2: abstention and confidence

- `schema_version` is `"2"` (version 1 was never published). Early blind testing showed two dominant failures:
  narrow or qualified prohibitions and generic accountability sentences read as hard verdicts, and
  `no-policy-found` for repositories whose policy is written in soft language. The tool now abstains instead of
  guessing and reports how sure it is. The product is the located, linked policy text; the class is a hint.
- New verdict `needs-review` (between `disclosure-required` and `permitted`). It is returned when the files hold
  AI-policy text (an AI term and a policy verb in one paragraph) that no rule decided on
  (`review_reason: "no-rule-decision"`, the evidence is the paragraphs, signal `needs-review`), or when signals
  conflict in a way precedence cannot resolve credibly (`"conflicting-signals"`: a welcome next to a full ban,
  or a restriction that exists only as a demoted ban next to a weaker signal). `no-policy-found` is returned
  only when no such paragraph exists. Adding an enum value breaks a strict consumer, hence the version bump;
  the migration table is in `docs/schema.md`.
- New required fields: `confidence` (`high` or `low`) on the report and on every evidence item, and
  `review_reason` (`null` unless `needs-review`). A verdict is `low` when it rests on one sentence, on a rule
  marked `"weak": true` in `rules/patterns.json`, or on a demoted ban.
- A ban sentence with a narrowing qualifier (`raw`, `unverified`, `unreviewed`, `fully`, `entirely`, `largely`,
  `wall of`, `to disguise`, `without review`, `mass`, `low-effort`, `unless`, ...) is no longer `banned`: it is
  `restricted` with `low` confidence. The qualifiers are one shared list, `narrowing_qualifiers` in
  `rules/patterns.json` (rules 4.0); the rule keys `weak` and `demote_on` / `demote_to` are new.
- Exit code 12 for `needs-review` and `--fail-on needs-review` (also a new `fail-on` value of the GitHub Action).
  Like `disclosure-required`, `needs-review` exits 0 unless the gate asks for it.
- Text, markdown (a "Needs human review" section listing the paragraphs) and SARIF (rule `needs-review`, level
  `warning`, `confidence` properties) renderers know the new verdict and the confidence.
- `classify_documents(documents, patterns=None, owner=None)`: `owner` lets a paragraph that only links into the
  owner's `.github` repository count as a pointer. The classification adds `confidence`, `review`,
  `review_reason`; evidence items add `confidence`, `sentence`, `demoted`.
- `tests/golden/run_gold.py` reports (a) decided-row accuracy (needs-review is a correct abstention), (b) abstain
  rate and (c) dangerous errors (a labelled policy reported as `no-policy-found`) next to the overall figure,
  which counts `needs-review` as wrong.
- Development-set numbers, optimistic (151 rows, every one seen while tuning): rules 3.1 before: 90.7%
  overall, 5 dangerous errors, no abstention. Rules 4.0: 93.0% on decided rows (132 of 142), abstain rate 6.0%
  (9 of 151), 2 dangerous errors, 87.4% overall with an abstention counted as wrong. Blind figures are in
  Accuracy below.
- `docs/examples/pymeasure.*` are regenerated in the new format.

### Added

- Command line tool `oss-ai-policy-check OWNER/REPO` with `--ref`, `--format`, `--path`, `--fail-on`,
  `--no-cache` and `--no-org`.
- GitHub transport without the `gh` CLI: anonymous REST API plus `raw.githubusercontent.com`; `GITHUB_TOKEN`
  or `GH_TOKEN` is optional and raises the rate limit; `gh` is only a fallback on rate limit or network error.
- On-disk cache keyed by repository and commit SHA (`%LOCALAPPDATA%/oss-ai-policy-check/cache`, or
  `$XDG_CACHE_HOME` / `~/.cache`); `--no-cache` turns it off.
- The owner's `.github` repository is read when the repository itself has no AI policy statement (no file
  named like an AI policy, and no paragraph with both an AI term and a policy verb); those files appear as
  `org:.github/<path>`. The repository text wins when both exist. New nullable report field `org_commit_sha`;
  evidence permalinks for such files point to `OWNER/.github` at that SHA (`url` is `null` if it is unknown).
- `AI-USE.md` / `AI_USE.md` (any case, root, `.github/` or docs directories) are policy candidate files.
- Wider file discovery. A file whose name has an AI word (`ai`, `llm`, `llms`, `genai`, `generative`) as a
  whole segment is read when it sits in the repository root, `.github/`, `docs/`, `doc/`, `documentation/`,
  `policies/` or below a `contributing/` directory (at most four directories deep, text extensions only;
  examples, tests, vendored code and language directories are skipped), and so is every
  `CONTRIBUTING.<x>.md` sibling (`CONTRIBUTING.ai.md`; a bare language code is a translation). Names such as
  `email`, `detail`, `rails` or `openai-sdk` do not match. At most 8 such files and AI-context links are read
  per repository, the most policy-like name first; the rest are named in a warning. The large-repository
  fallback and `--path` also list top-level `policies/`, `policy/` and `contributing/` directories.
- A same-repository link is followed (one level, from contribution and agent files only) when the link text or
  its target name says AI, or when it sits under an AI heading and reads like a policy. This is why
  `[AI.md](AI.md)` in a contribution guide was not followed before: the target name alone did not look like a
  policy.
- Report contract with a JSON Schema in `schema/report.schema.json` (schema version 2, see above).
- Six verdicts (`banned`, `restricted`, `disclosure-required`, `needs-review`, `permitted`, `no-policy-found`) and a
  separate `complete` flag that keeps "could not read" apart from "no policy found".
- Evidence lines with permalinks pinned to a commit SHA and quotes of at most 15 words.
- Regex rules in `rules/patterns.json`, adapted in part from `beduldul/ai-policy` (see
  `THIRD-PARTY-NOTICES.md`).
- Output formats: text, JSON, markdown (for job summaries and pull request comments) and SARIF 2.1.0.
- A composite GitHub Action (`action.yml`), documented in `docs/action.md`.
- Documentation: README, how it works, limitations, contributing guide, security policy and
  `AI-USE.md`.

### Rules and classifier

- Rules 3.1 (18 to 12 rules): the rules were generalized, not extended; a ban now needs a scope-wide
  prohibition, and conditional closures, voice guidance and autocompletion-only carve-outs read as
  `restricted`. Rule table in `docs/how-it-works.md`.
- Classifier rules rewritten (`rules/patterns.json` version 3.0): sentence-level matching with negation
  ("we do not ban AI", "no need to disclose"), conditions, qualifiers, narrow targets (descriptions,
  comments, media), pull request template rules, agent-file rules and an explicit `permitted` rule.
- Fenced code blocks, reStructuredText literal blocks and "Example" sections are ignored; list items are
  separate sentences. `user-agent` and `ssh agent` no longer count as AI.
- Candidate files: `AI_COVENANT.md` and similar AI policy names are read; `.github` automation, agent
  skills, `CODEOWNERS` and licence listings are not.

### Fixed during development

Bugs found while building the release, listed because they changed which files are read:

- A recursive tree reply over the 16 MB read limit (`llvm/llvm-project`, `rust-lang/rust`,
  `mozilla-firefox/firefox`, `NixOS/nixpkgs`) no longer ends in `repository discovery failed (invalid API
  JSON)`: it is detected (`TooLargeError`), never parsed, and the root, `.github` and docs trees are read
  instead (3 to 5 API requests).
- The policy text of five labelled repositories was never fetched (an `AI.md` reached through a link, a
  `CONTRIBUTING.ai.md`, a `policies/` page, a page under `docs/contributing/` and an `.rst` page under
  `doc/src/contributing/`); all five are now read.
- The organization `.github` repository was skipped whenever the repository text mentioned AI at all, and
  its `CONTRIBUTING` whenever the repository had a contributing file, which lost six of the seven labelled
  organization-policy repositories. The gate is now "the repository has no AI policy statement"; a paragraph
  that only links to the owner's `.github` policy does not count as one. The organization's `CONTRIBUTING`
  and AI policy are read even when the repository has its own `CONTRIBUTING`.

### Accuracy

Blind results for this release (rules 4.0, commit de7caa1, 2026-11-22). Both sets are repositories the rules
had not seen. Labels come from a single labeler and are pinned to commit SHAs.

| blind set | repositories | decided-row accuracy | abstain rate | "no policy" when one exists |
|---|---|---|---|---|
| A | 60 | 74.0% (37 of 50) | 16.7% (10 of 60) | 0 of 60 |
| B (about half deliberately hard cases) | 60 | 68.9% (31 of 45) | 25.0% (15 of 60) | 1 of 60 |

Set A was viewed before the `needs-review` change, so set B is the cleaner figure; counting an abstention as
wrong, set B's overall exact verdict is 51.7% (31 of 60). Positioning follows from these numbers: the tool is a
policy finder with pinned evidence and an honest abstention, and its class verdict is a hint with a confidence
level.

Development set, optimistic: 151 labelled repositories (gold date 2026-11-22, labels pinned to the SHAs in
`tests/golden/gold.jsonl`; the tool is always run at that SHA, never at HEAD). Every row was read while the
rules were tuned, so these figures say how well the rules fit this set, not how well they predict new ones.
Rules 4.0 reach 93.0% on the rows they decide (132 of 142), abstain on 6.0% (9 of 151) and have 2 dangerous
errors; counting an abstention as wrong the overall figure is 87.4% (132 of 151). Earlier rule sets scored 38.6%
(initial rules, 101 development rows), 84.2% (rules 3.0, same rows) and 90.7% (rules 3.1 with the wider file
discovery, all 151 rows). Reproduce with `python tests/golden/run_gold.py --split all --summary` (network on the
first run; responses are cached under `tests/golden/_cache`, then `--offline` reproduces the numbers).

### Known gaps

- The classifier is regex based and English only; the class verdict is a hint, and about one repository in six to
  four comes back as `needs-review` (see Accuracy).
- External policy websites are not scanned, so labels that rest on them are missed.
- Reading a policy file is not understanding it: three labelled policies that are now fetched (an agent rule
  beside a disclosure rule, a pull-request rejection read as a ban, a same-standards note without a welcome
  sentence) are still classified differently from their labels.

### Security hardening (found in the pre-release review)

- The action runs `python -P` with `PYTHONPATH` at its own `src`, so a pull request that adds
  `oss_ai_policy_check/__init__.py` to the workspace cannot run code in the step that holds the token. The
  `pip install` of the action path (and its unpinned build backend download) is gone.
- The action prints the text report between `::stop-commands::<random token>` and `::<token>::`; the text report
  never starts a line with a file name or quote, and a leading `::` in any untrusted string is defused.
- The action step fails with exit code 3 whenever the read was incomplete and nothing restrictive was found, also
  when `fail-on` is empty (before, a green check could stand for a policy that was never read).
- Quotes are also capped at 300 characters (a single huge "word" used to pass the 15-word cap); this covers
  `unclassified_ai_lines` too, and the JSON schema has `maxLength: 300` for `quote`.
- Bidirectional controls (U+202A-202E, U+2066-2069, U+200E/F, U+061C) and C1 controls are removed from every
  format, and the JSON renderer cleans quotes like the others.
- `OWNER` and `REPO` of `.` or `..` are rejected.

### Packaging

- The wheel ships `rules/patterns.json` and the report schema (`oss_ai_policy_check/schema/report.schema.json`).
- A tag `v*` runs `.github/workflows/release.yml`: tests, build, then publish to PyPI through Trusted
  Publishing (no token). Steps for the maintainer are in `docs/release.md`.
- The GitHub Action runs straight from its own checkout (no install, no download), so `uses: ...@v0.2.0` runs
  the v0.2.0 code.

[0.3.0]: https://github.com/KemalG-u/oss-ai-policy-check/releases/tag/v0.3.0
[0.2.0]: https://github.com/KemalG-u/oss-ai-policy-check/releases/tag/v0.2.0
