"""Unit tests for the adapter registry integrity."""

from __future__ import annotations

import pytest

from archy.install.base import AgentAdapter
from archy.install.registry import adapter_ids, all_adapters, get_adapter

EXPECTED_IDS = {"claude", "cursor", "codex", "opencode", "continue"}


def test_registry_has_the_five_launch_adapters():
    assert set(adapter_ids()) == EXPECTED_IDS


def test_ids_are_unique():
    ids = adapter_ids()
    assert len(ids) == len(set(ids))


def test_every_entry_is_an_adapter_with_required_attrs():
    for adapter in all_adapters():
        assert isinstance(adapter, AgentAdapter)
        assert adapter.id and adapter.name and adapter.cli_name


def test_get_adapter_round_trips():
    for adapter_id in adapter_ids():
        assert get_adapter(adapter_id).id == adapter_id


def test_get_adapter_unknown_raises_with_help():
    with pytest.raises(KeyError) as exc:
        get_adapter("nope")
    assert "nope" in str(exc.value)
    assert "claude" in str(exc.value)
