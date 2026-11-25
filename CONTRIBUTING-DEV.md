# Developer guide: modules and tests

The report contract (`docs/schema.md`, `schema/report.schema.json`, `src/oss_ai_policy_check/contract.py`) is
stable: schema version "2" added `needs-review`, `confidence` and `review_reason`. Treat a change to it as a
public API change. A change to the verdict set or the report shape bumps `SCHEMA_VERSION` and adds a migration
note to `docs/schema.md` and `CHANGELOG.md`; open an issue first so the maintainer can agree on it.

## Module responsibilities

| Module | Responsibility | Tests |
|---|---|---|
| `contract.py` | vocabulary, report shape, exit codes, `clip_quote`, permalinks | `tests/test_contract.py`, `tests/_support.py` |
| `cli.py` | argument parsing and exit codes | `tests/test_cli.py` |
| `classify.py`, `rules/patterns.json` | turns document text into a verdict and evidence lines (pure) | `tests/test_classify.py`, `tests/golden/` |
| `discover.py` | decides which files are candidates (pure) | `tests/test_discover*.py` |
| `fetch.py` | all network and disk I/O: GitHub REST, raw files, local clones, the cache | `tests/test_fetch.py`, `tests/_fakes.py` |
| `report.py` | renders a report as text, JSON, markdown or SARIF; neutralises untrusted text | `tests/test_report.py` |
| `scripts/build_index.py`, `index/`, `.github/workflows/index.yml` | weekly policy index (not packaged; reuses `cli.analyze`, adds repository search only) | `tests/test_build_index.py` |
| `action.yml`, `.github/workflows/` | the GitHub Action and the project's own CI | `tests/test_action_yaml.py` |

Documentation lives in `README.md`, `AI-USE.md` and `docs/`; `THIRD-PARTY-NOTICES.md` carries the MIT notice of
`beduldul/ai-policy` and its text must not change. Keep a change inside the module that owns the behaviour, and
update the matching tests and docs in the same change.

Rules of the road:

- Stdlib only, Python >= 3.10. Everything user-visible (CLI, JSON keys, messages) is English.
- Tests are offline `unittest`. A test that needs the network is a bug.
- Dependency direction: `contract` <- `classify`, `discover`, `fetch`, `report`, `cli`.
  `classify` and `discover` are pure. Only `fetch` does I/O.
- Keep every public signature listed in a module's docstring. Adding is fine; changing one is an API change (see above).
- Never print raw `gh` output or local paths in messages; quotes are at most 15 words and 300 characters.
- Do not edit `THIRD-PARTY-NOTICES.md` content (MIT notice of `beduldul/ai-policy`).

## Run the tests

From the repository root (no install needed, `tests/_support.py` puts `src/` on the path):

```
python -X utf8 -B -m unittest discover -s tests
```

One module: `python -X utf8 -B -m unittest discover -s tests -p "test_classify.py" -v`.

Editable install and a live run (anonymous REST works; `GITHUB_TOKEN` raises the rate limit):

```
python -m venv .venv
.venv/Scripts/python -m pip install -e .        # Linux/macOS: .venv/bin/python
.venv/Scripts/oss-ai-policy-check pymeasure/pymeasure --format json
```

Check a JSON report against the schema with the test helper:

```
python -X utf8 -B -c "import sys,json;sys.path.insert(0,'tests');import _support;print(_support.schema_errors(json.load(open('report.json',encoding='utf-8'))))"
```

An empty list means valid.

Gold-set accuracy (151 labelled repositories). `--offline` reads cached responses under `tests/golden/_cache`,
which is not part of the repository: it needs a filled local cache, and the first run without `--offline` fetches
the responses from GitHub (set `GITHUB_TOKEN`). After that it runs without the network:

```
python -X utf8 -B tests/golden/run_gold.py --split all --offline --summary
```

`--split dev|holdout|all` picks the repositories; without `--offline` cache misses are fetched (set `GITHUB_TOKEN`).
Tune on `dev` only; read `holdout` after the rules are frozen.

The summary prints the overall accuracy (a `needs-review` verdict counts as wrong), then (a) the accuracy on
decided rows, (b) the abstain rate and (c) the "dangerous errors" (a labelled policy reported as
`no-policy-found`). A rule change is good when (a) does not fall and (c) does not rise; the abstain rate is
what the tool pays for it. These figures are development-set figures and optimistic: only a fresh blind set says
how the rules do on repositories they have not seen. Never tune a rule on a blind set, and never name a repository
in `rules/patterns.json`.

## Releasing

Tag-driven PyPI release through Trusted Publishing, with the one-time setup steps: [docs/release.md](docs/release.md).
