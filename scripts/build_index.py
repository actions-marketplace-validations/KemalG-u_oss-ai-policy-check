"""Build the weekly AI contribution policy index (not part of the installed package).

Picks popular, recently active public repositories by the criteria in index/config.json, runs the product's
deterministic `rules` classifier on each one through `oss_ai_policy_check.cli.analyze` (no API key, no model; the
fetch logic is the product's own) and writes

    index/latest.json              full result: decision, evidence line, source file, commit SHA per repository
    index/history/YYYY-MM-DD.json  compact snapshot used for the week-over-week comparison
    index/README.md                counts, changes since the previous run, the repositories with a policy signal

Read-only towards GitHub: repository search plus the product's GET transport. Nothing is posted anywhere.
Stops before the hourly API budget runs out and marks the result `partial`.

Usage: python scripts/build_index.py [--config index/config.json] [--out index] [--limit N] [--date YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import date as Date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from oss_ai_policy_check import __version__, cli  # noqa: E402
from oss_ai_policy_check.classify import compile_ai_term, load_patterns  # noqa: E402
from oss_ai_policy_check.contract import (BANNED, DISCLOSURE_REQUIRED, HIGH, NEEDS_REVIEW,  # noqa: E402
                                          NO_POLICY_FOUND, PERMITTED, RESTRICTED)
from oss_ai_policy_check.fetch import FetchError, RateLimitedError, RestTransport, github_token  # noqa: E402
from oss_ai_policy_check.report import _clean, _md  # noqa: E402

SCHEMA_VERSION = "1"
UNREADABLE = "unreadable"  # not a verdict: files could not be read and nothing restrictive was seen
# Publication rule: a public index must not call another project's policy "banned" or "restricted" on a weak read.
# A rules verdict of banned/restricted counts under that name only when it is firm (high confidence: two or more
# deciding sentences, none of them hedged) and its cited line sits in a file that states contribution rules.
# Anything else is listed as `possible`, with its quote, for a person to read; the classifier's own verdict stays
# in the entry's `verdict` field.
POSSIBLE = "possible"
HEADLINE = (BANNED, RESTRICTED)
CATEGORIES = (BANNED, RESTRICTED, POSSIBLE, DISCLOSURE_REQUIRED, NEEDS_REVIEW, PERMITTED, NO_POLICY_FOUND, UNREADABLE)
LISTED = (BANNED, RESTRICTED, POSSIBLE, DISCLOSURE_REQUIRED, NEEDS_REVIEW, PERMITTED)  # listed one by one in the README
# Files that state contribution or agent rules. A README, a documentation page or a provider guide that mentions AI
# is usually describing a product, not setting a rule for contributors.
POLICY_FILE = re.compile(
    r"contribut|(?:^|[_\-. ])ai[_\-. ]?(?:polic|use|usage|rules|guid|assist)|polic|agents?\.md$|claude\.md$|gemini\.md$|"
    r"copilot-instructions|\.cursorrules|\.cursor/rules|pull[_\-]request[_\-]template|issue[_\-]template|"
    r"code[_\-]of[_\-]conduct|governance|hacking|development\.md$|developing\.md$", re.I)
ISSUES_URL = "https://github.com/KemalG-u/oss-ai-policy-check/issues"
NO_CAP = 10 ** 9


class Github(RestTransport):
    """The product's read-only transport plus the two calls the index needs: repository search and /rate_limit."""

    def search_repositories(self, query: str, page: int) -> dict[str, Any]:
        endpoint = ("https://api.github.com/search/repositories?sort=stars&order=desc&per_page=100"
                    f"&page={page}&q={quote(query, safe=':>=<')}")
        saved = dict(self.limits)  # the search bucket has its own counter; keep the core one
        try:
            body, _ = self._get(endpoint, "application/vnd.github+json")
        finally:
            self.limits = saved
        return json.loads(body)

    def core_remaining(self) -> int | None:
        """Requests left in the core bucket: the last API reply's header, else /rate_limit (it costs nothing)."""
        if "x-ratelimit-remaining" not in self.limits:
            body, _ = self._get("https://api.github.com/rate_limit", "application/vnd.github+json")
            core = json.loads(body)["resources"]["core"]
            self.limits = {"x-ratelimit-remaining": str(core["remaining"]), "x-ratelimit-reset": str(core["reset"])}
        return int(self.limits["x-ratelimit-remaining"])


