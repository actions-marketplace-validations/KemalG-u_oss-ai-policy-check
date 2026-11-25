# How it works

The tool answers one question: what does this repository say about AI contributions, and where exactly.
It reads files, matches lines against a small set of rules and reports what it found with the line number
and a permalink. It does not run repository code and does not clone the repository.

## 1. Pin a commit

For a GitHub repository the tool resolves the branch (or the `--ref` you gave) to a commit SHA once, and
reads every file at that SHA. Each piece of evidence carries a permalink of the form
`https://github.com/owner/name/blob/<sha>/<path>#L<line>`. The link shows the text the tool saw, even if
the branch moves later.

With `--path` the SHA is read from `.git/HEAD` of the local clone without running `git`. If the directory
is not a git clone, the SHA is unknown and `evidence[].url` is `null`.

## 2. Choose the files

A file is a candidate when it is a text file (no extension, `.md`, `.markdown`, `.rst`, `.txt`, `.adoc`)
and one of these holds. Names are compared without case.

- Its name suggests an AI policy: a name containing `ai`/`llm` followed by `policy`, `usage`, `rules`,
  `tool`, `covenant`, `contribution(s)`, `guidelines`, `assistance`, `disclosure`, `statement`, `etiquette`
  or `ethics` (for example `AI_POLICY.md`, `ai-usage.md`, `AI_COVENANT.md`), `AI-USE` or `AI_USE`,
  `code-provenance`, or `generated-content`.
- It is one of the common contribution and agent files: `CONTRIBUTING`, `CONTRIBUTE`, `AGENTS`,
  `AGENT_POLICY`, `CODE_OF_CONDUCT`, `PULL_REQUEST_TEMPLATE`, `CLAUDE`, `.cursorrules`.
- Its name has an AI word as a whole segment: `ai`, `llm`, `llms`, `genai` or `generative`, split on anything
  that is not a letter or digit (`AI.md`, `ai-coding.md`, `ai-generated-code-policy.rst`, `ai_use.txt`,
  `CONTRIBUTING.ai.md`). `email`, `detail`, `rails` and `openai-sdk` do not match. Such a file counts only
  in the repository root, `.github/`, `docs/`, `doc/`, `documentation/`, `policies/` or below a
  `contributing/` directory, at most four directories deep, with a `.md`, `.markdown`, `.rst`, `.txt` or
  `.adoc` extension; copies and translations (`examples/`, `tests/`, `node_modules/`, `de/`, `zh-hant/`, ...)
  are skipped.
- It is a `CONTRIBUTING.<something>.md` sibling of the contribution guide (`CONTRIBUTING.template.md`); a bare
  language code (`CONTRIBUTING.de.md`) is a translation and is skipped.
- It is the `README` in the repository root.
- It is any text file under `.github/`, except automation and agent-skill noise: files under
  `workflows/`, `actions/`, `agents/`, `skills/`, `prompts/`, `instructions/`, `codeql/`, `scripts/` or
  `linters/`, and `CODEOWNERS`, `FUNDING` and licence listings. A file in such a directory is still read
  when its own name marks it as a policy (`.github/agents/AI_POLICY.md`). Issue and pull request template
  files in YAML are included when their path contains `template`.

Searched locations are the repository root and the trees of `.github`, `docs`, `doc` and `documentation`
(any depth). `llvm/docs` is a built-in special case for one large monorepo. AI-named files are also taken from
`policies/` and `contributing/` directories. A file whose name contains `policy` or `policies` (for example
`Documentation/CodePolicy.md`) is read as well, after the AI-named files and at most 3 per repository, unless the
name is about security, privacy, licences, releases and the like.

Links count too: when a found contribution or agent file links to another file in the same repository, that
file is read as well, one level deep, if it is a candidate by name or if the link itself is about AI: the link
text or the target name says AI (`[AI.md](AI.md)`), or the link sits under a heading that mentions AI and its
text or target name reads like a policy (`[usage policy](docs/guide.md)`). Neighbouring prose does not count,
and files found through such a link do not lead further. A link to a rendered documentation page is matched to
a source file with the same name.

There are three caps. At most 200 files are read, and a file over 1 MB is not read. Beyond the name-based
candidates, at most 8 AI-named files and AI-context links are read per repository, the most policy-like name
first (a name with a policy word such as `ai-coding` before a bare `ai`, root and `.github` before deep
`docs` paths); the ones left out are named in a warning. Hitting any cap, or a truncated tree, adds a warning
and sets `complete` to `false`.

