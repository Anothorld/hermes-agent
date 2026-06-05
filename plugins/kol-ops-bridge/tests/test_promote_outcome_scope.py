"""Stage D: promotion supports outcome_strategy with a distinct reference file."""

from __future__ import annotations

import datetime as _dt


def _seed_outcome_versions(pol, conn, *, goal, n):
    for i in range(n):
        cur = pol.get_policy(conn, scope="outcome_strategy", env="LIVE")
        base = (cur or {}).get("content_md") or ""
        pol.put_policy(
            conn,
            scope="outcome_strategy",
            content_md=f"{base}\n\n## {goal}\n- guidance v{i}\n".strip(),
            updated_by="test",
            env="LIVE",
        )


def test_promote_outcome_strategy_writes_distinct_file(cal_db, bridge_pkg, tmp_path):
    promote = bridge_pkg.learning_promote
    pol = bridge_pkg.policies
    cal = cal_db
    goal = "compensation_negotiation"
    skills_root = tmp_path / "skills"
    with cal._connect() as conn:  # type: ignore[attr-defined]
        _seed_outcome_versions(pol, conn, goal=goal, n=3)
        # Force first-seen old enough.
        out = promote.promote_strategy_to_skill(
            conn,
            env="LIVE",
            goal=goal,
            scope="outcome_strategy",
            min_approvals=2,
            min_age_days=0,
            skills_root=skills_root,
            dry_run=False,
            now=_dt.datetime.now(_dt.timezone.utc),
        )
    assert out["target_path"].endswith(f"{goal}.outcome.md")
    assert out["written"] is True


def test_promote_default_scope_unchanged(cal_db, bridge_pkg, tmp_path):
    promote = bridge_pkg.learning_promote
    pol = bridge_pkg.policies
    cal = cal_db
    goal = "interest_qualification"
    with cal._connect() as conn:  # type: ignore[attr-defined]
        for i in range(2):
            cur = pol.get_policy(conn, scope="reply_strategy", env="LIVE")
            base = (cur or {}).get("content_md") or ""
            pol.put_policy(
                conn, scope="reply_strategy",
                content_md=f"{base}\n\n## {goal}\n- v{i}\n".strip(),
                updated_by="t", env="LIVE",
            )
        out = promote.promote_strategy_to_skill(
            conn, env="LIVE", goal=goal, min_approvals=2, min_age_days=0,
            skills_root=tmp_path / "skills", dry_run=True,
        )
    # Default scope still writes plain <goal>.md
    assert out["target_path"].endswith(f"{goal}.md")
    assert not out["target_path"].endswith(".outcome.md")
