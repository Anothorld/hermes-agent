"""Tests for confirmed-candidate ingest and jsonl buffer replay."""

from __future__ import annotations

import sys

import pytest

CAMPAIGN = "C-ingest-test"


def _ingest(cal_db, tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_KOL_OPS_INGEST_STATE_DIR", str(tmp_path))
    return sys.modules["kol_ops_bridge_pkg.confirmed_ingest"]


def _buffer(cal_db, tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_KOL_OPS_INGEST_STATE_DIR", str(tmp_path))
    return sys.modules["kol_ops_bridge_pkg.confirmed_fact_buffer"]


def _seed_campaign(cal):
    cal.upsert_campaign_config(
        campaign_id=CAMPAIGN,
        label="Ingest test",
        barter_policy="barter_first",
        product_unit_price=200.0,
        paid_ceiling=1000.0,
        sku_whitelist=["SKU-A"],
        deliverable_platforms=["instagram"],
        deliverable_count_per_platform=1,
        contract_required=False,
    )


def _facts_payload(handle: str) -> dict:
    now = "2026-06-01T09:00:00+00:00"
    profile = f"https://www.instagram.com/{handle}/"
    reel = "https://www.instagram.com/reel/ABC123xyz/"
    return {
        "identity.instagram_profile_url": profile,
        "identity.instagram_profile_url_source": "ig_bio",
        "identity.instagram_profile_url_discovered_at": now,
        "identity.instagram_profile_url_discovered_url": profile,
        "identity.content_pillars": ["cozy living", "family routines"],
        "identity.content_pillars_source": "ig_profile_and_reels",
        "identity.content_pillars_discovered_at": now,
        "identity.content_pillars_discovered_url": profile,
        "identity.signature_hooks": ["before/after tour", "POV diary"],
        "identity.signature_hooks_source": "ig_profile_and_reels",
        "identity.signature_hooks_discovered_at": now,
        "identity.signature_hooks_discovered_url": profile,
        "identity.voice_descriptors": ["warm", "honest", "cozy"],
        "identity.voice_descriptors_source": "ig_profile_and_reels",
        "identity.voice_descriptors_discovered_at": now,
        "identity.voice_descriptors_discovered_url": profile,
        "identity.hero_post_url": reel,
        "identity.hero_post_url_source": "ig_reel_pick",
        "identity.hero_post_url_discovered_at": now,
        "identity.hero_post_url_discovered_url": profile,
        "identity.hero_post_note": "Comfort tour reel",
        "identity.hero_post_note_source": "ig_reel_pick",
        "identity.hero_post_note_discovered_at": now,
        "identity.hero_post_note_discovered_url": profile,
        "identity.recommendation_reason": "Strong family-home fit.",
        "identity.recommendation_reason_source": "ig_profile_and_reels",
        "identity.recommendation_reason_discovered_at": now,
        "identity.recommendation_reason_discovered_url": profile,
    }


def test_ingest_confirmed_candidate_writes_all_layers(cal_db, tmp_path, monkeypatch):
    cal = cal_db
    ingest = _ingest(cal, tmp_path, monkeypatch)
    _seed_campaign(cal)

    out = ingest.ingest_confirmed_candidate(
        campaign_id=CAMPAIGN,
        env="TEST",
        source="tool:cdp-ingest",
        identity={"primary_handle": "alice_kol", "platform": "instagram"},
        candidate={
            "source": "discovery:profile_verification",
            "discovery_score": 82.0,
            "payload": {"evidence_url": "https://www.instagram.com/alice_kol/"},
        },
        identity_facts=_facts_payload("alice_kol"),
        ingest_id="ingest-alice-1",
    )

    assert out["identity_id"]
    assert out["candidate_id"]
    assert out["written"]["identity"] is True
    assert out["written"]["candidate"] is True
    assert "identity.hero_post_url" in out["written"]["facts"]

    facts = cal.latest_facts_for(identity_id=out["identity_id"], campaign_id=None, env="TEST")
    assert facts["identity.hero_post_url"] == "https://www.instagram.com/reel/ABC123xyz/"

    cand = cal.get_candidate_for(
        identity_id=out["identity_id"], campaign_id=CAMPAIGN, env="TEST",
    )
    assert cand is not None
    assert cand["candidate_status"] == "discovered"


def test_ingest_idempotent_by_ingest_id(cal_db, tmp_path, monkeypatch):
    cal = cal_db
    ingest = _ingest(cal, tmp_path, monkeypatch)
    _seed_campaign(cal)

    body = dict(
        campaign_id=CAMPAIGN,
        env="TEST",
        source="tool:cdp-ingest",
        identity={"primary_handle": "bob_kol", "platform": "instagram"},
        candidate={"source": "discovery", "payload": {}},
        identity_facts={},
        ingest_id="ingest-bob-1",
    )
    first = ingest.ingest_confirmed_candidate(**body)
    second = ingest.ingest_confirmed_candidate(**body)
    assert second["already_imported"] is True
    assert second["identity_id"] == first["identity_id"]


def test_ingest_rejects_owner_mismatch_hero_post(cal_db, tmp_path, monkeypatch):
    cal = cal_db
    ingest = _ingest(cal, tmp_path, monkeypatch)
    _seed_campaign(cal)

    facts = _facts_payload("carol_kol")
    facts["identity.hero_post_url_discovered_url"] = "https://www.instagram.com/other_creator/"

    with pytest.raises(ingest.IngestValidationError) as exc:
        ingest.ingest_confirmed_candidate(
            campaign_id=CAMPAIGN,
            env="TEST",
            source="tool:cdp-ingest",
            identity={"primary_handle": "carol_kol", "platform": "instagram"},
            candidate={"source": "discovery", "payload": {}},
            identity_facts=facts,
        )
    assert "owner mismatch" in str(exc.value)


def test_ingest_skips_existing_identity_facts(cal_db, tmp_path, monkeypatch):
    cal = cal_db
    ingest = _ingest(cal, tmp_path, monkeypatch)
    _seed_campaign(cal)

    iid = cal.upsert_identity(primary_handle="dana_kol", platform="instagram", env="TEST")
    cal.write_facts(
        identity_id=iid,
        campaign_id=None,
        namespace="identity",
        facts={
            "identity.instagram_profile_url": "https://www.instagram.com/dana_kol/",
            "identity.instagram_profile_url_source": "ig_bio",
            "identity.instagram_profile_url_discovered_at": "2026-06-01T09:00:00+00:00",
            "identity.instagram_profile_url_discovered_url": "https://www.instagram.com/dana_kol/",
        },
        source="seed",
        env="TEST",
    )

    out = ingest.ingest_confirmed_candidate(
        campaign_id=CAMPAIGN,
        env="TEST",
        source="tool:cdp-ingest",
        identity={"primary_handle": "dana_kol", "platform": "instagram"},
        candidate={"source": "discovery", "payload": {}},
        identity_facts=_facts_payload("dana_kol"),
    )
    assert "identity.instagram_profile_url" in out["skipped"]["facts"]
    assert "identity.hero_post_url" in out["written"]["facts"]


def test_buffer_replay_imports_pending(cal_db, tmp_path, monkeypatch):
    cal = cal_db
    buf_mod = _buffer(cal, tmp_path, monkeypatch)
    _ingest(cal, tmp_path, monkeypatch)
    _seed_campaign(cal)

    buf_path = tmp_path / "buffer.jsonl"
    monkeypatch.setattr(buf_mod, "default_buffer_path", lambda: buf_path)

    payload = {
        "source": "tool:cdp-ingest",
        "identity": {"primary_handle": "eve_kol", "platform": "instagram"},
        "candidate": {"source": "discovery", "payload": {"followers": "120K"}},
        "identity_facts": _facts_payload("eve_kol"),
    }
    buf_mod.append_enqueue(
        path=buf_path,
        campaign_id=CAMPAIGN,
        env="TEST",
        payload=payload,
        fact_id="buffer-eve-1",
        identity_hint="eve_kol",
    )

    result = buf_mod.replay_pending(path=buf_path)
    assert result["attempted"] == 1
    assert result["imported"] == ["buffer-eve-1"]
    assert not result["failed"]

    ident = cal.find_identity_by_handle("eve_kol", env="TEST")
    assert ident is not None
    cand = cal.get_candidate_for(
        identity_id=ident["id"], campaign_id=CAMPAIGN, env="TEST",
    )
    assert cand is not None

    second = buf_mod.replay_pending(path=buf_path)
    assert second["attempted"] == 0


def test_ingest_normalizes_flat_identity_fact_keys(cal_db, tmp_path, monkeypatch):
    cal = cal_db
    ingest = _ingest(cal, tmp_path, monkeypatch)
    _seed_campaign(cal)

    flat = {
        "instagram_profile_url": "https://www.instagram.com/eve2_kol/",
        "instagram_profile_url_source": "ig_bio",
        "instagram_profile_url_discovered_at": "2026-06-01T09:00:00+00:00",
        "instagram_profile_url_discovered_url": "https://www.instagram.com/eve2_kol/",
        "nox_audience_authenticity": 0.91,
    }
    out = ingest.ingest_confirmed_candidate(
        campaign_id=CAMPAIGN,
        env="TEST",
        source="tool:cdp-ingest",
        identity={"primary_handle": "eve2_kol", "platform": "instagram"},
        candidate={"source": "discovery", "payload": {}},
        identity_facts=flat,
    )
    assert "identity.instagram_profile_url" in out["written"]["facts"]
    assert "identity.nox_audience_authenticity" in out["written"]["facts"]
    assert out["skipped"]["dropped_identity_fact_keys"] == []


def test_buffer_crash_recovery_still_replayable(cal_db, tmp_path, monkeypatch):
    buf_mod = _buffer(cal_db, tmp_path, monkeypatch)

    buf_path = tmp_path / "buffer.jsonl"
    payload = {
        "source": "tool:cdp-ingest",
        "identity": {"primary_handle": "frank_kol", "platform": "instagram"},
        "candidate": {"source": "discovery", "payload": {}},
        "identity_facts": _facts_payload("frank_kol"),
    }
    buf_mod.append_enqueue(
        path=buf_path,
        campaign_id=CAMPAIGN,
        env="TEST",
        payload=payload,
        fact_id="buffer-frank-1",
    )

    pending = buf_mod.list_pending(buf_path)
    assert len(pending) == 1
    assert pending[0]["fact_id"] == "buffer-frank-1"


# ---------------------------------------------------------------------------
# Round-2: structured IngestValidationError fields
# ---------------------------------------------------------------------------


def test_ingest_validation_error_carries_structured_fields():
    """IngestValidationError must carry code/missing_fields/invalid_fields
    so the HTTP 400 can tell the agent exactly what to fix.
    """
    ci = sys.modules["kol_ops_bridge_pkg.confirmed_ingest"]
    exc = ci.IngestValidationError(
        "creator brief bundle incomplete",
        code="creator_brief_incomplete",
        missing_fields=["hero_post_url", "hero_post_note"],
        invalid_fields=[{"field": "identity.voice_descriptors", "reason": "2-3"}],
    )
    assert exc.code == "creator_brief_incomplete"
    assert exc.missing_fields == ["hero_post_url", "hero_post_note"]
    assert len(exc.invalid_fields) == 1
    assert str(exc) == "creator brief bundle incomplete"


def test_ingest_brief_bundle_400_has_missing_fields(cal_db, tmp_path, monkeypatch):
    """A partial creator brief bundle must raise IngestValidationError with
    code='creator_brief_incomplete' and missing_fields listing the absent keys.
    """
    ci = _ingest(cal_db, tmp_path, monkeypatch)
    cal = cal_db
    _seed_campaign(cal)

    # Include voice_descriptors but omit the other 5 brief keys -> partial bundle.
    facts = {
        "identity.instagram_profile_url": "https://www.instagram.com/partial_kol/",
        "identity.instagram_profile_url_source": "ig_bio",
        "identity.instagram_profile_url_discovered_at": "2026-06-01T09:00:00+00:00",
        "identity.instagram_profile_url_discovered_url": "https://www.instagram.com/partial_kol/",
        "identity.voice_descriptors": ["warm"],
        "identity.voice_descriptors_source": "ig_profile",
        "identity.voice_descriptors_discovered_at": "2026-06-01T09:00:00+00:00",
        "identity.voice_descriptors_discovered_url": "https://www.instagram.com/partial_kol/",
    }
    with pytest.raises(ci.IngestValidationError) as exc_info:
        ci.ingest_confirmed_candidate(
            campaign_id=CAMPAIGN,
            env="TEST",
            source="tool:cdp-ingest",
            identity={"primary_handle": "partial_kol", "platform": "instagram"},
            candidate={"source": "discovery", "payload": {}},
            identity_facts=facts,
        )
    assert exc_info.value.code == "creator_brief_incomplete"
    assert len(exc_info.value.missing_fields) > 0
    assert "identity.content_pillars" in exc_info.value.missing_fields