## 3. Organization `.github` repository

Organizations often keep default community files in a repository named `.github`. GitHub shows those files
as defaults for member repositories that lack their own. The tool reads the owner's `.github` repository
whenever the repository itself has no AI policy statement. The rule, exactly:

- A repository states an AI policy when one of its read files is named like an AI policy (`AI_POLICY.md`,
  `AI-USE.md` and the other names above), or when one paragraph of any read file holds both an AI term
  (`AI`, `LLM`, `generative`, `Copilot`, `ChatGPT`, `Claude`, `agent-generated`, `machine-generated` and
  similar) and a policy verb (`must`, `require`, `disclose`, `ban`, `prohibit`, `forbid`, `accept`,
  `reject`, `allowed`, `not allowed`, `do not`, `never` and similar). A bare mention such as "AI features"
  in a README does not count, and neither does a paragraph that only links into the owner's `.github`
  repository ("we require all AI use to follow our [AI policy](https://github.com/owner/.github/...)"):
  that is a pointer, so the organization file is read too.
- In that case the tool picks the organization's `CONTRIBUTING`, AI policy and pull request template files
  and skips `README` and `profile/`. The organization's `CONTRIBUTING` and AI policy files are read even
  when the repository has a `CONTRIBUTING` of its own, because that file has no AI statement. A pull
  request or issue template the repository already has still wins over the organization's.
- When the repository does state an AI policy, the organization is not read at all: the repository's own
  text wins over the organization default whenever both exist.
- The lookup costs at most two API calls (the commit and one recursive tree of the `.github` repository).

Those files appear as `org:.github/<path>` in `files_checked` and in evidence. Their permalinks point into
the `.github` repository at its own commit, reported as `org_commit_sha`; without that SHA the evidence
`url` is `null`. `--no-org` skips the lookup. A repository that states a weaker policy of its own can still
miss a stricter organization rule; see [limitations](limitations.md).

## 4. Match lines

