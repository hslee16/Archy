"""What the #587 pre-registration fixed about the evaluation draw, in one place.

The fourth addendum to `docs/research/EMBEDDING_ORACLE_RECALL.md` (2026-11-13) fixed the
evaluation subset: M = 250 measured PRs, at most 25 per repository, and the sha256 of its keys
(sorted, joined by newlines) and of the calibration draw's keys. The evaluation command and the
vector loader both read these, so the two cannot disagree. Changing any constant is a new dated
addendum, never an edit here. No I/O and no heavy imports.

archy:owns        keys_sha256
archy:mirrored-by keys_sha256 -> bench.embedding_oracle_evaluation,
                  bench.embedding_oracle_vectors
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

EVALUATION_SHARD = "evaluation"
EVALUATION_N = 250
EVALUATION_REPO_CAP = 25
EVALUATION_KEYS_SHA256 = "dafa1fa79a93da51d3ff808198eab07dd5a8fe4d0533b4ba16f9edf7963582f0"
CALIBRATION_KEYS_SHA256 = "721eff0ad057bc6f79369657ecb03179bb536f8d221449222cae0645ca779f83"


def keys_sha256(keys: Iterable[str]) -> str:
    """The registered fingerprint of a draw: its sorted keys joined by newlines."""
    return hashlib.sha256("\n".join(sorted(keys)).encode()).hexdigest()
