"""Step 5 wiring: outcome jobs in suites + overview outcome stats."""

from __future__ import annotations


def test_outcome_jobs_in_suites(bridge_pkg):
    jobs = bridge_pkg.learning_jobs
    capture = jobs.resolve_job_names(suite="capture")
    nightly = jobs.resolve_job_names(suite="nightly")
    assert "analyze_collab_outcome" in capture
    assert "apply_outcome_policy" in nightly
    assert "analyze_collab_outcome" in nightly


def _archive(cal, *, handle, campaign_id, outcome):
    iid = cal.upsert_identity(primary_handle=handle, env="LIVE")
    cal.archive_collab(
        identity_id=iid, campaign_id=campaign_id, outcome=outcome, decided_by="t",
    )
    return iid


def test_analyze_collab_outcome_job(cal_db, bridge_pkg, monkeypatch):
    jobs = bridge_pkg.learning_jobs
    o = bridge_pkg.learning_outcome
    cal = cal_db
    monkeypatch.setattr(o.learning_llm, "invoke_learning_llm", lambda *a, **k: "{}")
    _archive(cal, handle="job_o1", campaign_id="C1", outcome="lost")
    with cal._connect() as conn:  # type: ignore[attr-defined]
        out = jobs._execute_job(
            conn,
            job_name=jobs.JOB_ANALYZE_COLLAB_OUTCOME,
            env="LIVE",
            triggered_by="test",
            limit=200,
            lookback_days=7,
            max_results=100,
            min_pricing_samples=3,
            dry_run=False,
        )
    assert out["analyzed_count"] >= 1


def test_apply_outcome_policy_job_proposes(cal_db, bridge_pkg, monkeypatch):
    jobs = bridge_pkg.learning_jobs
    o = bridge_pkg.learning_outcome
    cal = cal_db
    monkeypatch.setenv("KOL_OUTCOME_LEARNING_MIN_FAILURES", "1")
    monkeypatch.setattr(
        o.learning_llm, "invoke_learning_llm",
        lambda *a, **k: "## Approved outcome learning\n- anchor lower",
    )
    iid = cal.upsert_identity(primary_handle="job_o2", env="LIVE")
    cal.write_event(
        identity_id=iid, campaign_id="C1", event_type="collab_outcome_learning",
        goal=None, lane="meta", actor="t",
        payload={"outcome_class": "failure", "root_cause_tags": ["price_too_high"],
                 "forward_guidance": ["anchor lower"]},
        env="LIVE",
    )
    with cal._connect() as conn:  # type: ignore[attr-defined]
        out = jobs._execute_job(
            conn,
            job_name=jobs.JOB_APPLY_OUTCOME_POLICY,
            env="LIVE",
            triggered_by="test",
            limit=200,
            lookback_days=7,
            max_results=100,
            min_pricing_samples=3,
            dry_run=False,
        )
    assert out.get("pending") is True


def test_overview_includes_outcome_learning(cal_db, bridge_pkg):
    overview_mod = bridge_pkg.learning_overview
    cal = cal_db
    iid = cal.upsert_identity(primary_handle="ov_o1", env="LIVE")
    cal.write_event(
        identity_id=iid, campaign_id="C1", event_type="collab_outcome_learning",
        goal=None, lane="meta", actor="t",
        payload={"outcome_class": "failure"}, env="LIVE",
    )
    with cal._connect() as conn:  # type: ignore[attr-defined]
        out = overview_mod.build_learning_overview(conn, env="LIVE", runs_limit=5)
    assert "outcome_learning" in out
    assert out["outcome_learning"]["total_retros"] >= 1
    assert out["outcome_learning"]["by_class"]["failure"] >= 1
