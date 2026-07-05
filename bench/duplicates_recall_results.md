# Duplicate-detection recall by clone type (#246)

Output of `uv run --with pyyaml python bench/duplicates_recall_experiment.py`. Captured 2026-07-05. 120 real seed functions from django + fastapi (size >= 35), each mutated into known clone types. `shape-hash` recovery = clone shares the seed's `shape_hash`; `token-overlap` recovery = multiset Jaccard >= 0.85 (the shipped near-miss floor); both at min-nodes 30.

| clone type | shape-hash recall | token-overlap recall | applicable |
| --- | ---: | ---: | ---: |
| `t1_identical` | 100.0% | 100.0% | 120 |
| `t2_renamed` | 100.0% | 100.0% | 120 |
| `t3_insert_1stmt` | 0.0% | 100.0% | 120 |
| `t3_delete_1stmt` | 0.0% | 59.5% | 79 |
| `t3_reorder_2stmt` | 1.0% | 100.0% | 105 |
| `t3_flip_operator` | 0.0% | 100.0% | 14 |

The exact shape-hash recovers Type-1/2 fully but ~0% of any Type-3 gap (one statement inserted/deleted or a single operator flipped moves the hash). The token-overlap primitive (#246, `compute_near_duplicates`) recovers the Type-3 rows the hash misses, at the cost of a lower-precision tier (its FP gate is RESEARCH_METRICS §12h). Overall recall on real code is bounded by the clone-type mix; Type-3 is the plurality (~52% on BigCloneBench). See RESEARCH_METRICS §12g/§12h.
