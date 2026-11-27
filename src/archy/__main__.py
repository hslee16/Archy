"""`python -m archy`, for an install whose console script is not on PATH (#766)."""

from __future__ import annotations

from archy.cli import main

if __name__ == "__main__":  # pragma: no cover
    main(prog_name="archy")
