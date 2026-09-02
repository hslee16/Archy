#!/usr/bin/env python
"""Pull the whole distribution funnel in one command and append a dated row.

Nine research documents defer candidates "behind a usage signal" without that
phrase having a source, a cadence, or a threshold. This script is the source
and the cadence; ``docs/DISTRIBUTION.md`` is the threshold.

Why a script and not the hand-run curl commands in ``docs/PYPI_STATS.md``:

  - **The GitHub traffic endpoints keep a 14-day rolling window.** Views,
    clones, referrers, and paths older than 14 days are gone permanently, for
    everyone, with no archive to recover them from. A month you forget to pull
    is a month that never existed. That alone justifies automating it.
  - **pypistats rate-limits aggressively.** During the #316 research it
    returned HTTP 429 and the download figure simply could not be reported.
    Every source here retries with backoff and, if it still fails, records an
    explicit ``null`` plus the error string rather than crashing the run or
    silently reporting a stale number as current.

No telemetry, by design. Every source below is external and public: archy
itself never phones home, and ``docs/INSTALL.md`` promises exactly that. The
marginal fidelity of in-process metrics is not worth trading that promise for.

Rows are raw counts only, never derived percentages or ratios. Percentages
bake in an interpretation that cannot be undone later; counts can always be
re-analyzed against a question nobody has thought to ask yet.

Usage:
    uv run python bench/distribution.py                    # append a row
    uv run python bench/distribution.py --dry-run          # print, don't append

The GitHub traffic endpoints are owner-only and need a token with repo scope.
The script reads ``GITHUB_TOKEN`` / ``GH_TOKEN``, else falls back to
``gh auth token``. Without one it degrades cleanly: every other source still
lands and the traffic block records its null.

archy:owns        append, build_row, main, pull_adopters, pull_community, pull_pypi,
                  pull_repo, pull_traffic, summarize
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "bench" / "distribution.jsonl"
DEFAULT_REPO = "hslee16/Archy"
DEFAULT_PACKAGE = "archy"
USER_AGENT = "archy-distribution-readout (+https://github.com/hslee16/archy)"

# Referrers and paths are long-tailed; the head is the part that answers "which
# distribution move worked". The tail is mostly single-visit noise.
TOP_N = 10


def _http_json(url: str, headers: dict[str, str] | None = None, attempts: int = 4) -> object:
    """GET and parse JSON, retrying 429/5xx with exponential backoff.

    Honours ``Retry-After`` when the server sends one -- pypistats does, and
    guessing a shorter delay just burns the next attempt too. Raises on final
    failure; every caller catches and records a null.
    """
    hdrs = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    hdrs.update(headers or {})
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code not in (429, 500, 502, 503, 504) or attempt == attempts - 1:
                raise
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            delay = float(retry_after) if retry_after and retry_after.isdigit() else 2.0**attempt
            time.sleep(min(delay, 60.0))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = exc
            if attempt == attempts - 1:
                raise
            time.sleep(2.0**attempt)
    raise last if last else RuntimeError("unreachable")


def _err(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"


def _gh_headers(token: str | None) -> dict[str, str]:
    """Pinning the API version matters more than it looks: the unversioned
    default moves, and a shape change in `user.type` or `pull_request` would
    silently reclassify contributions rather than fail loudly."""
    headers = {"X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _github_token() -> str | None:
    """Token from the environment, else whatever `gh` is already authed as.

    The `gh` fallback exists so the maintainer's normal interactive run needs no
    setup; CI would set GITHUB_TOKEN instead.
    """
    for var in ("GITHUB_TOKEN", "GH_TOKEN"):
        if os.environ.get(var):
            return os.environ[var]
    try:
        res = subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, timeout=15, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    token = res.stdout.strip()
    return token if res.returncode == 0 and token else None


# --------------------------------------------------------------------------
# Sources. Each returns a dict whose values are all populated or all null, plus
# an `error` that is null on success. None of them raise.
# --------------------------------------------------------------------------


def pull_pypi(package: str) -> dict[str, object]:
    """Download rollups from pypistats, with and without mirrors.

    Both matter and neither alone is honest. Mirror share runs 60-70% for a
    package this size (bandersnatch and corporate devpi proxies pull every new
    version automatically), so `with_mirrors` is mostly release-triggered noise
    -- but it is the number every other package quotes, so dropping it makes
    archy's figures look artificially small next to anyone else's.

    Rollups are summed from the daily series rather than read off `/recent`,
    because `/recent` has no mirrors parameter and would give a `with_mirrors`
    number that cannot be compared to the `without_mirrors` one.
    """
    out: dict[str, object] = {
        "with_mirrors": None,
        "without_mirrors": None,
        "data_through": None,
        "error": None,
    }
    try:
        for mirrors, key in ((True, "with_mirrors"), (False, "without_mirrors")):
            url = (
                f"https://pypistats.org/api/packages/{package}/overall"
                f"?mirrors={str(mirrors).lower()}"
            )
            payload = _http_json(url)
            rows = payload["data"] if isinstance(payload, dict) else []
            by_date = {r["date"]: r["downloads"] for r in rows}
            dates = sorted(by_date)
            if not dates:
                out["error"] = "pypistats returned an empty series"
                return out
            # Windows are anchored to the last date *in the series*, not to
            # today: pypistats lags ~24h, so anchoring to today would silently
            # count one empty day and under-report every window by a day.
            out["data_through"] = dates[-1]
            out[key] = {
                "last_day": sum(by_date[d] for d in dates[-1:]),
                "last_week": sum(by_date[d] for d in dates[-7:]),
                "last_month": sum(by_date[d] for d in dates[-30:]),
                "days_available": len(dates),
            }
    except Exception as exc:
        out["error"] = _err(exc)
    return out


def pull_traffic(repo: str, token: str | None) -> dict[str, object]:
    """Views, clones, referrers and paths -- the 14-day-window endpoints.

    This is the block worth protecting. Everything else here can be recovered
    later; these four cannot. `uniques` is the number to read, not `count`:
    count includes the maintainer's own repeated visits and every CI clone.
    """
    out: dict[str, object] = {
        "views_count": None,
        "views_uniques": None,
        "clones_count": None,
        "clones_uniques": None,
        "referrers": None,
        "paths": None,
        "error": None,
    }
    if not token:
        out["error"] = "no GitHub token (traffic is owner-only); see docs/DISTRIBUTION.md"
        return out
    headers = _gh_headers(token)
    try:
        views = _http_json(f"https://api.github.com/repos/{repo}/traffic/views", headers)
        clones = _http_json(f"https://api.github.com/repos/{repo}/traffic/clones", headers)
        base = f"https://api.github.com/repos/{repo}/traffic/popular"
        referrers = _http_json(f"{base}/referrers", headers)
        paths = _http_json(f"{base}/paths", headers)
        out["views_count"] = views["count"]
        out["views_uniques"] = views["uniques"]
        out["clones_count"] = clones["count"]
        out["clones_uniques"] = clones["uniques"]
        out["referrers"] = [
            {"referrer": r["referrer"], "count": r["count"], "uniques": r["uniques"]}
            for r in referrers[:TOP_N]
        ]
        out["paths"] = [
            {"path": p["path"], "count": p["count"], "uniques": p["uniques"]} for p in paths[:TOP_N]
        ]
    except Exception as exc:
        out["error"] = _err(exc)
    return out


def pull_repo(repo: str, token: str | None) -> dict[str, object]:
    """Stars, forks, watchers. Vanity, but cheap, and it is the number
    outsiders judge by before they read a line of the README."""
    out: dict[str, object] = {
        "stars": None,
        "forks": None,
        "watchers": None,
        "open_issues": None,
        "error": None,
    }
    headers = _gh_headers(token)
    try:
        data = _http_json(f"https://api.github.com/repos/{repo}", headers)
        out["stars"] = data["stargazers_count"]
        out["forks"] = data["forks_count"]
        out["watchers"] = data["subscribers_count"]
        # Includes open PRs, the way GitHub's own API defines it. Kept raw
        # rather than netted out, so the series stays re-analyzable.
        out["open_issues"] = data["open_issues_count"]
    except Exception as exc:
        out["error"] = _err(exc)
    return out


def _maintainer_logins() -> set[str]:
    """GitHub handles parsed out of MAINTAINERS.md.

    Parsed rather than hardcoded so adding a maintainer does not silently
    reclassify their work as an outside contribution.
    """
    path = REPO_ROOT / "MAINTAINERS.md"
    if not path.exists():
        return set()
    return {m.lower() for m in re.findall(r"\[@([A-Za-z0-9][A-Za-z0-9-]*)\]", path.read_text())}


def pull_community(repo: str, token: str | None) -> dict[str, object]:
    """Issues and PRs authored by someone other than a maintainer or a bot.

    The strongest real signal in this file, and the one that is hardest to
    manufacture. Downloads can be mirrors and stars can be a good headline;
    an outside issue means somebody ran archy on their own code and cared
    enough about the result to write it up.

    Bots are identified by the API's own `user.type`, not by a name list, so
    a new bot does not quietly inflate the outside count.
    """
    out: dict[str, object] = {
        "issues_total": None,
        "issues_outside": None,
        "prs_total": None,
        "prs_outside": None,
        "prs_bot": None,
        "outside_authors": None,
        "maintainers": None,
        "error": None,
    }
    headers = _gh_headers(token)
    maintainers = _maintainer_logins()
    out["maintainers"] = sorted(maintainers)
    try:
        items: list[dict[str, object]] = []
        # /issues returns issues *and* PRs; PRs carry a `pull_request` key.
        # Unauthenticated this caps at 60 req/hr, which is enough for one
        # monthly run but not for a tight retry loop.
        for page in range(1, 21):
            url = (
                f"https://api.github.com/repos/{repo}/issues"
                f"?state=all&per_page=100&page={page}&filter=all"
            )
            batch = _http_json(url, headers)
            if not isinstance(batch, list) or not batch:
                break
            items.extend(batch)
            if len(batch) < 100:
                break
        issues = [i for i in items if "pull_request" not in i]
        prs = [i for i in items if "pull_request" in i]

        def outside(rows: list[dict[str, object]]) -> list[dict[str, object]]:
            return [
                r
                for r in rows
                if r["user"]["type"] != "Bot" and r["user"]["login"].lower() not in maintainers
            ]

        out["issues_total"] = len(issues)
        out["prs_total"] = len(prs)
        out["issues_outside"] = len(outside(issues))
        out["prs_outside"] = len(outside(prs))
        out["prs_bot"] = len([p for p in prs if p["user"]["type"] == "Bot"])
        out["outside_authors"] = sorted({r["user"]["login"] for r in outside(issues + prs)})
    except Exception as exc:
        out["error"] = _err(exc)
    return out


def pull_adopters() -> dict[str, object]:
    """Entries in ADOPTERS.md.

    The narrowest funnel stage and the only one that is a deliberate public
    act. The placeholder bullet ("Be the first") is italic, not bold, so the
    bold-lead template match skips it without needing a special case.
    """
    out: dict[str, object] = {"count": None, "error": None}
    path = REPO_ROOT / "ADOPTERS.md"
    try:
        body = path.read_text(encoding="utf-8")
        after = body.split("## Adopters", 1)[-1]
        out["count"] = len(re.findall(r"^\s*-\s+\*\*", after, flags=re.MULTILINE))
    except OSError as exc:
        out["error"] = _err(exc)
    return out


def build_row(repo: str, package: str) -> dict[str, object]:
    token = _github_token()
    return {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "repo": repo,
        "package": package,
        "authenticated": token is not None,
        "pypi": pull_pypi(package),
        "traffic": pull_traffic(repo, token),
        "github": pull_repo(repo, token),
        "community": pull_community(repo, token),
        "adopters": pull_adopters(),
    }


def append(out_path: Path, row: dict[str, object]) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("a", encoding="utf-8") as fh:
        # Single write of the JSON *and* its newline, matching
        # src/archy/history.py: two write() calls leave a window where a crash
        # between them lands a record with no trailing newline, so the next
        # append merges onto that line and both rows become unparseable.
        fh.write(json.dumps(row, sort_keys=True) + "\n")


def summarize(row: dict[str, object]) -> str:
    """Human-readable digest. The JSONL row is the artifact; this is so the
    run tells you something without needing to open the file."""
    lines = [f"archy distribution readout  {row['timestamp']}"]
    pypi = row["pypi"]
    wo = pypi["without_mirrors"]
    wi = pypi["with_mirrors"]
    if wo and wi:
        lines.append(
            f"  pypi        {wo['last_month']:>6,} / mo without mirrors"
            f"   ({wi['last_month']:,} with)   through {pypi['data_through']}"
        )
    else:
        lines.append(f"  pypi        null -- {pypi['error']}")
    tr = row["traffic"]
    if tr["views_uniques"] is not None:
        top = tr["referrers"][0]["referrer"] if tr["referrers"] else "none"
        lines.append(
            f"  traffic     {tr['views_uniques']:>6,} unique visitors, "
            f"{tr['clones_uniques']:,} unique cloners (14d)   top referrer: {top}"
        )
    else:
        lines.append(f"  traffic     null -- {tr['error']}")
    gh = row["github"]
    if gh["stars"] is not None:
        lines.append(
            f"  github      {gh['stars']:>6,} stars, {gh['forks']:,} forks, "
            f"{gh['watchers']:,} watchers"
        )
    else:
        lines.append(f"  github      null -- {gh['error']}")
    co = row["community"]
    if co["issues_total"] is not None:
        lines.append(
            f"  outside     {co['issues_outside']:>6,} of {co['issues_total']:,} issues, "
            f"{co['prs_outside']} of {co['prs_total']:,} PRs "
            f"({co['prs_bot']} bot)"
        )
    else:
        lines.append(f"  outside     null -- {co['error']}")
    ad = row["adopters"]
    lines.append(
        f"  adopters    {ad['count']:>6,}" if ad["count"] is not None else "  adopters    null"
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--repo", default=DEFAULT_REPO, help="owner/name (default: %(default)s)")
    parser.add_argument(
        "--package", default=DEFAULT_PACKAGE, help="PyPI name (default: %(default)s)"
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="JSONL to append to")
    parser.add_argument(
        "--dry-run", action="store_true", help="print the row and summary, do not append"
    )
    args = parser.parse_args()

    row = build_row(args.repo, args.package)
    print(summarize(row))
    if args.dry_run:
        print()
        print(json.dumps(row, sort_keys=True, indent=2))
        return 0
    append(args.out, row)
    print(f"\nappended to {args.out.relative_to(REPO_ROOT)}")
    # Exit 0 even with nulls: a partial row is the designed outcome of a rate
    # limit, not a failure, and a nonzero exit would make a cron wrapper page
    # for something that is working as intended.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
