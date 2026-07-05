# Duplicate-detection recall by clone type (#246 motivation)

Output of `uv run --with pyyaml python bench/duplicates_recall_experiment.py`. Captured 2026-07-05. 120 real seed functions from django + fastapi (size >= 35), each mutated into known clone types; recovery = clone shares the seed's `shape_hash` at min-nodes 30.

| clone type | recall | recovered / applicable |
| --- | ---: | ---: |
| `t1_identical` | 100.0% | 120 / 120 |
| `t2_renamed` | 100.0% | 120 / 120 |
| `t3_insert_1stmt` | 0.0% | 0 / 120 |
| `t3_delete_1stmt` | 0.0% | 0 / 79 |
| `t3_reorder_2stmt` | 1.0% | 1 / 105 |
| `t3_flip_operator` | 0.0% | 0 / 14 |

Type-1/2 recover fully (the shape-hash normalizes identifiers/literals); any Type-3 gap - one statement inserted/deleted, or a single operator flipped - moves the hash, so the exact detector misses it. Overall recall on real code is bounded by the clone-type mix; Type-3 is the plurality (~52% on BigCloneBench). See RESEARCH_METRICS §12g; the Type-3-tolerant primitive is tracked in #246.
