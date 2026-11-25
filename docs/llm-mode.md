# Claude mode (`--classifier claude`)

The default classifier is deterministic: fixed rules, no key, no network beyond GitHub, the same answer every run.
Claude mode is an optional second reader for the paragraphs the rules find hard. It is off unless you ask for it.

```
pip install 'oss-ai-policy-check[claude]'
export ANTHROPIC_API_KEY=...            # or sign in with `ant auth login`; the SDK finds the credentials
oss-ai-policy-check OWNER/REPO --classifier claude [--model claude-opus-5-5]
```

Without the extra the command exits with code 2 and `install oss-ai-policy-check[claude]`. The base package stays
stdlib-only and never imports the SDK unless `--classifier claude` is given. This tool never reads, prints or logs a key.

## What happens

1. Fetching and file discovery are exactly as in the default mode (same files, same commit pin, same `complete` rules).
2. The deterministic classifier runs as usual. Its result is the fallback and the "second opinion" (see below).
3. Only the AI-policy paragraphs are sent to the model: paragraphs that pair an AI term with a policy verb, plus every
   paragraph where a rule fired, outside code blocks and "Example" sections. A heading that names AI carries its body:
   the lines below it up to the next heading of the same or a higher level (at most 60 lines and 1800 characters,
   cut at a line boundary) are one paragraph, so the rules written under "# AI agent directives" reach the model and
   a heading is never sent alone (a heading with nothing below it is dropped). Each one carries an id of the form
   `file:line`. At most 40 paragraphs are sent (rule-flagged ones first); if more exist, a warning says so and
   `complete` is `false`. Nothing else from the repository is sent. These are public repository texts, and they go to
   the Anthropic API under your account.
4. The model returns a verdict, a confidence (`high` or `low`), the ids of the deciding paragraphs and a one-sentence
   reason. It returns no quotation and no URL.
5. The tool builds `evidence` from the ids: `file`, `line`, the quote (the literal first 15 words of the anchor line)
   and the SHA-pinned permalink all come from the fetched files and `contract.build_report`. The model cannot invent
   a link or a quote. The anchor line is the line where the AI term first appears in the paragraph, which is not
   always the deciding sentence: open the link.

## Checks on the answer

- An id that was not sent (the model made it up, or the repository text forged it) discards the verdict:
  the report says `needs-review` (`no-rule-decision`, confidence `low`) with a warning, and lists the sent paragraphs.
- A decided verdict (`banned`, `restricted`, `disclosure-required`, `permitted`) with no deciding id is discarded the
  same way.
- `needs-review` is always confidence `low`, as in the default mode.
- Paragraph text is untrusted. It is escaped (`<` becomes `&lt;`) so it cannot close the fence, it sits inside
  `<paragraph>` tags, and the system prompt says to ignore instructions inside it. A prompt injection can at most
  mislead the verdict; it cannot add evidence that is not in the fetched files.
- When the model's verdict differs from the deterministic one, a warning names the deterministic verdict. The model
  does not override evidence silently.

## How the verdicts are combined

The model answer is not taken as is. After it passes the checks above, `classify.combine` joins it with the
deterministic result by a fixed table (no further call). The first row that applies wins. "Rules signal" means the
rules say `banned`, `restricted` or `disclosure-required`; "quiet" means `permitted` or `no-policy-found`.

| # | Model verdict and confidence | Rules verdict | Result |
|---|---|---|---|
| 1 | `needs-review` (any) | any | model result (`needs-review`, `no-rule-decision`) |
| 2 | same verdict as the rules | same | model result, model confidence |
| 3 | quiet (`permitted`, `no-policy-found`), any confidence | rules signal | `needs-review`, `llm-rules-disagree` |
| 4 | any other verdict, `low` | any other verdict | `needs-review`, `llm-low-confidence` |
| 5 | `banned` or `restricted`, `high` | quiet | model verdict, confidence `high` |
| 6 | any decided verdict, `high` | `needs-review` | model verdict, confidence `high` |
| 7 | any other verdict, `high` | any decided verdict, rules confidence `high` | model verdict, confidence lowered to `low` |
| 8 | any other verdict, `high` | any decided verdict, rules confidence `low` | model verdict, confidence `high` |

Why: the measured failure was a model that reads fewer lines than the rules (row 3: a quiet answer against a rule hit
is the dangerous direction, so a person reads it), and a model that guessed on thin text (row 4). A confident
`banned` or `restricted` against quiet rules is where the model adds the most (row 5), so it stands. A disagreement
between two confident readers (row 7) keeps the model's verdict but is marked `low`, which is the signal to open the
cited lines. Every disagreement adds a warning that names both verdicts.

The two new `review_reason` values, `llm-low-confidence` and `llm-rules-disagree`, are written only in Claude mode.
The report lists the model's deciding lines and the rules' evidence lines (each with its own signal, a line once),
or the paragraphs to read when neither side cites a line. A `needs-review` report exits with code `0` unless
`--fail-on needs-review` is set, like any other abstention.