# ------------------------------------------------------------------ selection

def search_query(selection: dict[str, Any], today: Date) -> str:
    since = today - timedelta(days=int(selection["pushed_within_days"]))
    return f"pushed:>={since.isoformat()} stars:>={int(selection['min_stars'])} archived:false fork:false"


def select_repos(search: Callable[[str, int], dict[str, Any]], selection: dict[str, Any], today: Date,
                 max_pages: int = 10) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Most-starred first; archived repositories and forks never; at most `max_per_language` per language so that
    one ecosystem cannot fill the index; `count` repositories. Returns (repos, selection facts)."""
    query = search_query(selection, today)
    count, cap = int(selection["count"]), int(selection.get("max_per_language") or NO_CAP)
    exclude = {name.lower() for name in selection.get("exclude", [])}
    skip_languages = set(selection.get("exclude_languages", []))  # link lists and doc-only repositories
    chosen: list[dict[str, Any]] = []
    per_language: dict[str, int] = {}
    seen: set[str] = set()
    total = None
    for page in range(1, max_pages + 1):
        reply = search(query, page)
        total = reply.get("total_count", total)
        items = reply.get("items", [])
        for item in items:
            name = item["full_name"]
            language = item.get("language") or "(none)"
            if (name.lower() in exclude or name.lower() in seen or item.get("archived") or item.get("fork")
                    or item.get("private") or language in skip_languages or per_language.get(language, 0) >= cap):
                continue
            seen.add(name.lower())
            per_language[language] = per_language.get(language, 0) + 1
            chosen.append({"repo": name, "stars": item.get("stargazers_count", 0), "language": language,
                           "default_branch": item.get("default_branch") or None})
            if len(chosen) >= count:
                break
        if len(chosen) >= count or len(items) < 100:
            break
    facts = {"query": query, "matching_repositories": total, "requested": count,
             "max_per_language": cap if cap < NO_CAP else None,
             "excluded_languages": sorted(skip_languages), "languages": dict(sorted(per_language.items()))}
    return chosen, facts


# ------------------------------------------------------------------ one repository

_AI = compile_ai_term(load_patterns())


def best_evidence(evidence: list[dict[str, Any]], verdict: str) -> dict[str, Any] | None:
    """The line to show for a decision: a high-confidence line of the verdict's own signal first, and among equals a
    line that names AI itself (a label such as "Banned:" or a sentence about localisation files says nothing)."""
    own = [item for item in evidence if item["signal"] == verdict] or list(evidence)
    own.sort(key=lambda item: (item.get("confidence") != "high", not _AI.search(item.get("quote") or "")))
    return own[0] if own else None


def headline_category(verdict: str, confidence: str | None, source_file: str | None) -> str:
    """The class an entry is counted under: the verdict, except that a weak banned/restricted read is `possible`."""
    if verdict in HEADLINE and not (confidence == HIGH and source_file and POLICY_FILE.search(source_file)):
        return POSSIBLE
    return verdict


def entry_from_report(info: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    verdict, complete = report["verdict"], report["complete"]
    line = best_evidence(report["evidence"], verdict)
    return {
        "repo": info["repo"], "stars": info["stars"], "language": info["language"], "status": "ok",
        "category": (UNREADABLE if (not complete and verdict == NO_POLICY_FOUND)
                     else headline_category(verdict, report["confidence"], line["file"] if line else None)),
        "verdict": verdict, "confidence": report["confidence"], "review_reason": report["review_reason"],
        "complete": complete, "commit_sha": report["commit_sha"],
        "source_file": line["file"] if line else None, "evidence_line": line["line"] if line else None,
        "evidence_quote": _clean(line["quote"]) if line else None, "evidence_url": line["url"] if line else None,
        "evidence_count": len(report["evidence"]),
    }


def error_entry(info: dict[str, Any], reason: str) -> dict[str, Any]:
    return {"repo": info["repo"], "stars": info["stars"], "language": info["language"], "status": "error",
            "category": UNREADABLE, "verdict": None, "confidence": None, "review_reason": None, "complete": False,
            "commit_sha": None, "source_file": None, "evidence_line": None, "evidence_quote": None,
            "evidence_url": None, "evidence_count": 0, "error": _clean(reason)[:200]}


def refusal_wait(message: str) -> float:
    """Seconds to wait after a refused read: the 'resets in N min' of the message (at most 10 min), else 60."""
    found = re.search(r"resets in (\d+) min", message)
    return float(max(30, min(600, int(found.group(1)) * 60 + 5))) if found else 60.0


def analyze_with_retry(info: dict[str, Any], analyze: Callable[[dict[str, Any]], dict[str, Any]], retries: int,
                       sleep: Callable[[float], None]) -> dict[str, Any] | str:
    """The report, or the refusal text when a read stayed rate limited after `retries` waits. Other failures raise.
    A refused read inside a report (the product records it as a warning) is not an answer about the repository."""
    for attempt in range(retries + 1):
        try:
            report = analyze(info)
            refusal = next((w for w in report["warnings"] if "rate limited" in w), None)
            if refusal is None:
                return report
        except RateLimitedError as exc:
            refusal = str(exc)
        if attempt < retries:
            sleep(refusal_wait(refusal))
    return refusal


def scan(selected: list[dict[str, Any]], analyze: Callable[[dict[str, Any]], dict[str, Any]],
         remaining: Callable[[], int | None], reserve: int, *, retries: int = 0,
         sleep: Callable[[float], None] = time.sleep,
         log: Callable[[str], None] = lambda _message: None) -> tuple[list[dict[str, Any]], str | None]:
    """Analyse the repositories in order. A refused read is retried after a wait; still refused, or fewer than
    `reserve` requests left, stops the scan and returns the reason. The repository that hit the limit is not recorded,
    so it is not mistaken for 'unreadable'."""
    entries: list[dict[str, Any]] = []
    for position, info in enumerate(selected, 1):
        left = remaining()
        if left is not None and left < reserve:
            return entries, f"rate limit: {left} requests left, reserve {reserve}"
        try:
            outcome = analyze_with_retry(info, analyze, retries, sleep)
        except (FetchError, ValueError) as exc:
            entries.append(error_entry(info, str(exc)))
            continue
        if isinstance(outcome, str):
            return entries, f"rate limit at {info['repo']}: {_clean(outcome)[:160]}"
        entries.append(entry_from_report(info, outcome))
        if position % 25 == 0:
            log(f"{position}/{len(selected)} scanned")
    return entries, None


# ------------------------------------------------------------------ results

def summarize(entries: list[dict[str, Any]]) -> dict[str, int]:
    counts = {name: 0 for name in CATEGORIES}
    for entry in entries:
        counts[entry["category"]] += 1
    return counts


def compact(doc: dict[str, Any]) -> dict[str, Any]:
    out = {key: doc[key] for key in ("schema_version", "date", "partial", "tool", "counts")}
    out["repos"] = [{"repo": e["repo"], "category": e["category"], "confidence": e["confidence"],
                     "commit_sha": e["commit_sha"], "complete": e["complete"]} for e in doc["repos"]]
    return out


def compare(previous: dict[str, Any] | None, current: dict[str, Any]) -> dict[str, Any] | None:
    """Week-over-week: repositories present in both runs whose class changed, plus who entered or left. A partial run
    cannot say a repository left, and a repository unreadable on either side cannot count as changed."""
    if previous is None:
        return None
    before = {e["repo"]: e for e in previous["repos"]}
    after = {e["repo"]: e for e in current["repos"]}
    changed = [{"repo": name, "from": before[name]["category"], "to": entry["category"]}
               for name, entry in after.items()
               if name in before and before[name]["category"] != entry["category"]
               and UNREADABLE not in (before[name]["category"], entry["category"])]
    unknown = bool(previous.get("partial") or current["partial"])
    return {"since": previous["date"], "changed": changed, "entered": sorted(set(after) - set(before)),
            "left": [] if unknown else sorted(set(before) - set(after)),
            "compared_repositories": len(set(after) & set(before)), "left_unknown": unknown}


def load_previous(history_dir: Path, today: str) -> dict[str, Any] | None:
    """The newest snapshot dated before `today`."""
    if not history_dir.is_dir():
        return None
    names = sorted(p.name for p in history_dir.glob("????-??-??.json") if p.stem < today)
    return json.loads((history_dir / names[-1]).read_text(encoding="utf-8")) if names else None


_MEANING = {BANNED: "a quoted line says AI-assisted work is refused (firm: two or more deciding sentences, in a contribution or agent file)",
            RESTRICTED: "AI use is conditional (human in the loop, limits on scope; firm, as for `banned`)",
            POSSIBLE: "the rules read a ban or a restriction but not firmly (one deciding sentence, a hedged one, or a line outside the contribution files); read the quote, it is not counted as banned or restricted",
            DISCLOSURE_REQUIRED: "AI use has to be disclosed or labelled",
            NEEDS_REVIEW: "AI-policy text the tool could not decide on; a person must read it",
            PERMITTED: "an explicit welcome statement",
            NO_POLICY_FOUND: "no AI-policy text in the scanned scope (not permission)",
            UNREADABLE: "files could not be read; no decision possible"}


def _row(entry: dict[str, Any]) -> str:
    where = f"{_md(entry['source_file'])}:{entry['evidence_line']}"
    cell = f"[{where}]({entry['evidence_url']})" if entry["evidence_url"] else where
    quote_text = f" {_md(entry['evidence_quote'])}" if entry["evidence_quote"] else ""
    seen = f"{entry['confidence']}, rules read: `{entry['verdict']}`" if entry["category"] == POSSIBLE else entry["confidence"]
    return f"| {_md(entry['repo'])} | {entry['stars']} | {seen} | {cell}{quote_text} |"


def render_readme(doc: dict[str, Any], diff: dict[str, Any] | None) -> str:
    counts, facts = doc["counts"], doc["selection"]
    lines = ["# AI contribution policy index", "",
             "Weekly scan of popular open source repositories for their AI contribution rules. "
             f"Latest run: **{doc['date']}**, {len(doc['repos'])} repositories"
             + (f" (**partial**: {_md(doc['partial_reason'])}; {facts['requested']} were requested)"
                if doc["partial"] else "") + ".", "",
             "> **Read this first.** This is an automatic scan with the `rules` classifier of "
             f"[oss-ai-policy-check](https://github.com/KemalG-u/oss-ai-policy-check) {doc['tool']['version']}: "
             "fixed rules, no language model. Measured accuracy is 70% (42 of 60 repositories, see "
             "[Measured accuracy](../README.md#measured-accuracy)). A class is a hint that points you at the cited "
             "line, not a ruling: read the line. `banned` and `restricted` are shown only for a firm read; a weaker "
             "one is listed as `possible` (needs review) with its quote. "
             f"If a row is wrong, [open an issue]({ISSUES_URL}).", "",
             "## Counts", "", "| class | repositories | meaning |", "|---|---:|---|"]
    lines += [f"| `{name}` | {counts[name]} | {_MEANING[name]} |" for name in CATEGORIES]
    cap = f"at most {facts['max_per_language']} per language, " if facts["max_per_language"] else ""
    lines += ["", f"Selection: {facts['matching_repositories']} public repositories matched "
              f"`{facts['query'].replace('`', '')}`; most-starred first, {cap}archived repositories and forks left out"
              + (f", repositories whose main language is {', '.join(facts['excluded_languages'])} "
                 "(mostly link lists and documentation) left out" if facts.get("excluded_languages") else "")
              + ". Even so, a repository that is mainly a list of links or a documentation set can match on wording "
              "that is not a contribution rule; the cited line shows when it did.", "",
              "## Changes since the previous run", ""]
    if diff is None:
        lines += ["This is the first run; nothing to compare yet.", ""]
    else:
        summary = (f"Compared with {diff['since']} ({diff['compared_repositories']} repositories in both runs): "
                   f"{len(diff['changed'])} changed class, {len(diff['entered'])} entered the selection")
        summary += (". One of the two runs was partial, so departures are not counted." if diff["left_unknown"]
                    else f", {len(diff['left'])} left it.")
        lines += [summary, ""]
        if diff["changed"]:
            lines += ["| repository | before | now |", "|---|---|---|"]
            lines += [f"| {_md(c['repo'])} | `{c['from']}` | `{c['to']}` |" for c in diff["changed"]] + [""]
    lines += ["## Repositories with a policy signal", "",
              "Most-starred first inside each class. The link goes to the cited line at the scanned commit.", ""]
    for name in LISTED:
        rows = [entry for entry in doc["repos"] if entry["category"] == name]
        if rows:
            title = f"`{name}` - needs review" if name == POSSIBLE else f"`{name}`"
            lines += [f"### {title} ({len(rows)})", "", "| repository | stars | confidence | line |",
                      "|---|---:|---|---|"] + [_row(entry) for entry in rows] + [""]
    lines += ["## Data", "",
              "- [`latest.json`](latest.json): every repository with its decision, quoted line, source file and commit SHA.",
              "- [`history/`](history): one compact snapshot per run, for your own comparisons.",
              "- [`config.json`](config.json): the selection criteria. Changing them changes what is compared.", "",
              "The data is public GitHub content read at pinned commits; quotes are cut at 15 words. "
              "It is not legal advice and `no-policy-found` is not permission to contribute with AI. "
              "Related: [melissawm/open-source-ai-contribution-policies]"
              "(https://github.com/melissawm/open-source-ai-contribution-policies) (hand-curated policies) and "
              "[sujeito-operator/ai-contribution-policy](https://github.com/sujeito-operator/ai-contribution-policy) "
              "(one-time August 2026 measurement of the top 800 repositories).", ""]
    return "\n".join(lines)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def dump(value: Any) -> str:
    return json.dumps(value, indent=1, ensure_ascii=False) + "\n"


def build_document(today: str, config: dict[str, Any], facts: dict[str, Any], entries: list[dict[str, Any]],
                   stopped: str | None, used: tuple[int, int]) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "date": today, "partial": stopped is not None,
            "partial_reason": stopped,
            "tool": {"name": "oss-ai-policy-check", "version": __version__, "classifier": "rules"},
            "selection": {**{key: config["selection"][key] for key in ("pushed_within_days", "min_stars")}, **facts},
            "requests": {"api": used[0], "raw": used[1]},
            "counts": summarize(entries), "repos": entries}


def write_outputs(out: Path, doc: dict[str, Any]) -> dict[str, Any] | None:
    diff = compare(load_previous(out / "history", doc["date"]), doc)
    write_text(out / "latest.json", dump(doc))
    write_text(out / "history" / f"{doc['date']}.json", dump(compact(doc)))
    write_text(out / "README.md", render_readme(doc, diff))
    return diff


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the weekly AI contribution policy index.")
    parser.add_argument("--config", default=str(ROOT / "index" / "config.json"))
    parser.add_argument("--out", default=str(ROOT / "index"))
    parser.add_argument("--limit", type=int, help="override selection.count (smoke runs)")
    parser.add_argument("--date", help="UTC date to stamp (default: today)")
    args = parser.parse_args(argv)
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    if args.limit:
        config["selection"]["count"] = args.limit
    today = args.date or datetime.now(timezone.utc).date().isoformat()
    token = github_token()
    if not token:
        print("warning: no GITHUB_TOKEN; anonymous reads allow 60 API requests per hour", file=sys.stderr)
    client = Github(token, delay=float(config["scan"].get("request_delay_seconds", 0.2)))
    try:
        selected, facts = select_repos(client.search_repositories, config["selection"], Date.fromisoformat(today))
    except (FetchError, ValueError, KeyError) as exc:
        print(f"error: repository selection failed ({_clean(exc)})", file=sys.stderr)
        return 1
    include_org = bool(config["scan"].get("include_org_policy", True))

    def analyze(info: dict[str, Any]) -> dict[str, Any]:
        return cli.analyze(info["repo"], info["default_branch"], client=client, no_cache=True, org=include_org)

    started = (client.calls, client.raw_calls)
    entries, stopped = scan(selected, analyze, client.core_remaining, int(config["scan"].get("rate_reserve", 40)),
                            retries=int(config["scan"].get("retries", 2)),
                            log=lambda message: print(message, file=sys.stderr))
    used = (client.calls - started[0], client.raw_calls - started[1])
    if not entries:
        print("error: nothing scanned" + (f" ({stopped})" if stopped else ""), file=sys.stderr)
        return 1
    doc = build_document(today, config, facts, entries, stopped, used)
    write_outputs(Path(args.out), doc)
    print(f"{len(entries)}/{len(selected)} repositories, {used[0]} API + {used[1]} raw requests "
          f"({used[0] / len(entries):.1f} API per repository), partial={doc['partial']}, counts={doc['counts']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
