# #548 data

Ledgers and labels behind `bench/diff_duplicates_precision_results.md`.

- `precision.run0.jsonl`: the first 87 PRs, run in one process before sharding.
- `precision.shard<K>of8.jsonl`: the remaining PRs, sharded by repository, `K` in 0..7.
- `key.json`: for the 120 firings drawn for labeling, the fields the labelers were
  blind to (tier, similarity, PR, agent, outcome). Do not open before labeling.
- `labels_A.jsonl`, `labels_B.jsonl`: the two labelers' verdicts, one row per firing.

Reproduce the verdict and the tables (needs the clones, so run `diff_duplicates_run.py`
first on a fresh checkout, or read the numbers from the results file):

    uv run python bench/diff_duplicates_report.py --ledgers bench/diff_duplicates_data/precision.*.jsonl \
        --labels-a bench/diff_duplicates_data/labels_A.jsonl \
        --labels-b bench/diff_duplicates_data/labels_B.jsonl \
        --key bench/diff_duplicates_data/key.json
