"""Tests for scheduled learning jobs + audit store."""

from __future__ import annotations


def test_run_reject_policy_skipped_without_events(cal_db, bridge_pkg):
    jobs = bridge_pkg.learning_jobs
    store = bridge_pkg.learning_job_store
    with cal_db._connect() as conn:  # type: ignore[attr-defined]
        out = jobs.run_single_job(
            conn,
            job_name=jobs.JOB_APPLY_REJECT_POLICY,
            env="TEST",
            triggered_by="test",
            dry_run=False,
        )
    assert out["status"] == store.JOB_STATUS_SKIPPED
    assert out["run_id"]


def test_run_reject_policy_writes_policy(cal_db, bridge_pkg):
    jobs = bridge_pkg.learning_jobs
    iid = cal_db.upsert_identity(primary_handle="lj1", platform="instagram")
    cal_db.write_event(
        identity_id=iid,
        campaign_id="C1",
        event_type="draft_rejected_learning",
        goal="outreach",
        lane="commerce",
        actor="test",
        payload={"tags": ["other"], "note": "bad tone", "goal": "outreach"},
        env="TEST",
    )
    with cal_db._connect() as conn:  # type: ignore[attr-defined]
        out = jobs.run_single_job(
            conn,
            job_name=jobs.JOB_APPLY_REJECT_POLICY,
            env="TEST",
            triggered_by="test",
        )
        pol = bridge_pkg.policies.get_policy(conn, scope="reply_learning", env="TEST")
    assert out["status"] == "ok"
    assert pol and "bad tone" in pol["content_md"]


def test_job_runs_listable(cal_db, bridge_pkg):
    jobs = bridge_pkg.learning_jobs
    store = bridge_pkg.learning_job_store
    with cal_db._connect() as conn:  # type: ignore[attr-defined]
        jobs.run_single_job(
            conn,
            job_name=jobs.JOB_SNAPSHOT_FACT_CORRECTIONS,
            env="TEST",
            triggered_by="test",
        )
        rows = store.list_runs(conn, env="TEST", limit=5)
    assert rows
    assert rows[0]["job_name"] == jobs.JOB_SNAPSHOT_FACT_CORRECTIONS


def test_resolve_suite_nightly(bridge_pkg):
    names = bridge_pkg.learning_jobs.resolve_job_names(suite="nightly")
    assert "apply_reject_policy" in names
    assert "reconcile_sent" not in names


def test_scheduled_jobs_reject_test_env(bridge_pkg):
    jobs = bridge_pkg.learning_jobs
    import pytest

    with pytest.raises(ValueError, match="LIVE"):
        jobs.run_scheduled_jobs(env="TEST", triggered_by="test")


def test_job_alias_auto_pricing_test_campaigns(bridge_pkg):
    names = bridge_pkg.learning_jobs.resolve_job_names(
        jobs=["auto_pricing_test_campaigns"],
    )
    assert names == ["auto_pricing_campaigns"]


def test_sync_failure_examples_appends(cal_db, bridge_pkg, tmp_path):
    jobs = bridge_pkg.learning_jobs
    distill = bridge_pkg.learning_distill
    target = tmp_path / "failure-examples.md"
    target.write_text("# Seed\n", encoding="utf-8")
    iid = cal_db.upsert_identity(primary_handle="fc2", platform="instagram")
    cal_db.write_facts(
        identity_id=iid,
        campaign_id="C1",
        namespace="offer",
        facts={"offer.interest_signal": "confirmed"},
        source="email:classifier",
        env="LIVE",
    )
    cal_db.write_facts(
        identity_id=iid,
        campaign_id="C1",
        namespace="offer",
        facts={"offer.interest_signal": "needs_more_info"},
        source="manual",
        env="LIVE",
    )
    with cal_db._connect() as conn:  # type: ignore[attr-defined]
        out = distill.sync_failure_examples_md(
            conn, env="LIVE", limit=10, target_path=target,
        )
    assert out["appended"] == 1
    text = target.read_text(encoding="utf-8")
    assert "offer.interest_signal" in text


def test_classifier_eval_job_ok(cal_db, bridge_pkg):
    jobs = bridge_pkg.learning_jobs
    cer = bridge_pkg.classifier_eval_runner
    pre = cer.run_deterministic_eval()
    assert pre.get("skipped", 0) > 0, "fixture should include non-sanitize cases"
    with cal_db._connect() as conn:  # type: ignore[attr-defined]
        out = jobs.run_single_job(
            conn,
            job_name=jobs.JOB_CLASSIFIER_EVAL,
            env="LIVE",
            triggered_by="test",
        )
    assert out["status"] == "ok"
    payload = out.get("output") or out
    assert payload.get("failed") == 0


def test_build_pricing_report(bridge_pkg):
    report = bridge_pkg.learning_distill.build_pricing_report([
        {
            "facts": {
                "offer.latest_requested_amount": 1000,
                "offer.latest_counter_amount": 580,
            },
        },
        {
            "facts": {
                "offer.latest_requested_amount": 800,
                "offer.latest_counter_amount": 400,
            },
        },
    ])
    assert report["sample_size"] == 2
    assert report["suggested_paid_ratio_override"] == 0.54


def test_reconcile_stale_running_runs(cal_db, bridge_pkg):
    store = bridge_pkg.learning_job_store
    with cal_db._connect() as conn:  # type: ignore[attr-defined]
        run_id = store.start_run(
            conn,
            job_name="reconcile_sent",
            env="TEST",
            triggered_by="test-stale",
        )
        conn.execute(
            "UPDATE kol_learning_job_runs SET started_at=? WHERE id=?",
            ("2020-01-01T00:00:00+00:00", run_id),
        )
        conn.commit()
        reconciled = store.reconcile_stale_running_runs(
            conn, env="TEST", stale_hours=1,
        )
        row = store.list_runs(conn, env="TEST", limit=1)[0]
    assert len(reconciled) == 1
    assert reconciled[0]["id"] == run_id
    assert row["status"] == store.JOB_STATUS_ERROR
    assert row.get("error_message")