## Failures never crash

A refusal (`stop_reason: refusal`, checked before the content is read), a rate limit, an API or connection error, a
missing credential, a cut-off or malformed answer: the report keeps the deterministic verdict, sets `complete: false`,
and adds a `claude classifier failed: ...` warning (HTTP status and error type only, never the response body). In that
case `classifier.kind` is `rules`, because the rules produced the verdict. With nothing but a `permitted` or
`no-policy-found` verdict the exit code is `3` (incomplete), as for any unreadable file.

If no AI-policy paragraph exists, no call is made and the verdict is `no-policy-found` (or the rules' result);
`classifier.kind` is `claude` and the usage line is not printed.

## Request details

- Model: `claude-opus-5-5` by default, `--model ID` to change. The server-side refusal fallback
  (`betas=["server-side-fallback-2026-07-01"]`, `fallbacks="default"`) is sent for `claude-opus-5*` and `claude-fable-5*`
  models only; other models (for example Haiku) do not support it and get a plain request.
- `output_config={"effort": "low", "format": json_schema}`: a classification, not a research task. No `temperature`,
  `top_p`, `budget_tokens` or `thinking` setting is sent; current models reject them. The system prompt (the labeling
  rubric, condensed) is marked for prompt caching.
- `max_tokens` is 4000: thinking is always on for Opus 5.5 and counts against it, and 2000 risks a cut-off answer. Only
  the tokens actually generated are billed.
- Output is not deterministic. Two runs can differ on a borderline text; the SHA-pinned evidence is what you verify.

## Cost

Estimates from a character count (chars / 4), not a tokenizer. Measured on the 151 gold repositories (cached files,
no API call): 129 have AI-policy paragraphs, the system prompt is about 970 tokens, the paragraph part has a median
of about 580 and a mean of about 800 tokens (90th percentile about 1,600, largest about 4,800).

| | input tokens | output tokens | Opus 5.5 ($4 in / $20 out per MTok) |
|---|---|---|---|
| typical repository (median) | ~1,500 | 150-1,000 | $0.009 - $0.026 |
| mean repository | ~1,800 | 150-1,000 | $0.010 - $0.027 |
| whole gold set, cold run (129 calls) | ~230,000 | 20,000-130,000 | about $1.3 - $3.5 |

The low end of the output range is the visible answer only (about 150 tokens); the high end allows about 850 tokens of
always-on thinking at low effort. The command prints the real token counts and a list-price estimate on stderr after a
run. The system-prompt cache discount is not counted, so real cost is slightly lower.

## Measured accuracy

Both classifiers were run once on gold4, a set of 60 repositories that had never been used to write or tune a rule or
the prompt. Labels are one annotator's reading of `tests/golden/RUBRIC.md`, pinned to commit SHAs. "Correct" means the
verdict equals the label; `needs-review` counts as not correct.

| | correct | decided rows correct | `needs-review` | policy missed | Wilson 95% interval (correct) |
|---|---|---|---|---|---|
| Rules only (default) | 42 of 60 (70.0%) | 42 of 53 | 7 | 2 | 57% - 80% |
| Claude mode | 55 of 60 (91.7%) | 55 of 56 | 4 | 1 | 82% - 96% |

"Policy missed" is a repository that has an AI policy but got `no-policy-found`. The rules row was run on 2026-11-22
and again on 2026-11-24 with v0.3.0 and on 2026-11-25 with the `agent-file-no-posting` fix (same 42 of 60); the Claude mode row was measured once with v0.3.0 on 2026-11-22
and has not been re-run. Caveats:

- n = 60 and a single run; the model's output is not deterministic, so a second run or another 60 repositories would
  move the figures. The intervals do not overlap, which is evidence of a difference on this set, not a guarantee.
- The judge was Claude Sonnet 5.5 through a subscription, standing in for the API model. The default model
  (`claude-opus-5-5`) was not the one measured.
- The labels come from one annotator, and the `restricted` / `disclosure-required` boundary is a judgement call.
- Earlier figures on the 151-repository gold set were produced while the rules and the prompt were being tuned on
  those rows; they are optimistic and are not blind results.

## Measuring it on the gold set

```
python tests/golden/run_gold.py --classifier claude --split dev
python tests/golden/run_gold.py --classifier claude --split dev --llm-cache-only   # rerun, no API call at all
```

Same pipeline as the command line (fetch at the pinned SHA, classify, build the report). Every model response is
cached under `tests/golden/_llm_cache/<model>/<sha256 of system and user prompt>.json`, so a rerun with an unchanged
prompt costs nothing; only finished answers are cached. The run ends with token totals, the cost of a cold run and what
this run was billed. A change to the system prompt or the paragraph selection changes the key and re-asks.
