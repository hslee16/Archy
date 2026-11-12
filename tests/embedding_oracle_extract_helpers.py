"""Shared helpers for the #587 extractor tests (see the two test modules); no fixtures."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from archy.complexity import compute_function_complexity

# bench/ is not a package; append it (not insert(0)) so its generic module names cannot
# shadow stdlib/installed imports, mirroring tests/test_embedding_oracle_pairs.py.
sys.path.append(str(Path(__file__).resolve().parent.parent / "bench"))

from embedding_oracle_extract import (  # ty: ignore[unresolved-import]  (added to sys.path above)
    normalise,
    text_id,
)


def big(name: str, extra: str = "") -> str:
    """A function whose body is far above the 30-node floor (checked by `floor`)."""
    body = "\n".join(f"    v{i} = a + {i} * b - len(c) ** 2" for i in range(8))
    return f"def {name}(a, b, c):{extra}\n{body}\n    return v0 + v7\n"


SMALL = "def tiny(a):\n    return a + 1\n"


def size(source: str, name: str) -> int:
    return next(f.size for f in compute_function_complexity(source.encode()) if f.name == name)


def floor() -> None:
    """The fixture reaches both sides of the floor: big clears it, tiny does not."""
    assert size(big("big"), "big") >= 30
    assert size(SMALL, "tiny") < 30


def write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)


def git(root: Path, *args: str) -> str:
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
        "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
    }
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True, env=env
    ).stdout.strip()


def commit(root: Path, files: dict[str, str]) -> str:
    write(root, files)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "c")
    return git(root, "rev-parse", "HEAD")


def shard_texts(*sources: str) -> dict[str, str]:
    return {text_id(s): normalise(s) for s in sources}
