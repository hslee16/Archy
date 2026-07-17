"""Unit tests for the deterministic core of the agent-footprint bench (#259).

Only `parse_transcript` and `summarize` are exercised here: they are pure over
a session `.jsonl` and need no live agent. The runner (`run_variant` /
`run_pair`) invokes `claude` and is intentionally left to the live bench, never
CI. See docs/SPEC_AGENT_FOOTPRINT_BENCH.md sections 4-5 for the metric
definitions these assertions pin.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# bench/ is not a package; append it (not insert(0)) so its generic module
# names cannot shadow stdlib/installed imports, mirroring tests/test_delta_direction.py.
sys.path.append(str(Path(__file__).resolve().parent.parent / "bench"))

import agent_footprint as af  # ty: ignore[unresolved-import]  (added to sys.path above)


def _assistant(usage: dict, tool_uses: list[tuple[str, str | None]]) -> dict:
    """A synthetic Claude Code transcript `assistant` line."""
    content: list[dict] = []
    for name, file_path in tool_uses:
        block: dict = {"type": "tool_use", "id": f"toolu_{name}", "name": name, "input": {}}
        if file_path is not None:
            block["input"]["file_path"] = file_path
        content.append(block)
    return {
        "type": "assistant",
        "message": {"role": "assistant", "content": content, "usage": usage},
    }


def _user_tool_result(text: str = "ok") -> dict:
    # User/tool_result lines carry no usage and no tool_use; the parser must skip
    # them for token sums so they cannot inflate the footprint.
    return {
        "type": "user",
        "message": {"role": "user", "content": [{"type": "tool_result", "content": text}]},
    }


def _write_transcript(path: Path, lines: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(entry) for entry in lines) + "\n", encoding="utf-8")


def _sample_transcript(path: Path) -> None:
    """A 4-turn run with a known-by-hand set of expected metrics.

    Sequence of file touches:
      m1 Read a.py     (a not yet written -> no revisit)
      m2 Edit a.py     (a not yet written -> no revisit; a now written)
      m3 Read a.py     (a written -> REVISIT #1) + Write b.py (new -> no revisit; b written)
      m4 Edit b.py     (b written -> REVISIT #2)
    Plus a Grep and a Bash that must NOT count as file touches, and a
    user/system line that must NOT contribute tokens.
    """
    _write_transcript(
        path,
        [
            {"type": "system", "subtype": "init"},
            _assistant(
                {
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "cache_read_input_tokens": 1000,
                    "cache_creation_input_tokens": 200,
                },
                [("Read", "a.py"), ("Grep", None)],
            ),
            _user_tool_result(),
            _assistant(
                {
                    "input_tokens": 30,
                    "output_tokens": 20,
                    "cache_read_input_tokens": 1100,
                    "cache_creation_input_tokens": 0,
                },
                [("Edit", "a.py")],
            ),
            _user_tool_result(),
            _assistant(
                {
                    "input_tokens": 40,
                    "output_tokens": 25,
                    "cache_read_input_tokens": 1150,
                    "cache_creation_input_tokens": 0,
                },
                [("Read", "a.py"), ("Write", "b.py"), ("Bash", None)],
            ),
            _user_tool_result(),
            _assistant(
                {
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "cache_read_input_tokens": 1200,
                    "cache_creation_input_tokens": 0,
                },
                [("Edit", "b.py")],
            ),
        ],
    )


def test_parse_transcript_token_sums(tmp_path: Path):
    t = tmp_path / "session.jsonl"
    _sample_transcript(t)
    parsed = af.parse_transcript(t)
    # Summed across the four assistant messages only (user/system skipped).
    assert parsed.input_tokens == 100 + 30 + 40 + 10  # 180
    assert parsed.output_tokens == 50 + 20 + 25 + 5  # 100
    assert parsed.cache_read_input_tokens == 1000 + 1100 + 1150 + 1200
    assert parsed.cache_creation_input_tokens == 200
    assert parsed.assistant_messages == 4
    # Headline footprint excludes cache tokens (spec section 5).
    assert parsed.footprint_tokens == 280


def test_parse_transcript_file_metrics(tmp_path: Path):
    t = tmp_path / "session.jsonl"
    _sample_transcript(t)
    parsed = af.parse_transcript(t)
    # a.py and b.py; Grep/Bash have no file_path so they do not count.
    assert parsed.distinct_files_touched == 2
    # Two revisits: m3 Read a.py (a already edited), m4 Edit b.py (b already written).
    assert parsed.file_revisitations == 2


def test_first_write_is_not_a_revisit(tmp_path: Path):
    # A file read once and edited once, never returned to, is zero revisitations.
    t = tmp_path / "session.jsonl"
    _write_transcript(
        t,
        [
            _assistant({"input_tokens": 5, "output_tokens": 5}, [("Read", "x.py")]),
            _assistant({"input_tokens": 5, "output_tokens": 5}, [("Edit", "x.py")]),
        ],
    )
    parsed = af.parse_transcript(t)
    assert parsed.distinct_files_touched == 1
    assert parsed.file_revisitations == 0


def test_path_normalization_collapses_equivalent_paths(tmp_path: Path):
    t = tmp_path / "session.jsonl"
    _write_transcript(
        t,
        [
            _assistant({"input_tokens": 1, "output_tokens": 1}, [("Write", "pkg/a.py")]),
            _assistant({"input_tokens": 1, "output_tokens": 1}, [("Edit", "pkg/../pkg/a.py")]),
        ],
    )
    parsed = af.parse_transcript(t)
    assert parsed.distinct_files_touched == 1  # same file after normpath
    assert parsed.file_revisitations == 1  # the second edit is a revisit


def test_malformed_lines_are_skipped(tmp_path: Path):
    t = tmp_path / "session.jsonl"
    good = json.dumps(_assistant({"input_tokens": 7, "output_tokens": 3}, [("Read", "a.py")]))
    t.write_text(f"not json\n\n{good}\n{{ partial", encoding="utf-8")
    parsed = af.parse_transcript(t)
    assert parsed.input_tokens == 7
    assert parsed.assistant_messages == 1


def _record(variant: str, run_index: int, **over) -> af.FootprintRecord:
    base = dict(
        variant=variant,
        run_index=run_index,
        model="test-model",
        input_tokens=100,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
        output_tokens=50,
        num_turns=5,
        distinct_files_touched=4,
        file_revisitations=3,
        duration_ms=1000,
        total_cost_usd=0.01,
        task_completed=True,
        test_regression=False,
    )
    base.update(over)
    return af.FootprintRecord(**base)


def test_summarize_paired_deltas(tmp_path: Path):
    # Two paired runs; B is consistently lighter than A on footprint + revisits.
    records = [
        _record("A", 0, input_tokens=200, output_tokens=100, file_revisitations=6),
        _record("B", 0, input_tokens=150, output_tokens=80, file_revisitations=3),
        _record("A", 1, input_tokens=220, output_tokens=90, file_revisitations=5),
        _record("B", 1, input_tokens=160, output_tokens=70, file_revisitations=2),
    ]
    summary = af.summarize(records)
    assert summary["n_pairs"] == 2
    fp = summary["metrics"]["footprint_tokens"]
    # A: 300, 310 ; B: 230, 230 ; deltas: -70, -80 ; median -75.
    assert fp["median_delta"] == -75
    assert fp["b_lower_count"] == 2 and fp["b_higher_count"] == 0
    assert summary["metrics"]["file_revisitations"]["median_delta"] == -3
    assert summary["regressions"] == 0
