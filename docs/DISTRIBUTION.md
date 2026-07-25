# Distribution readout

How archy measures whether anyone is using it, and what counts as "yes".

Nine research documents defer a candidate feature "behind a usage signal."
Until now that phrase had no source, no cadence, and no threshold, so it could
never be satisfied and never be falsified. This document fixes the threshold in
writing *before* the numbers are in, and [`bench/distribution.py`](../bench/distribution.py)
provides the source.

## Running it

```bash
uv run python bench/distribution.py              # append a dated row
uv run python bench/distribution.py --dry-run    # print, append nothing
```

Rows land in [`bench/distribution.jsonl`](../bench/distribution.jsonl), one JSON
object per line, same append-only shape as `.archy/history.jsonl`. The file is
committed: a series nobody can read is not a measurement.

The GitHub traffic endpoints are owner-only, so they need a token with `repo`
scope. The script reads `GITHUB_TOKEN` or `GH_TOKEN`, and otherwise falls back
to `gh auth token`, which covers the normal interactive run with no setup.
Without a token it degrades cleanly: PyPI, stars, issue/PR authorship, and
`ADOPTERS.md` all still land, and the traffic block records a null with the
reason. Rate limits and network failures behave the same way. The script never
crashes on a source being unavailable, and never substitutes a stale number for
a current one.

## Cadence: monthly, minimum

**The GitHub traffic endpoints keep a 14-day rolling window.** Views, clones,
referrers, and paths older than 14 days are deleted, for everyone, with no
archive and no way to reconstruct them. A month nobody pulls is a month that
did not happen.

Monthly is the floor, not the target. Around a release or any deliberate
distribution move (a directory listing going live, a post shipping), pull
before and after, because the referrer breakdown is the only thing in this file
that attributes an increase to a cause.

Everything else here is recoverable at any time. Only the traffic block is
perishable, and it is the block that answers "which move worked."

## What counts as a usage signal

Any **one** of the following, **counted from the 2026-07-25 baseline forward**.
Written down now so it is a threshold rather than a vibe read off whichever
number happens to look best later.

The forward-dated framing is not a hedge, it is a correction. The first draft of
this list said outside PRs were effectively zero. Checking the actual six showed
that is false: [#182](https://github.com/hslee16/archy/pull/182) and
[#183](https://github.com/hslee16/archy/pull/183) (mojobeeping) are merged
behavior changes: CLI numeric-option validation and rejecting directory paths
for the contracts config, not typo fixes. Criterion 2 was already met before
this document existed. A bar that is already cleared measures nothing going
forward, hence the baseline date.

1. **An issue filed by someone who is not a maintainer.** Any issue, including
   a bug report or a "does it support X" question. Baseline: **0 of 96**. This
   one is genuinely untouched.
2. **A PR from an outside contributor that is not a typo fix**, and not from a
   bot. Substance, not volume: one real patch clears this, five README
   punctuation fixes do not. Baseline: 6 outside PRs of 235, of which 3 merged
   and 2 were substantive. Three more are open right now
   ([#329](https://github.com/hslee16/archy/pull/329),
   [#330](https://github.com/hslee16/archy/pull/330),
   [#332](https://github.com/hslee16/archy/pull/332)), all refactors rather than
   bug reports from use.
3. **An `ADOPTERS.md` entry.** Baseline: 0.
4. **A sustained non-mirror download baseline that survives a release spike**
   The `without_mirrors` floor between releases holding at a visibly higher
   level a month after a distribution move than a month before. Not the peak:
   the peak is mirrors and curiosity, the floor is use.

Note what the existing outside PRs have in common: every one is a refactor, a
test-helper extraction, or a docs correction found by *reading* archy. None
report a result from *running* archy on the contributor's own code. That
distinction is the whole point of criterion 1, and it is why an outside issue
outranks an outside PR on this list despite being cheaper to produce.

Read `without_mirrors`, and read `uniques` rather than `count`. Mirror share
runs 60-70% for a package this size, because bandersnatch and corporate devpi
proxies pull every new version automatically whether or not a human ever asked
for it. `count` on the traffic endpoints includes the maintainer's own visits
and every CI clone.

**Stars, forks, and watchers are not on this list.** They are recorded because
they are cheap and because they are the number outsiders judge by, but they are
not evidence anyone ran archy on their own code.

### What a null result means

Per [#322](https://github.com/hslee16/archy/issues/322): if the readout still
shows near-zero after the distribution work lands, **that is information about
the product, not a reason to do more marketing.** The threshold above exists so
that outcome is legible instead of arguable. Nobody gets to move the bar after
seeing the numbers.

## What is deliberately not measured

**Telemetry.** [`docs/INSTALL.md`](INSTALL.md) promises archy does not phone
home, and the README sells no-commercial-version, no-account. Every source in
this file is external and public. In-process metrics would buy marginal
fidelity at the cost of a stated value, which is a bad trade at any adoption
level. Recorded here so it does not get re-proposed.

**Anything derived.** Rows store raw counts only, never percentages or ratios.
A percentage bakes in an interpretation that cannot be undone; counts can be
re-analyzed later against a question nobody has asked yet.

**Token savings or performance.** archy has two measured nulls of its own
(agent footprint [#282](https://github.com/hslee16/archy/issues/282), brief
[#289](https://github.com/hslee16/archy/issues/289)) and `RESEARCH_METRICS.md`
§14c.7 forbids an archy-specific token claim. Distribution work does not get to
relax that. The honest pitch is what archy *catches*.

## Baseline, 2026-07-25

The first committed row, for comparison against everything that follows.

| Metric | Value |
|---|---|
| PyPI downloads, last 30d, without mirrors | 1,707 |
| PyPI downloads, last 30d, with mirrors | 6,495 |
| Unique visitors (14d) | 16 |
| Unique cloners (14d) | 151 |
| Top referrer | github.com |
| Stars / forks / watchers | 6 / 7 / 0 |
| **Issues by outsiders** | **0 of 96** |
| **PRs by outsiders** (excl. 8 bot) | **6 of 235** (3 merged, 3 open) |
| **`ADOPTERS.md` entries** | **0** |

The three bolded rows are the ones that matter, and they are the finding
[#316](https://github.com/hslee16/archy/issues/316) surfaced and
[#322](https://github.com/hslee16/archy/issues/322) exists to respond to.

Two and a half months after the Show HN: zero outside issues, zero adopters. The
PR row is the one that is better than #322's framing of it (six rather than
three, with two merged substantive patches), but all six are contributions from
reading the source, not reports from running the tool. Nobody has yet told archy
what it found in their codebase.

One note on reading the traffic row: 151 unique cloners against 16 unique
visitors is not 151 interested people. Clones without a corresponding page view
are overwhelmingly CI, mirrors, and scrapers. The visitor number is the more
honest one, and it is small.

## Related

- [`docs/PYPI_STATS.md`](PYPI_STATS.md): local-only maintainer notes
  (gitignored): hand-run curl commands, BigQuery pointers, and interpretation
  notes on mirror share and release-day inflation. Useful when the script
  leaves a question open.
- [`docs/MCP_DIRECTORIES.md`](MCP_DIRECTORIES.md): directory submission status,
  the discovery end of the same funnel.
- [`ADOPTERS.md`](../ADOPTERS.md): the narrowest stage, and the only one that
  is a deliberate public act.