Before matching, text that only illustrates is blanked out (line numbers stay the same): fenced code
blocks (``` and ~~~), reStructuredText literal blocks (after `::`) and sections headed "Example". A README
snippet that shows how to write a policy therefore produces no evidence.

Each file is then split into paragraphs, and each paragraph into sentences. A list item is its own
sentence, and a paragraph that ends with a colon is read together with the list it introduces. A
paragraph is looked at when it mentions AI (`AI`, `LLM`, `agent`, `agentic`, `Copilot`, `Claude`,
`ChatGPT`, `language model`, `generative`, and similar terms; `user-agent` and `ssh agent` do not count).
Agent instruction files (`AGENTS.md`, `CLAUDE.md`, `copilot-instructions`) also get the rule that forbids
agents to open issues or pull requests, even in a paragraph that never says "AI".

The rules live in `src/oss_ai_policy_check/rules/patterns.json` (12 rules). Each rule has a verdict class and a
regular expression. Optional parts narrow it: the file path it applies to (`paths`), phrases that cancel
the match in its sentence (`exclude`, `forbid`) or in its paragraph (`forbid_paragraph`) or in its file
(`forbid_document`), words the sentence must contain (`require`), a negation pattern (`negate`), a check
that the sentence is about a narrow thing only (`narrow`), and `ctx_waives`, which lets a rule drop a
required word when the paragraph sits under a heading such as "Unacceptable uses" or "Do not". Two keys
carry confidence: `weak: true` marks a loose heuristic rule (its evidence is `low` confidence), and
`demote_on` names a pattern that downgrades a match instead of cancelling it (see below). The rules are
general patterns; none names a repository.

The shared list `narrowing_qualifiers` (top of `patterns.json`) holds the words and phrases that narrow a
prohibition: `raw`, `unverified`, `unreviewed`, `fully`, `entirely`, `largely`, `primarily`, `wall of`,
`to disguise`, `submitted without review`, `mass-produced`, `low-effort`, conditions such as `unless` and
`except`, and similar. An entry of the form `@name` includes the named pattern `name`. The ban rule
(`general-ban`) lists it under `demote_on`: a sentence that matches the ban regex and also carries one of these
is reported as `restricted` with `low` confidence, never as `banned`.

What each class means follows `tests/golden/RUBRIC.md`:

- `banned` needs a scope-wide prohibition: AI-generated contributions, code, pull requests or content are
  not accepted, forbidden or closed on sight. A prohibition that is conditional ("unless", "only for
  accepted issues") or qualified ("fully", "primarily", "unreviewed", "raw") is demoted to `restricted` with
  `low` confidence. A prohibition about communication ("do not
  use AI to speak for you"), history ("version 1.0 banned ..."), a sanction or a link to another project
  is not a ban. Tolerating translation or proofreading does not downgrade a ban. When the same file says
  AI may still be used for small autocompletion or "assistively only", the file's prohibition is not
  scope-wide and reads as `restricted`.
- `restricted` is a hard AI-specific limit beyond disclosure and generic accountability: AI-written
  descriptions, issues, comments or replies; AI co-author or "Generated with" trailers (a generic "no
  Co-authored-by" is not an AI limit); AI media; autonomous agents (the agent as the actor: "agents may not
  open pull requests") or AI review bots; AI pull requests only for accepted issues or not for good first
  issues; issues instead of pull requests; AI only for autocompletion or assistively. "Purely agentic
  contributions are not accepted" is generic accountability and gives no signal.
- `disclosure-required` is an ask to disclose, label or name AI use (also a pull request template box or
  prompt about AI). A box that only asserts "no AI was used" is a disclosure answer, not a ban. A pointer to
  guidelines plus "I understand my responsibilities" gives no signal.
- `permitted` is an explicit welcome. Generic accountability beside it ("but you must understand every
  line") does not take it away; accountability alone gives no signal.

| rule | class | what it recognises |
|---|---|---|
| `general-ban` | `banned` | AI contributions, content or code not accepted, forbidden, closed on sight; "no AI / no LLMs"; "use of AI for this project is prohibited"; "must not include content generated by ..." |
| `contribution-limit`, `agent-restriction`, `agent-file-no-posting` | `restricted` | AI pull requests only for accepted issues or not on good first issues, issues instead of pull requests, AI only for autocompletion or assistively, "AI use must not be disclosed"; autonomous agents or AI review bots not allowed; an agent instruction file that forbids opening issues or pull requests |
| `agent-pr-prohibited` | `restricted` | agent instruction files only: a sentence that bars the agent from opening pull requests ("prohibits you from opening pull requests", "agents must not open pull requests", "do not open PRs on behalf of ..."); a target-branch rule ("against main") or a condition ("without", "unless") does not count |
| `outside-pr-closed` | `restricted` | pull requests from outside the maintainers are closed, whatever the reason ("We accept issues, not pull requests", "pull requests are opened by the maintainers only", "we do not accept external pull requests"); it needs no AI word, because the door is shut for an AI-assisted contributor too |
| `attribution-ban`, `own-voice`, `human-written`, `ai-voice-ban`, `media-ban` | `restricted` | no AI co-author or "Generated with" trailers; descriptions, comments and replies in the author's own words or voice ("no AI please"); AI-generated media |
| `disclosure`, `template-ai-prompt` | `disclosure-required` | AI use must be disclosed, revealed, labelled or named in a trailer; a pull request template that asks about AI |
| `welcome` | `permitted` | an explicit welcome of AI-assisted work ("AI-assisted contributions are welcome", "we are okay with AI tools") |

**Agent instruction files.** `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, Copilot and Cursor instruction files (the
`agent_files` entry of `patterns.json`) tell the agent that works in the repository how to build, test, branch and
commit. That is operating procedure for the agent, not the repository's rule for outside contributors, and it is full
of words the rules look for ("never", "agent", "Claude", "commit", "reports"). A rule therefore reads such a file only
in a paragraph that names contributing at all (contributions, pull requests, patches, issues, commits, an AI
policy, disclosure); the loose rules ask for more there (`disclosure` needs a disclosure word, `ai-voice-ban` an AI
authoring phrase such as "AI-generated"); and an instruction that only qualifies the target of a pull request
("never open a PR against `main`") is not a ban on pull requests. Explicit limits keep working in these files
("autonomous agents are not allowed", "no AI attribution trailers", "never create a PR", "the AI agent is not
permitted to create pull requests", a disclosure trailer). The rules `attribution-ban`, `agent-file-no-posting`
and `welcome` are marked `agent_file: "exempt"` and read every paragraph. A policy file such as `AGENT_POLICY.md`
or a product page `docs/agents.md` is not an instruction file and is read like any other file.

Negation is handled per sentence: "we do not ban AI", "this does not prohibit coding assistants" and "no
need to disclose AI use" do not produce a ban, a restriction or a disclosure signal. A sentence with a
condition ("unless", "only if", "except" for anything but translation or proofreading) or a qualifier
("that you haven't reviewed", "fully", "primarily", "unreviewed") is not a full ban: it is reported as a
`restricted` signal with `low` confidence. A sanction ("temporary ban"), a discretion ("may reject"), a link
to another project's policy, or a narrow target (only descriptions, comments or media, including "comments
on issues and pull requests") is not a general ban either; a narrow target is a restriction instead. Emphasis marks are ignored (`**must not** include` reads as `must not
include`).

A match becomes evidence: file, line, class, confidence, and the source line cut at 15 words and 300 characters. A paragraph
that mentions AI but matches no rule is listed under `unclassified_ai_lines`. Those lines are shown so a
reader can look at them; they are not counted as a verdict by themselves (see the next section for when they
make the tool abstain).

## 5. Choose the verdict, or abstain

The classifier does not guess. The order (the exact text is in [schema.md](schema.md#decision-order)):

1. A ban sentence with a narrowing qualifier becomes `restricted` evidence of `low` confidence.
2. Conflicting evidence is not resolved by precedence: a welcome next to a full ban, or a restriction that
   exists only as such a demoted ban next to a weaker signal (welcome, disclosure), gives `needs-review`
   with reason `conflicting-signals`.
3. Otherwise the most restrictive signal wins: `banned`, then `restricted`, then `disclosure-required`,
   then `permitted`. No file outranks another; there is no priority order between files. A single `banned`
   line in any candidate file decides the verdict, and a `restricted` line wins over any number of
   `disclosure-required` lines and over a welcome. `permitted` needs an explicit welcome sentence and loses
   to every other signal.
4. With no evidence at all, but a paragraph that pairs an AI term with a policy verb (`must`, `require`,
   `disclose`, `ban`, `accept`, `allowed`, `do not`, ... plus softer verbs such as `please`, `encourage`,
   `feel free`, `should`, `appreciate`, `responsible`), the verdict is `needs-review` with reason
   `no-rule-decision`, and those paragraphs (at most 20) are the evidence. This catches policies written in a
   soft or welcoming voice that no rule recognised. A paragraph that only links into the owner's `.github`
   repository, one that says there is no AI policy, and README or agent-instruction paragraphs that never
   speak about contributing do not count.
5. Only when none of that exists is the verdict `no-policy-found`, which is not permission.

Evidence is listed strongest first, then by file and line.

**Confidence.** Each evidence item is `high`, or `low` when its rule is marked weak or it is a demoted ban.
The report is `high` only when the evidence that decides the verdict has a `high` item and comes from at
least two distinct sentences; one deciding sentence, or only weak or demoted evidence, gives `low`.
`needs-review` is always `low`. `confidence` is about the reading of the text, `complete` about whether
the files were read.

## 6. Say whether the read was complete

Each candidate file ends up as `read`, `missing` (it does not exist) or `error` (it exists but could not be
read). `missing` is a normal answer. `error`, a truncated tree, a reached file cap or a failed repository
lookup set `complete` to `false` and add a warning. If nothing restrictive was found and `complete` is
`false`, the tool exits with code 3 and makes no decision, because the unread text might hold a
restriction. A `banned` or `restricted` result stands even when `complete` is `false`.

## Transport

The tool asks the anonymous GitHub REST API for the commit and the tree, and reads file contents from
`raw.githubusercontent.com` at the pinned SHA, with GET requests only. The tree is one recursive request.
For very large repositories (`llvm/llvm-project`, `rust-lang/rust`, `NixOS/nixpkgs`) that reply is cut off
by GitHub or larger than the 16 MB read limit; the tool then never parses it and reads the root tree plus
the `.github`, `docs` (and `llvm/docs`, `policies`, `contributing`) trees instead, 3 to 5 API requests rather
than 1, because policy files only live in those places. No `gh` CLI is needed.
`GITHUB_TOKEN` or `GH_TOKEN` is optional and raises the rate limit; the `gh` CLI is used only as a fallback
when the REST API is rate limited or unreachable. Nothing is written to GitHub, and tokens are never
printed. Files and trees are cached on disk by repository and commit SHA
(`%LOCALAPPDATA%\oss-ai-policy-check\cache` on Windows, `$XDG_CACHE_HOME` or `~/.cache` elsewhere;
`--no-cache` turns it off). Report fields and exit codes are defined in [schema.md](schema.md).
