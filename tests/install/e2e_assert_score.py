"""Does the client's reply report the real score? (gated E2E, #1018)

Run as `python tests/install/e2e_assert_score.py <score.json> <reply.txt>`.
The reply must contain the overall score `archy score --format json` computed,
to at least two decimals. An error message has numbers too, but not that one.
Needs no archy import, so it matches the published server the client ran.

It reads the reply, not a tool trace, so it cannot tell the MCP tool from
another way of getting the number; the workflow keeps those out of reach.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# Half a unit in the second decimal, so a reply rounded to two places passes.
_TOLERANCE = 0.005
# A whole decimal number: not a piece of a version like 10.0.19041.
_NUMBER = re.compile(r"(?<![\d.])[01]\.\d{2,}(?![\d.]*\d)")


def says_the_score(reply: str, overall: float) -> bool:
    return any(abs(float(n) - overall) <= _TOLERANCE for n in _NUMBER.findall(reply))


def main(argv: list[str]) -> int:
    expected, reply = Path(argv[1]), Path(argv[2])
    overall = float(json.loads(expected.read_text(encoding="utf-8"))["overall"])
    if says_the_score(reply.read_text(encoding="utf-8", errors="replace"), overall):
        print(f"OK: the reply reports the score, {overall:.3f}")
        return 0
    print(f"FAIL: no number in the reply matches the score {overall:.3f}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
