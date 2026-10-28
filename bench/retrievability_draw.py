"""Draw the candidate derivations for the #418 retrievability measurement.

Pre-registered in `bench/retrievability_prereg.md`. The keyword filter and the seed are
fixed there; this script only applies them. It writes the candidates, shuffled with the
seed and stratified by corpus, to the path given, for hand labelling.

    uv run python bench/retrievability_draw.py OUT.json

archy:owns        candidates, main
"""

from __future__ import annotations

import json
import random
import re
import sys
from pathlib import Path

SEED = 418
CORPORA = ("q1b_transcripts", "greenfield_transcripts")
PATTERN = re.compile(
    r"\b(imports?|imported by|who (uses|calls|consumes)|consumers? of|reach(es)?"
    r"|depends on|circular import|import cycle)\b",
    re.IGNORECASE,
)
MAX_CHARS = 1200


def candidates(bench: Path) -> list[dict]:
    out: list[dict] = []
    for corpus in CORPORA:
        for path in sorted((bench / corpus).glob("*.jsonl")):
            for n, line in enumerate(path.read_text().splitlines()):
                try:
                    msg = json.loads(line).get("message")
                except json.JSONDecodeError:
                    continue
                if not isinstance(msg, dict) or msg.get("role") != "assistant":
                    continue
                blocks = msg.get("content")
                for b in blocks if isinstance(blocks, list) else []:
                    text = b.get("text") or b.get("thinking") or ""
                    if b.get("type") in ("text", "thinking") and PATTERN.search(text):
                        out.append(
                            {
                                "corpus": corpus,
                                "file": path.name,
                                "line": n,
                                "text": text[:MAX_CHARS],
                            }
                        )
    return out


def main(argv: list[str]) -> int:
    pool = candidates(Path(__file__).parent)
    rng = random.Random(SEED)
    by_corpus = {c: [x for x in pool if x["corpus"] == c] for c in CORPORA}
    for items in by_corpus.values():
        rng.shuffle(items)
    # Interleave so the draw order is stratified, not corpus-blocked.
    drawn: list[dict] = []
    while any(by_corpus.values()):
        for items in by_corpus.values():
            if items:
                drawn.append(items.pop())
    for i, d in enumerate(drawn):
        d["id"] = i
    Path(argv[1]).write_text(json.dumps(drawn, indent=1))
    print(f"{len(drawn)} candidates written to {argv[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
