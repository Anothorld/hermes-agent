"""Console wiring for gateway memory-layer brief helpers."""

from __future__ import annotations

from app.bridge_agent_contract_loader import (
    format_hindsight_recall_seed,
    memory_layers_brief_block,
)


def test_memory_layers_brief_block_loads_from_bridge_contract():
    text = memory_layers_brief_block()
    assert "Memory layers" in text
    assert "learning_hints" in text
    assert "Hindsight" in text


def test_format_hindsight_recall_seed_via_loader():
    text = format_hindsight_recall_seed(
        campaign_id="C-99",
        identity_id=7,
        handle="@kol",
    )
    assert "# hindsight_recall_seed" in text
    assert "campaign_id: C-99" in text
    assert "identity_id: 7" in text
    assert "handle: @kol" in text
