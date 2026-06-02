"""Promote stabilized reply_strategy sections into skill references."""

from __future__ import annotations

import datetime as _dt

import pytest


def _put_strategy(bridge_pkg, conn, content_md):
    bridge_pkg.policies.put_policy(
        conn,
        scope="reply_strategy",
        content_md=content_md,
        updated_by="test:approve",
        env="LIVE",
    )


_SECTION = (
    "## Approved strategy learning\n\n"
    "## compensation_negotiation\n\n"
    "- Clarify deliverables before discussing any cash supplement.\n"
)
_FUTURE = _dt.datetime(2030, 1, 1, tzinfo=_dt.timezone.utc)


def test_unknown_goal_rejected(cal_db, bridge_pkg):
    promote = bridge_pkg.learning_promote
    with cal_db._connect() as conn:  # type: ignore[attr-defined]
        with pytest.raises(promote.PromoteError):
            promote.select_promotable_strategy(
                conn, env="LIVE", goal="not_a_goal",
            )


def test_below_min_approvals_not_eligible(cal_db, bridge_pkg):
    promote = bridge_pkg.learning_promote
    with cal_db._connect() as conn:  # type: ignore[attr-defined]
        _put_strategy(bridge_pkg, conn, _SECTION)
        out = promote.select_promotable_strategy(
            conn, env="LIVE", goal="compensation_negotiation",
            min_approvals=2, min_age_days=0, now=_FUTURE,
        )
    assert out["eligible"] is False
    assert "below_min_approvals" in out["reason"]
    assert out["approvals"] == 1


def test_no_section_not_eligible(cal_db, bridge_pkg):
    promote = bridge_pkg.learning_promote
    with cal_db._connect() as conn:  # type: ignore[attr-defined]
        _put_strategy(bridge_pkg, conn, "## interest_qualification\n\n- foo\n")
        out = promote.select_promotable_strategy(
            conn, env="LIVE", goal="compensation_negotiation", now=_FUTURE,
        )
    assert out["eligible"] is False
    assert out["reason"] == "no_strategy_section"


def test_eligible_when_stable(cal_db, bridge_pkg):
    promote = bridge_pkg.learning_promote
    with cal_db._connect() as conn:  # type: ignore[attr-defined]
        _put_strategy(bridge_pkg, conn, _SECTION)
        _put_strategy(bridge_pkg, conn, _SECTION + "- Anchor low on first counter.\n")
        out = promote.select_promotable_strategy(
            conn, env="LIVE", goal="compensation_negotiation",
            min_approvals=2, min_age_days=0, now=_FUTURE,
        )
    assert out["eligible"] is True
    assert out["approvals"] == 2
    assert out["skill"] == "kol-compensation-negotiator"
    assert "deliverables" in out["section_md"]


def test_dry_run_does_not_write(cal_db, bridge_pkg, tmp_path):
    promote = bridge_pkg.learning_promote
    with cal_db._connect() as conn:  # type: ignore[attr-defined]
        _put_strategy(bridge_pkg, conn, _SECTION)
        _put_strategy(bridge_pkg, conn, _SECTION)
        out = promote.promote_strategy_to_skill(
            conn, env="LIVE", goal="compensation_negotiation",
            min_approvals=2, min_age_days=0,
            skills_root=tmp_path, dry_run=True, now=_FUTURE,
        )
    assert out["dry_run"] is True
    assert out["eligible"] is True
    assert "Advisory only" in out["proposed_markdown"]
    target = tmp_path / "kol-compensation-negotiator" / "references" / "learned" / "compensation_negotiation.md"
    assert not target.exists()


def test_apply_writes_file_and_audits(cal_db, bridge_pkg, tmp_path):
    promote = bridge_pkg.learning_promote
    with cal_db._connect() as conn:  # type: ignore[attr-defined]
        _put_strategy(bridge_pkg, conn, _SECTION)
        _put_strategy(bridge_pkg, conn, _SECTION)
        out = promote.promote_strategy_to_skill(
            conn, env="LIVE", goal="compensation_negotiation",
            min_approvals=2, min_age_days=0,
            skills_root=tmp_path, dry_run=False, now=_FUTURE,
        )
    assert out["written"] is True
    assert out["needs_sync_skills"] is True
    target = tmp_path / "kol-compensation-negotiator" / "references" / "learned" / "compensation_negotiation.md"
    assert target.exists()
    body = target.read_text(encoding="utf-8")
    assert "AUTO-PROMOTED" in body
    assert "deliverables" in body
    with cal_db._connect() as conn:  # type: ignore[attr-defined]
        runs = bridge_pkg.learning_job_store.list_runs(
            conn, env="LIVE", job_name="promote_strategy",
        )
    assert runs and runs[0]["status"] == "ok"


def test_apply_not_eligible_raises(cal_db, bridge_pkg, tmp_path):
    promote = bridge_pkg.learning_promote
    with cal_db._connect() as conn:  # type: ignore[attr-defined]
        _put_strategy(bridge_pkg, conn, _SECTION)
        with pytest.raises(promote.PromoteError):
            promote.promote_strategy_to_skill(
                conn, env="LIVE", goal="compensation_negotiation",
                min_approvals=5, min_age_days=0,
                skills_root=tmp_path, dry_run=False, now=_FUTURE,
            )
