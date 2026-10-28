# Keeping the score trend in CI

`archy score` is a point in time. This keeps the points: on every merge to your default
branch, archy appends one row to a history branch in the same repository, and renders the
trend. It needs nothing but the workflow token and no extra service ([#605](https://github.com/hslee16/Archy/issues/605)).

```yaml
name: archy history
on:
  push:
    branches: [main]
permissions:
  contents: write            # to write the history branch, nothing else
concurrency:
  group: archy-history       # one writer at a time; do not cancel a run mid-write
  cancel-in-progress: false
jobs:
  history:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: hslee16/archy@v0.52.0     # the first release with `command: history`
        with:
          command: history
          record: "true"
```

Each run writes the trend as a job summary (the last 10 records) and uploads a
self-contained HTML trend as the `archy-score-trend` artifact.

On a pull request, run the same step with `record` left at `false` to show the trend
without writing anything. A pull request never records, even with `record: "true"`:
an unmerged head is not part of the project's history, and a fork's token is read-only.

## Where the data lives

A branch called `archy-history` (change it with `history-branch`) holding
`history/YYYY-MM.jsonl`, one file per month, one JSON row per merge. It is created as an
orphan branch, so it shares no history with your code and does not show up in your
code's `git log`. Rows are the same ones `archy score --record` writes locally, so
`archy trend` and `archy render --view trend` read them unchanged.

Why a branch and not something else GitHub gives away free:

| Option | Why not |
| --- | --- |
| Actions artifact | Expires (90 days on a public repo); a quiet quarter loses the series |
| Actions cache | Evicted after 7 idle days; keys cannot be overwritten |
| Actions variables | The workflow token cannot write them |
| GitHub Pages | Private repositories need a paid plan; every merge redeploys a whole site |
| Release asset | Replacing the file is delete-then-upload, so it is not atomic |

## How it stays correct

- **Two merges at once.** Each write sends the `sha` of the file it read, so GitHub rejects
  the loser; it re-reads and re-applies only its own row. Nobody's row is dropped or
  duplicated. The `concurrency` group above makes this rare; it does not make it
  necessary.
- **A re-run of the same commit** is skipped, not recorded twice.
- **File size.** The contents API serves a file inline up to 1 MB, so history is split by
  month (a row is about 450 bytes). A month that would exceed 900 KB is not written and
  the step says so.
- **Never fails your build.** If GitHub cannot be reached or refuses the write, the step
  emits a `::warning::` and carries on; this is observability, not a gate.

## Things to check in your repository

- If a branch ruleset applies to **all** branches and requires pull requests or status
  checks, the history branch needs an exception, or the write is refused (and warned).
  This was not tested against a ruleset.
- The score is computed by the archy version you pin. If you upgrade archy and the scoring
  changes, the trend will show a step at the upgrade; the rows do not record the version.
