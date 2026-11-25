"""Gold-set accuracy runner: real pipeline (fetch_snapshot at the pinned SHA -> classify -> build_report).

    python tests/golden/run_gold.py --split dev                 # tune on this
    python tests/golden/run_gold.py --split holdout --summary   # aggregate numbers only
    python tests/golden/run_gold.py --split all --offline       # reproduce from the cache, no network
    python tests/golden/run_gold.py --cv --offline              # development-set report (see below)
    python tests/golden/run_gold.py --fetch-only                # fill the cache, print nothing about labels
    python tests/golden/run_gold.py --classifier claude --split dev   # same pipeline, Claude picks the verdict

--classifier claude (optional `anthropic` extra, credentials from the SDK: ANTHROPIC_API_KEY or `ant auth login`)
sends the AI-policy paragraphs of every repository to Claude. Every model response is cached on disk under
`_llm_cache/<model>/<sha256 of system prompt and user prompt>.json`, so a rerun with an unchanged prompt costs nothing
(--llm-cache-only: a missing response is an error for that row, no API call at all). The run prints token totals and an
estimated cost at list price (Opus 5.5: $4 in / $20 out per MTok). The deterministic rules stay the default.

Labels are pinned to the commit SHAs in gold.jsonl (scan date 2026-11-22); the tool is always run at
that SHA, never at HEAD. For rows with source "org" exactly one note marker says which SHA `commit_sha` is:
"(repo sha x)" means commit_sha is the owner's `.github` SHA (gold.jsonl), "(org sha x)" means commit_sha is the
repository's (gold4 holdout.jsonl); neither or both is an error (see org_row_shas). org_pins.json pins owners whose
own text decides the verdict.
--gold PATH measures another label file.

Every GitHub response (API JSON and raw bytes, including 404s) is stored under `_cache/`, so a rerun
is offline and a discovery change only fetches files it newly asks for. GET only. GITHUB_TOKEN is
read from the environment by the tool's own transport; nothing here prints it.

Metrics (see metrics()): overall accuracy counts a needs-review verdict as wrong; decided-row accuracy and the
abstain rate treat it as a correct abstention; a "dangerous error" is a gold label with a policy that the tool
reports as no-policy-found.

--cv: hand-written rules cannot be cross-validated (there is nothing to fit per fold), and the former
holdout has been seen during tuning, so all 151 rows are one development set. --cv prints that set
(an optimistic figure), the 50 former-holdout rows separately, per-class precision and recall for both,
and the misses grouped by cause (rule, fetch-org, label-scope; see MISS_CAUSES). The honest number for
unseen repositories is the one measured on a fresh blind set, not these.

The split is deterministic: per verdict, repositories are ordered by sha256("oss-gold-v1:" + repo)
and the first round(2n/3) are `dev`, the rest `holdout`. Misses of the holdout split are printed
only with --holdout-misses, to keep tuning honest.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
# OSS_GOLD_SRC points at another checkout of src/ (used to measure the baseline of an older commit).
sys.path.insert(0, os.environ.get("OSS_GOLD_SRC") or str(HERE.parent.parent / "src"))

from oss_ai_policy_check.classify import classify_documents  # noqa: E402
from oss_ai_policy_check.contract import NEEDS_REVIEW, NO_POLICY_FOUND, VERDICTS, build_report  # noqa: E402
from oss_ai_policy_check.fetch import FetchError, NotFoundError, default_client, fetch_snapshot  # noqa: E402

SPLIT_SEED = "oss-gold-v1:"
GOLD = HERE / "gold.jsonl"  # --gold PATH measures another label file (e.g. a blind set kept outside the repository)
PINS = HERE / "org_pins.json"


def load_gold(path: Path = GOLD) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def split_gold(rows: list[dict]) -> dict[str, str]:
    """repo -> 'dev' | 'holdout', stratified by verdict, ordered by a seeded hash of the repo name."""
    by_verdict: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        by_verdict[row["verdict"]].append(row["repo"])
    out: dict[str, str] = {}
    for repos in by_verdict.values():
        repos.sort(key=lambda r: hashlib.sha256((SPLIT_SEED + r).encode()).hexdigest())
        cut = round(len(repos) * 2 / 3)
        for i, repo in enumerate(repos):
            out[repo] = "dev" if i < cut else "holdout"
    return out


class GoldFormatError(ValueError):
    """A gold row cannot be pinned unambiguously; measuring it would give a wrong number, so stop."""


_SHA_NOTE = {
    "repo": re.compile(r"\(repo sha ([0-9a-f]{7,40})\)"),
    "org": re.compile(r"\(org sha ([0-9a-f]{7,40})\)"),
}


def org_row_shas(row: dict) -> tuple[str, str]:
    """(repository sha, owner `.github` sha) of a row with source "org".

    The `commit_sha` field means different things in the two label files, and reading the wrong one pins the
    organization policy to a repository commit (24 Nov 2026: 37 of 60 instead of the true 42 of 60 on gold4):
      gold.jsonl           commit_sha = owner `.github` SHA, the note carries "(repo sha <hex>)"
      gold4 holdout.jsonl  commit_sha = repository SHA,       the note carries "(org sha <hex>)"
    Exactly one of the two notes must be present; neither or both is an error, never a guess."""
    marks = {kind: rx.search(row.get("note", "")) for kind, rx in _SHA_NOTE.items()}
    if marks["repo"] and marks["org"]:
        raise GoldFormatError(f"{row['repo']}: note carries both '(repo sha ...)' and '(org sha ...)'; "
                              f"cannot tell which SHA commit_sha is")
    if marks["repo"]:
        return marks["repo"].group(1), row["commit_sha"]
    if marks["org"]:
        return row["commit_sha"], marks["org"].group(1)
    raise GoldFormatError(f"{row['repo']}: org row without '(repo sha <hex>)' or '(org sha <hex>)' in the note; "
                          f"commit_sha alone cannot say whether it is the repository or the owner .github commit "
                          f"(reading it as the wrong one gives a wrong measurement)")


def target_ref(row: dict) -> str:
    """The commit of the repository itself (for org rows see org_row_shas)."""
    if row["source"] == "org":
        return org_row_shas(row)[0]
    return row["commit_sha"]


def org_pins(rows: list[dict]) -> dict[str, str]:
    """owner -> owner `.github` SHA. Two rows of one owner must agree; a row overrides org_pins.json."""
    pins = {k: v for k, v in json.loads(PINS.read_text(encoding="utf-8")).items() if not k.startswith("_")}
    from_rows: dict[str, str] = {}
    for row in rows:
        if row["source"] == "org":
            owner, sha = row["repo"].split("/")[0], org_row_shas(row)[1]
            if from_rows.setdefault(owner, sha) != sha:
                raise GoldFormatError(f"{row['repo']}: owner {owner} is pinned to two different .github SHAs")
    pins.update(from_rows)
    return pins


def validate_gold(rows: list[dict]) -> None:
    """Fail before any fetch: every org row must resolve, and no two repositories may share one row."""
    org_pins(rows)
    repos = [row["repo"] for row in rows]
    duplicates = sorted({r for r in repos if repos.count(r) > 1})
    if duplicates:
        raise GoldFormatError(f"duplicate repositories in the gold file: {', '.join(duplicates)}")


class CachingClient:
    """Wraps the real transport: every response is stored on disk; org HEAD is pinned."""

    def __init__(self, cache: Path, pins: dict[str, str], offline: bool = False):
        self.cache, self.pins, self.offline = cache, pins, offline
        self.inner = None
        self.network = 0
        self.misses = 0

    @property
    def calls(self) -> int:
        return self.network

    def _inner(self):
        if self.inner is None:
            self.inner = default_client()
        return self.inner

    def _path(self, kind: str, key: str) -> Path:
        return self.cache / kind / (hashlib.sha1(key.encode()).hexdigest() + ".gz")

    def _load(self, kind: str, key: str):
        target = self._path(kind, key)
        if target.exists():
            return json.loads(gzip.decompress(target.read_bytes()))
        return None

    def _store(self, kind: str, key: str, payload: dict) -> None:
        target = self._path(kind, key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(gzip.compress(json.dumps(payload).encode("utf-8")))

    @staticmethod
    def _replay(entry: dict):
        if entry["status"] == "ok":
            return entry["data"]
        if entry["status"] == "notfound":
            raise NotFoundError("HTTP 404")
        raise FetchError(entry["message"])

    def api(self, endpoint: str):
        found = re.fullmatch(r"repos/([^/]+)/\.github/commits/HEAD", endpoint)
        if found and found.group(1) in self.pins:
            return {"sha": self.pins[found.group(1)]}
        entry = self._load("api", endpoint)
        if entry is None:
            if self.offline:
                self.misses += 1
                raise FetchError("offline: not cached")
            self.network += 1
            try:
                entry = {"status": "ok", "data": self._inner().api(endpoint)}
            except NotFoundError:
                entry = {"status": "notfound"}
            except FetchError as exc:
                if str(exc) == "invalid API JSON" and endpoint.endswith("?recursive=1"):
                    # Transport bug found by this runner: a recursive tree over the 16 MB read cap
                    # (llvm, rust, firefox, nixpkgs) arrives cut off. Report it as `truncated` so the
                    # tool's own subtree fallback runs, as it does for GitHub's own truncation.
                    entry = {"status": "ok", "data": {"truncated": True, "tree": []}}
                elif str(exc) == "HTTP 409":
                    entry = {"status": "error", "message": str(exc)}
                else:
                    raise
            self._store("api", endpoint, entry)
        return self._replay(entry)

    def raw(self, repo: str, sha: str, path: str) -> bytes:
        key = f"{repo}|{sha}|{path}"
        entry = self._load("raw", key)
        if entry is None:
            if self.offline:
                self.misses += 1
                raise FetchError("offline: not cached")
            self.network += 1
            try:
                data = self._inner().raw(repo, sha, path)
                entry = {"status": "ok", "data": data.decode("latin-1")}
            except NotFoundError:
                entry = {"status": "notfound"}
            self._store("raw", key, entry)
        if entry["status"] == "ok":
            return entry["data"].encode("latin-1")
        raise NotFoundError("HTTP 404")


class LlmCacheMiss(Exception):
    """--llm-cache-only and the response is not on disk."""


class LlmCache:
    """Wraps the SDK client: `client.beta.messages.create(**kwargs)` is answered from disk when the (model, prompt)
    pair was seen before, else forwarded to `inner` and stored. Only finished answers (stop_reason end_turn) are
    stored, so a refusal or a cut-off answer is retried on the next run. Never touches credentials."""

    def __init__(self, directory: Path, inner=None):
        self.directory, self.inner = directory, inner
        self.hits = self.calls = 0
        self.billed_in = self.billed_out = 0
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    @staticmethod
    def key(kwargs: dict) -> str:
        """sha256 over system prompt and messages; the model id is the directory, so the pair is the key."""
        system = kwargs.get("system")
        system_text = system if isinstance(system, str) else json.dumps(system, sort_keys=True)
        prompt = json.dumps(kwargs["messages"], sort_keys=True) + "\0" + system_text
        return hashlib.sha256(prompt.encode("utf-8")).hexdigest()

    def create(self, **kwargs):
        target = self.directory / re.sub(r"[^A-Za-z0-9._-]", "_", kwargs["model"]) / (self.key(kwargs) + ".json")
        if target.exists():
            self.hits += 1
            data = json.loads(target.read_text(encoding="utf-8"))
            return SimpleNamespace(stop_reason=data["stop_reason"], stop_details=None,
                                   content=[SimpleNamespace(type="text", text=data["text"])],
                                   usage=SimpleNamespace(**data["usage"]))
        if self.inner is None:
            raise LlmCacheMiss(f"no cached response for model {kwargs['model']} ({target.name[:12]})")
        response = self.inner.beta.messages.create(**kwargs)
        self.calls += 1
        counts = {name: int(getattr(response.usage, name, 0) or 0)
                  for name in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")}
        self.billed_in += counts["input_tokens"] + counts["cache_creation_input_tokens"] + counts["cache_read_input_tokens"]
        self.billed_out += counts["output_tokens"]
        text = next((b.text for b in response.content if getattr(b, "type", None) == "text"), None)
        if response.stop_reason == "end_turn" and text is not None:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps({"model": kwargs["model"], "stop_reason": "end_turn",
                                          "text": text, "usage": counts}, indent=1), encoding="utf-8")
        return response


def run_row(row: dict, client: CachingClient, llm=None) -> dict:
    snapshot = fetch_snapshot(row["repo"], target_ref(row), client)
    if llm is None:
        classification = classify_documents(snapshot.documents, owner=row["repo"].split("/")[0])
        report = build_report(snapshot, classification)
        reason = ""
    else:  # the same pipeline the command line uses
        from oss_ai_policy_check.cli import report_from_snapshot
        report, outcome = report_from_snapshot(snapshot, row["repo"], llm)
        classification, reason = outcome.classification, outcome.reason
    return {"repo": row["repo"], "gold": row["verdict"], "pred": report["verdict"],
            "confidence": row["confidence"], "tool_confidence": report["confidence"],
            "review_reason": report["review_reason"], "complete": report["complete"],
            "classifier": report.get("classifier"), "reason": reason,
            "evidence": [{**e, "rule": c.get("rule", "-")}
                         for e, c in zip(report["evidence"], classification["evidence"])][:4]
            or [{**e, "rule": "-"} for e in report["evidence"][:4]],
            "unclassified": len(report["unclassified_ai_lines"]),
            "files": [f["path"] for f in report["files_checked"] if f["status"] == "read"],
            "warnings": report["warnings"]}


def metrics(results: list[dict]) -> dict:
    """Overall accuracy counts needs-review as wrong. Decided-row accuracy and the abstain rate treat it as a
    correct abstention (the row leaves the denominator); a dangerous error is a repository whose gold label
    has a policy while the tool says no-policy-found (a needs-review is never dangerous)."""
    n = len(results)
    ok = sum(r["gold"] == r["pred"] for r in results)
    decided = [r for r in results if r["pred"] != NEEDS_REVIEW]
    abstained = n - len(decided)
    dangerous = [r["repo"] for r in results if r["gold"] != NO_POLICY_FOUND and r["pred"] == NO_POLICY_FOUND]
    sure = [r for r in decided if r.get("tool_confidence") == "high"]
    unsure = [r for r in decided if r.get("tool_confidence") == "low"]
    conf = Counter((r["gold"], r["pred"]) for r in results)
    per_class = {}
    for v in VERDICTS:
        tp = conf[(v, v)]
        pred = sum(c for (g, p), c in conf.items() if p == v)
        gold = sum(c for (g, p), c in conf.items() if g == v)
        per_class[v] = {"precision": tp / pred if pred else None, "recall": tp / gold if gold else None,
                        "support": gold, "predicted": pred}
    hi = [r for r in results if r["confidence"] == "high"]
    mh = [r for r in results if r["confidence"] in ("high", "medium")]

    def acc(rs):
        return (sum(r["gold"] == r["pred"] for r in rs) / len(rs)) if rs else None

    def dacc(rs):
        return (sum(r["gold"] == r["pred"] for r in rs) / len(rs)) if rs else None

    return {"n": n, "correct": ok, "accuracy": ok / n if n else None,
            "decided_n": len(decided), "decided_correct": sum(r["gold"] == r["pred"] for r in decided),
            "decided_accuracy": dacc(decided),
            "abstained": abstained, "abstain_rate": abstained / n if n else None,
            "dangerous": len(dangerous), "dangerous_repos": dangerous,
            "tool_high_n": len(sure), "tool_high_accuracy": dacc(sure),
            "tool_low_n": len(unsure), "tool_low_accuracy": dacc(unsure),
            "high_n": len(hi), "high_accuracy": acc(hi),
            "high_medium_n": len(mh), "high_medium_accuracy": acc(mh),
            "per_class": per_class, "confusion": {f"{g}>{p}": c for (g, p), c in sorted(conf.items())}}


# Why a repository is still missed, recorded by hand after reading the evidence (scan of 2026-11-22).
#   fetch-org    the policy is in a file the transport does not read (org .github repository, a doc outside
#                the scanned folders, a file a link points to); the classifier never sees the text
#   label-scope  the label rests on a file the labelling rubric does not scan, on an external page, on an
#                interpretation the rubric does not state, or on one weak sentence (confidence low)
# A miss that is listed nowhere is reported as "rule": the classifier saw the text and decided differently.
MISS_CAUSES = {
    # (empty since the AI-named file and AI-context link discovery: every labelled policy file is now fetched)
    # the classifier saw the text; the label rests on something else (see the row's note and RUBRIC.md)
    "scipy/scipy": "label-scope",              # the policy file is outside the folders the rubric scans
    "fastapi/fastapi": "label-scope",          # only an org-level PR template; the label says no policy
    "pnpm/pnpm": "label-scope",                # CONTRIBUTING has an agent disclosure rule; label: agent-instructions-only
    "pytest-dev/pytest": "label-scope",        # credit-trailer checkbox in the template; the label says permitted
    "RustPython/RustPython": "label-scope",    # AGENTS.md forbids an AI Co-authored-by trailer (RUBRIC: restricted)
    "honojs/hono": "label-scope",              # the label counts a maintainers-only PR section that is not AI-specific
    "cupy/cupy": "label-scope", "aaif-goose/goose": "label-scope", "python/cpython": "label-scope",
    "jquery/jquery": "label-scope", "celery/celery": "label-scope", "junegunn/fzf": "label-scope",  # confidence low
}


def pct(x) -> str:
    return "  n/a" if x is None else f"{x * 100:5.1f}"


def print_metrics(title: str, results: list[dict]) -> None:
    m = metrics(results)
    print(f"\n== {title}: overall {m['correct']}/{m['n']} = {pct(m['accuracy'])}% (needs-review counted wrong)  "
          f"gold-high {pct(m['high_accuracy'])}% (n={m['high_n']})  "
          f"gold-high+medium {pct(m['high_medium_accuracy'])}% (n={m['high_medium_n']})")
    print(f"   (a) decided-row accuracy {m['decided_correct']}/{m['decided_n']} = {pct(m['decided_accuracy'])}%  "
          f"(b) abstain rate {m['abstained']}/{m['n']} = {pct(m['abstain_rate'])}%  "
          f"(c) dangerous errors {m['dangerous']} {m['dangerous_repos']}")
    print(f"   tool confidence on decided rows: high {pct(m['tool_high_accuracy'])}% (n={m['tool_high_n']}), "
          f"low {pct(m['tool_low_accuracy'])}% (n={m['tool_low_n']})")
    print(f"{'class':22} {'prec':>6} {'rec':>6} {'support':>8} {'predicted':>9}")
    for v in VERDICTS:
        c = m["per_class"][v]
        print(f"{v:22} {pct(c['precision']):>6} {pct(c['recall']):>6} {c['support']:>8} {c['predicted']:>9}")
    short = {"banned": "ban", "restricted": "restr", "disclosure-required": "discl", "permitted": "perm",
             "no-policy-found": "none", "needs-review": "review"}
    print("confusion (rows gold, cols predicted)")
    print(f"{'':8}" + "".join(f"{short[v]:>7}" for v in VERDICTS))
    for g in VERDICTS:
        if g == NEEDS_REVIEW:
            continue  # no gold label is needs-review
        print(f"{short[g]:8}" + "".join(
            f"{sum(1 for r in results if r['gold'] == g and r['pred'] == p):>7}" for p in VERDICTS))


def print_abstained(results: list[dict], notes: dict[str, str]) -> None:
    held = [r for r in results if r["pred"] == NEEDS_REVIEW]
    print(f"\nabstained, needs-review ({len(held)})")
    for r in sorted(held, key=lambda r: (r["gold"], r["repo"])):
        print(f"- {r['repo']}: gold={r['gold']} ({r['confidence']}) reason={r['review_reason']}")
        print(f"    note: {notes[r['repo']][:120]}")


def print_misses(results: list[dict], notes: dict[str, str]) -> None:
    misses = [r for r in results if r["gold"] != r["pred"] and r["pred"] != NEEDS_REVIEW]
    print(f"\nmisses on decided rows ({len(misses)}; needs-review rows are listed separately)")
    for r in sorted(misses, key=lambda r: (r["gold"], r["repo"])):
        print(f"- {r['repo']}: gold={r['gold']} ({r['confidence']}) pred={r['pred']} "
              f"read={len(r['files'])} complete={r['complete']}")
        print(f"    note: {notes[r['repo']][:150]}")
        for e in r["evidence"][:4]:
            print(f"    pred ev: {e['signal']}/{e['rule']} {e['file']}:{e['line']} {e['quote'][:90]}")


def print_causes(results: list[dict]) -> None:
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        if r["gold"] != r["pred"] and r["pred"] != NEEDS_REVIEW:  # an abstention is listed by print_abstained
            groups[MISS_CAUSES.get(r["repo"], "rule")].append(r)
    print(f"\nmisses by cause ({sum(len(v) for v in groups.values())})")
    for cause in ("rule", "fetch-org", "label-scope"):
        rows = sorted(groups.get(cause, []), key=lambda r: (r["gold"], r["repo"]))
        print(f"- {cause}: {len(rows)}")
        for r in rows:
            print(f"    {r['repo']} [{r['split']}] gold={r['gold']} ({r['confidence']}) pred={r['pred']}")


def print_llm_usage(llm, cache: LlmCache, repos: int) -> None:
    from oss_ai_policy_check.llm import estimate_cost

    def money(i: int, o: int) -> str:
        cost = estimate_cost(i, o, llm.model)
        return "n/a (no price for this model)" if cost is None else f"${cost:.2f}"

    u = llm.usage
    print(f"\nclaude {llm.model}: {repos} repositories, {u.calls} responses ({cache.hits} from the cache, "
          f"{cache.calls} new API calls)")
    print(f"  tokens in all responses: {u.input_tokens} in / {u.output_tokens} out = "
          f"{money(u.input_tokens, u.output_tokens)} at list price (what a cold run costs)")
    print(f"  billed this run:         {cache.billed_in} in / {cache.billed_out} out = "
          f"{money(cache.billed_in, cache.billed_out)} (prompt-cache discounts not applied)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gold", type=Path, default=GOLD, help="label file (default: gold.jsonl); see org_row_shas for "
                    "the two commit_sha conventions of org rows")
    ap.add_argument("--split", choices=("dev", "holdout", "all"), default="dev")
    ap.add_argument("--offline", action="store_true", help="never touch the network; cache misses become read errors")
    ap.add_argument("--cache", type=Path, default=HERE / "_cache")
    ap.add_argument("--fetch-only", action="store_true", help="fill the cache, print no verdicts")
    ap.add_argument("--cv", action="store_true",
                    help="development-set report: all rows, former-holdout rows, per-class precision/recall, misses by cause")
    ap.add_argument("--summary", action="store_true", help="metrics only, no per-repo misses")
    ap.add_argument("--holdout-misses", action="store_true", help="also list holdout misses (do not use while tuning)")
    ap.add_argument("--out", type=Path, help="write per-repo results as JSON")
    ap.add_argument("--classifier", choices=("rules", "claude"), default="rules",
                    help="rules (default) or claude (model responses cached under --llm-cache)")
    ap.add_argument("--model", help="Claude model id (default: the tool's default model)")
    ap.add_argument("--llm-cache", type=Path, default=HERE / "_llm_cache", help="directory of cached model responses")
    ap.add_argument("--llm-cache-only", action="store_true",
                    help="never call the API: a response that is not cached is an error for that row")
    args = ap.parse_args(argv)
    if args.model and args.classifier != "claude":
        ap.error("--model needs --classifier claude")
    if args.cv:
        args.split = "all"

    rows = load_gold(args.gold)
    try:
        validate_gold(rows)
    except GoldFormatError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    split = split_gold(rows)
    chosen = [r for r in rows if args.split == "all" or split[r["repo"]] == args.split]
    client = CachingClient(args.cache, org_pins(rows), offline=args.offline)
    llm = llm_cache = None
    if args.classifier == "claude":
        from oss_ai_policy_check import llm as llm_module  # imported here: an older OSS_GOLD_SRC baseline has no llm
        try:
            sdk = None if args.llm_cache_only else llm_module.load_sdk()
        except llm_module.ClaudeUnavailable as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        llm_cache = LlmCache(args.llm_cache, None if sdk is None else sdk.Anthropic())  # the SDK finds the credentials
        llm = llm_module.ClaudeClassifier(llm_cache, args.model or llm_module.DEFAULT_MODEL, sdk)
    results = []
    for row in chosen:
        try:
            result = run_row(row, client, llm)
        except Exception as exc:  # keep going; one bad repo must not hide the rest
            print(f"ERROR {row['repo']}: {type(exc).__name__}: {exc}", file=sys.stderr)
            result = {"repo": row["repo"], "gold": row["verdict"], "pred": "error", "confidence": row["confidence"],
                      "complete": False, "evidence": [], "unclassified": 0, "files": [], "warnings": [str(exc)]}
        result["split"] = split[row["repo"]]
        results.append(result)
    if args.fetch_only:
        print(f"fetched {len(results)} repositories, network requests {client.network}")
        return 0
    notes = {r["repo"]: r["note"] for r in rows}
    if args.cv:
        print_metrics(f"development set, all {len(results)} rows (optimistic: the rules were tuned on every row)", results)
        held = [r for r in results if r["split"] == "holdout"]
        print_metrics(f"former holdout, {len(held)} rows (seen while tuning, not blind)", held)
        print_causes(results)
        print_abstained(results, notes)
    elif args.split == "all":
        for name in ("dev", "holdout"):
            print_metrics(name, [r for r in results if r["split"] == name])
        print_metrics("all", results)
    else:
        print_metrics(args.split, results)
    if not args.cv and not args.summary and (args.split != "holdout" or args.holdout_misses):
        shown = [r for r in results if r["split"] == "dev" or args.holdout_misses]
        print_misses(shown, notes)
        print_abstained(shown, notes)
    if client.misses:
        print(f"\nWARNING: {client.misses} cache misses in offline mode; numbers are not a faithful rerun")
    if args.out:
        args.out.write_text(json.dumps(results, indent=1), encoding="utf-8")
    print(f"\nnetwork requests this run: {client.network}")
    if llm is not None:
        print_llm_usage(llm, llm_cache, len(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
