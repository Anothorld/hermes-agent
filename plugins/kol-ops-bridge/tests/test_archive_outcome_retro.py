"""Tier1 outcome retro runs synchronously on archive_collab."""

from __future__ import annotations


def test_archive_collab_triggers_outcome_retro(cal_db, bridge_pkg, monkeypatch):
    cal = cal_db
    o = bridge_pkg.learning_outcome
    monkeypatch.setattr(
        o.learning_llm,
        "invoke_learning_llm",
        lambda *a, **k: (
            '{"outcome_class":"success","root_cause_tags":["great_fit"],'
            '"what_worked":["fast"],"what_failed":[],"forward_guidance":["repeat"],'
            '"price_summary":"ok"}'
        ),
    )
    iid = cal.upsert_identity(primary_handle="arch_kol", env="LIVE")
    cal.archive_collab(
        identity_id=iid,
        campaign_id="C_ARCH",
        outcome="success",
        env="LIVE",
        run_outcome_retro=True,
    )
    with cal._connect() as conn:  # type: ignore[attr-defined]
        assert o.has_outcome_learning_event(
            conn, env="LIVE", identity_id=iid, campaign_id="C_ARCH",
        )
