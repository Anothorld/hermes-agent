"""Tests for ``--view agent`` dispatch-context slimming."""

from __future__ import annotations

from internal.dispatch_context_agent_view import slim_dispatch_context_for_agent


def test_agent_view_omits_lanes_and_strips_provenance() -> None:
    bundle = {
        "identity_id": 7,
        "campaign_id": "C1",
        "env": "LIVE",
        "goals": [
            {
                "goal": "outreach",
                "status": "active",
                "lane": "commerce",
                "missing_facts": ["offer.outreach_sent"],
                "blocking_escalation_id": None,
                "extra_noise": "drop-me",
            }
        ],
        "lanes": {"commerce": {"status": "active"}},
        "relationship": {"last_outcome": "none", "collab_history": [{"id": 1}]},
        "reusable_facts": {
            "identity_id": 7,
            "facts": {
                "identity.voice": "warm",
                "identity.voice_source": "ig",
                "identity.voice_discovered_at": "2026-01-01",
            },
        },
        "campaign_config": {
            "campaign_id": "C1",
            "label": "Spring",
            "product_pitch": "Sofa",
            "variant_candidates": [{"sku": "A"}],
            "internal_only": "secret",
        },
        "campaign_facts": {
            "approval.reply_draft": {
                "draft": {"subject": "Hi", "body": "Long draft body"},
            }
        },
        "identity_facts": {
            "identity.content_pillars": ["cozy"],
            "identity.content_pillars_source": "web",
        },
        "candidate": {"payload": {"reason": "fit"}},
        "learning_hints": {"offer": []},
    }
    identity = {
        "id": 7,
        "primary_handle": "erin",
        "platform": "instagram",
        "primary_email": "erin@example.com",
        "extra_field": "omit",
    }

    slim = slim_dispatch_context_for_agent(bundle, identity=identity)

    assert slim["view"] == "agent"
    assert "lanes" not in slim
    assert slim["identity"]["primary_handle"] == "erin"
    assert slim["identity"]["identity_id"] == 7
    assert "extra_field" not in slim["identity"]
    assert slim["goals"][0] == {
        "goal": "outreach",
        "status": "active",
        "lane": "commerce",
        "missing_facts": ["offer.outreach_sent"],
        "blocking_escalation_id": None,
    }
    assert "collab_history" not in slim["relationship"]
    assert "identity.voice_source" not in slim["reusable_facts"]["facts"]
    assert "internal_only" not in slim["campaign_config"]
    assert slim["campaign_config"]["product_pitch"] == "Sofa"
    assert slim["campaign_config"]["variant_candidates"] == [{"sku": "A"}]
    assert "[redacted" in slim["campaign_facts"]["approval.reply_draft"]["draft"]["body"]
    assert "identity.content_pillars_source" not in slim["identity_facts"]


def test_agent_view_keeps_creator_brief_freshness_anchors() -> None:
    bundle = {
        "identity_id": 9,
        "campaign_id": "C2",
        "env": "LIVE",
        "identity_facts": {
            "identity.content_pillars": ["cozy"],
            "identity.content_pillars_source": "ig_profile_and_reels",
            "identity.content_pillars_discovered_at": "2026-05-01T12:00:00Z",
        },
    }
    slim = slim_dispatch_context_for_agent(bundle)
    assert slim["identity_facts"]["identity.content_pillars_discovered_at"] == (
        "2026-05-01T12:00:00Z"
    )
    assert "identity.content_pillars_source" not in slim["identity_facts"]
