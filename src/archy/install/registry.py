"""The adapter registry: the one list of supported clients.

Adding a sixth client is a new adapter module plus one entry here. Everything
else (CLI, runner, tests) iterates this registry rather than naming adapters
directly.
"""

from __future__ import annotations

from archy.install.adapters.claude import ClaudeAdapter
from archy.install.adapters.codex import CodexAdapter
from archy.install.adapters.continue_ import ContinueAdapter
from archy.install.adapters.cursor import CursorAdapter
from archy.install.adapters.opencode import OpencodeAdapter
from archy.install.base import AgentAdapter

# Order is the user-facing order (detection table, --print-config listing).
REGISTRY: tuple[AgentAdapter, ...] = (
    ClaudeAdapter(),
    CursorAdapter(),
    CodexAdapter(),
    OpencodeAdapter(),
    ContinueAdapter(),
)

_BY_ID = {adapter.id: adapter for adapter in REGISTRY}


def all_adapters() -> tuple[AgentAdapter, ...]:
    return REGISTRY


def adapter_ids() -> list[str]:
    return [adapter.id for adapter in REGISTRY]


def get_adapter(adapter_id: str) -> AgentAdapter:
    """Look up an adapter by id, raising ``KeyError`` with a helpful message."""
    try:
        return _BY_ID[adapter_id]
    except KeyError:
        known = ", ".join(adapter_ids())
        raise KeyError(f"unknown agent '{adapter_id}'. Known agents: {known}.") from None
