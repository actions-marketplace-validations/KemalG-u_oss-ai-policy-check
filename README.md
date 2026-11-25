> **Status: active, v0.3.1 (pre-1.0).** Interfaces may change.

# oss-ai-policy-check

[![CI](https://github.com/KemalG-u/oss-ai-policy-check/actions/workflows/ci.yml/badge.svg)](https://github.com/KemalG-u/oss-ai-policy-check/actions/workflows/ci.yml) [![PyPI](https://img.shields.io/pypi/v/oss-ai-policy-check)](https://pypi.org/project/oss-ai-policy-check/) [![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/KemalG-u/oss-ai-policy-check/badge)](https://scorecard.dev/viewer/?uri=github.com/KemalG-u/oss-ai-policy-check)

Finds a repository's AI contribution policy and links every line it cites at a fixed commit, so you can check
the text yourself. When it cannot decide what the policy says, it answers `needs-review` instead of guessing.

The strength of the tool is locating the policy text and pointing at the exact lines. The class it puts on top
(`banned`, `restricted`, `disclosure-required`, ...) is a hint with a confidence level, not a ruling: read the
cited lines. Contributors and coding agents get the rules before they open a pull request; maintainers can test
that a machine finds the policy they wrote.

## Quick start

Python 3.10 or newer, no dependencies, no login. The optional Claude mode adds one extra package and your own key
(see [Claude mode](#claude-mode-optional)).

```
uvx oss-ai-policy-check owner/repo
pip install oss-ai-policy-check && oss-ai-policy-check owner/repo
```

`pipx run oss-ai-policy-check owner/repo` works too. As a GitHub Action (pinned to a release tag; for the
strictest pinning use the full commit SHA of the tag, see [docs/action.md](docs/action.md)):

```yaml
permissions:
  contents: read

steps:
  - id: policy
    uses: KemalG-u/oss-ai-policy-check@v0.3.1
    with:
      repo: owner/repo        # default: the repository running the workflow
      fail-on: banned         # empty means report only
```

From a clone of this repository:

```
python -m venv .venv
.venv/bin/python -m pip install -e .        # Windows: .venv/Scripts/python
.venv/bin/oss-ai-policy-check owner/repo
```

Options: `--ref REF` (branch, tag or SHA), `--path DIR` (read a local clone of `owner/repo` instead of GitHub),
`--format text|json|markdown|sarif`, `--fail-on banned|restricted|disclosure-required|needs-review`,
`--no-cache` (skip the disk cache), `--no-org` (do not read the owner's `.github` repository),
`--classifier rules|claude` and `--model ID` (optional Claude mode, below), `--version`.
Fetched files are cached on disk by repository and commit SHA (`%LOCALAPPDATA%\oss-ai-policy-check\cache` on
Windows, `$XDG_CACHE_HOME` or `~/.cache` elsewhere).

## Example output

Abridged output of a real run on 2026-11-22. Repositories change their policies; the SHA shows what was read.

```
VERDICT restricted - ghostty-org/ghostty @ 6d244209978e599277904ff3ced7b8a2ebcc528d (6d244209978e)
confidence: high
read status: complete within scope
evidence (12):
  - restricted (high): AI_POLICY.md:21 - - **No AI-generated media is allowed (art, images, videos, audio, etc.).**
    https://github.com/ghostty-org/ghostty/blob/6d244209978e599277904ff3ced7b8a2ebcc528d/AI_POLICY.md#L21
  - disclosure-required (high): AI_POLICY.md:5 - - **All AI usage in any form must be disclosed.** You must state
    https://github.com/ghostty-org/ghostty/blob/6d244209978e599277904ff3ced7b8a2ebcc528d/AI_POLICY.md#L5
  ... (10 more)
AI mentions without a classified rule (10):
  - ... (listed in full, not hidden)
```

When the files hold AI-policy text the tool cannot decide on, it says so instead of guessing (abridged):

```
VERDICT needs-review - mozilla-firefox/firefox @ a3178f3cbfac6e25d5bc7b6365c4ff520a42205e (a3178f3cbfac)
confidence: low
NEEDS HUMAN REVIEW (no-rule-decision): These paragraphs name AI and state a rule,
  but no rule of the tool decided on them.
evidence (4):
  - needs-review (low): docs/contributing/ai-coding.md:13 - AI-assisted contributions must meet the same standards ...
    https://github.com/mozilla-firefox/firefox/blob/a3178f3cbfac6e25d5bc7b6365c4ff520a42205e/docs/contributing/ai-coding.md#L13
  ... (3 more)
```

## Verdicts

One verdict per report, strongest signal first. Treat it as a hint: `confidence` (`high` or `low`) says how
sure the tool is about what it read, and `complete` is a separate flag.

| verdict | meaning |
|---|---|
| `banned` | a quoted line says AI-assisted work is refused |
| `restricted` | AI use is conditional: a human in the loop, own-voice descriptions, limits on scope |
| `disclosure-required` | AI use has to be disclosed or labelled |
| `needs-review` | the tool abstains: the files hold AI-policy text it cannot decide on credibly (no rule matched it, or the signals conflict, for example a welcome next to a ban). A person must read the cited paragraphs; this is not permission |
| `permitted` | explicit, positive evidence only, never inferred from silence (emitted for an explicit welcome statement such as "AI-assisted contributions are welcome") |
| `no-policy-found` | no AI-policy text in the scanned scope; this is not permission |

The tool abstains instead of guessing: `no-policy-found` is returned only when no paragraph pairs an AI term
with a policy verb. A ban sentence that carries a narrowing qualifier ("unreviewed", "fully", "unless") is
reported as `restricted`, not `banned`, and marked `low` confidence; so is a verdict that rests on one
sentence or on a loose heuristic rule. The order of precedence is in [docs/schema.md](docs/schema.md#decision-order).

`complete` is `false` when a candidate file could not be read, a tree was truncated, the file limit was
hit, or repository discovery failed.

**"No policy found" and "could not read" are different answers.** A file that does not exist is recorded
as `missing`: nothing is there. A file that exists but failed to load is recorded as `error`: unknown.
When `complete` is `false` and nothing restrictive was found, the tool exits with code 3 and says no
decision is possible, because a restriction may sit in the file it could not read. Printing "no policy"
in that case would tell a contributor that AI use is fine when the tool simply did not see the text.

## Exit codes

| code | meaning |
|---|---|
| 0 | `permitted`, `no-policy-found`, `disclosure-required` or `needs-review` |
| 10 | `restricted` |
| 20 | `banned` |
| 11 | `disclosure-required`, only with `--fail-on disclosure-required` or `needs-review` |
| 12 | `needs-review`, only with `--fail-on needs-review` |
| 3 | incomplete and undecided: `complete` is `false` and nothing restrictive was found |
| 2 | usage error |

`--fail-on V` exits non-zero only when the verdict is at least as strict as `V`. A `banned` or `restricted`
verdict stands even when `complete` is `false`. The JSON report is `schema_version` "2" (it adds the
`needs-review` verdict, `confidence` and `review_reason`; migration notes are in [docs/schema.md](docs/schema.md#migration-from-schema_version-1)).

## Claude mode (optional)

Off by default. The default classifier is deterministic: fixed rules, no key, no network beyond GitHub. Claude mode
is an opt-in second reader for the paragraphs the rules find hard:

```
pip install 'oss-ai-policy-check[claude]'
export ANTHROPIC_API_KEY=...
oss-ai-policy-check owner/repo --classifier claude [--model ID]
```

It needs your own Anthropic credentials; the tool never reads, prints or logs a key. Only the AI-policy paragraphs
of the fetched files are sent to the Anthropic API under your account. Every evidence line and link is still built
from the fetched files, never from the model's text, and a failed call keeps the deterministic verdict. How the two
verdicts are combined, what is sent, cost and failure behaviour: [docs/llm-mode.md](docs/llm-mode.md). Measured
effect: [Measured accuracy](#measured-accuracy).

## GitHub Action

The action reads a repository's policy, writes the report to the job summary and returns the verdict as an
output (example in the quick start above). It runs the deterministic rules only; Claude mode is not available in
the action. Inputs, outputs, permissions and more examples are in [docs/action.md](docs/action.md).

## Measured accuracy

Both modes were run once on a 60-repository set that had never been used to write or tune a rule or the prompt
(called gold4 here). Labels are one person's reading of the written rubric (`tests/golden/RUBRIC.md`), pinned to
commit SHAs. "Correct" means the verdict equals the label; a `needs-review` answer counts as not correct.
The gold4 label file is a separate blind set and is not part of this repository, so these two rows cannot be rerun
from here. The same runner is included: `python tests/golden/run_gold.py --split all --summary` (add
`--classifier claude` for Claude mode) runs the published 151-row development set `tests/golden/gold.jsonl`,
whose figures are optimistic because the rules were tuned on it (see [docs/limitations.md](docs/limitations.md)).

| | correct | decided rows correct | `needs-review` | policy missed | Wilson 95% interval (correct) |
|---|---|---|---|---|---|
| Rules only (default) | 42 of 60 (70.0%) | 42 of 53 | 7 | 2 | 57% - 80% |
| Claude mode (`--classifier claude`) | 55 of 60 (91.7%) | 55 of 56 | 4 | 1 | 82% - 96% |

"Decided rows" are the rows where the tool gave a class instead of abstaining. "Policy missed" is a repository
that has an AI policy but got `no-policy-found`.

When the rows were measured: the rules row was first run on 2026-11-22 and run again on 2026-11-24 with v0.3.0 and on 2026-11-25 with the rules of this repository after the
`agent-file-no-posting` fix and again the same day after the agent-instruction-file change, each from the pinned SHAs, with the same 42 of 60 (v0.2.0 gives the same verdict on every one of the 60 rows). The Claude mode
row was measured once with v0.3.0 on 2026-11-22; it has not been re-run.

Caveats, all of which matter:

- n = 60 and a single run. The two intervals do not overlap, but a different 60 repositories or a second
  run (the model's output is not deterministic) would move the figures.
- The judge was Claude Sonnet 5.5 through a subscription, standing in for the API model. The default model of
  Claude mode is `claude-opus-5-5`, which was not the model measured.
- The labels were written by one annotator.
- A label is a reading of the policy text at one commit; the label itself can be wrong or arguable at the
  `restricted` / `disclosure-required` boundary.

What this means in practice: the evidence lines and links are exact and are the product; the class is a hint with
a confidence level. In this run Claude mode gave fewer wrong decided verdicts (1 against 11), fewer abstentions
and fewer missed policies, at the price of a key, a network call per repository and non-deterministic output
([docs/llm-mode.md](docs/llm-mode.md)). Older figures on the 151-repository development set, which was read while
tuning the rules, are optimistic and kept only as history in
[docs/limitations.md](docs/limitations.md#measured-accuracy).

## How it works and its limits

- [How it works](docs/how-it-works.md): which files are read and how the verdict is chosen.
- [Limitations](docs/limitations.md): known gaps and how to report a wrong classification.

The classifier is regex based and can be wrong in both directions. A verdict is not legal advice and not
permission to contribute with AI. The text of the policy itself is what counts: follow the linked lines, not
the label.

## Privacy

No token is needed. In the default mode the tool makes read-only GET requests for public files to GitHub's REST API
(`api.github.com`) and to `raw.githubusercontent.com`, and sends nothing else anywhere: the repository name,
branch and file paths are the only data in those requests. It does not clone the repository, run any of its code,
or write to GitHub. An optional `GITHUB_TOKEN` or `GH_TOKEN` only raises the API rate limit and goes to those two
GitHub hosts only; the `gh` CLI is used only as a fallback when the REST API is rate limited or unreachable. No
analytics, no telemetry, and the report never includes local paths. The only thing written locally is the
commit-keyed file cache (`--no-cache` turns it off). Quotes are cut at 15 words and 300 characters.

With `--classifier claude` the AI-policy paragraphs of the fetched public files are also sent to the Anthropic API
under your account, with your credentials; nothing else is sent.

## Related projects

- [beduldul/ai-policy](https://github.com/beduldul/ai-policy) (Go, MIT): a checker for the same question.
  The discovery and line-evidence design here is adapted from it; see [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).
- [melissawm/open-source-ai-contribution-policies](https://github.com/melissawm/open-source-ai-contribution-policies):
  a list of policies that projects have written. A list, not a checker.
- [ai-policy.dev](https://ai-policy.dev): ready-made policy texts that maintainers can copy. Useful if you
  need to write a policy; this tool can then check that it is found.

## Project

[AI-USE.md](AI-USE.md) says how this repository is produced. Contributions: [CONTRIBUTING.md](CONTRIBUTING.md); conduct: [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
Security reports: [SECURITY.md](SECURITY.md). Changes: [CHANGELOG.md](CHANGELOG.md). License: MIT, see [LICENSE](LICENSE).
