"""Promote stabilized ``reply_strategy`` learning into skill references.

This is the **human-triggered** half of the learning feedback loop. Runtime
hints (``learning_hints``) inject the latest approved ``reply_strategy`` policy
on every dispatch; promotion takes a goal's strategy section that has been
**approved repeatedly and stayed stable** and writes it as an advisory
playbook file under the owning skill's ``references/learned/<goal>.md``.

Design constraints (see ``.cursor/rules/agent-prj-guardrails.mdc``):

* Deterministic + toolized — never an ad-hoc script.
* Only writes inside ``hermes-agent/skills/**`` (allowed scope).
* Promoted content is **advisory**; the skill's HARD rules, fact ownership,
  pricing engine, and escalation gates always win on conflict.
* Eligibility gated by ``min_approvals`` + ``min_age_days`` to suppress noise.
* Every ``--apply`` is audited in ``kol_learning_job_runs`` (job
  ``promote_strategy``) and requires ``sync skills`` afterwards.
"""

from __future__ import annotations

import datetime as _dt
import sqlite3
from pathlib import Path
from typing import Any, Final, Optional

from . import dispatch_router
from . import learning_job_store as job_store
from . import learning_store

PROMOTE_JOB_NAME: Final[str] = "promote_strategy"
LEARNED_REL_DIR: Final[str] = "references/learned"
DEFAULT_MIN_APPROVALS: Final[int] = 2
DEFAULT_MIN_AGE_DAYS: Final[int] = 7

# Goals that own a drafting skill and may receive a promoted playbook.
PROMOTABLE_GOALS: Final[tuple[str, ...]] = (
    "interest_qualification",
    "product_selection",
    "deliverables_scope",
    "compensation_negotiation",
)

_AUTO_HEADER = "<!-- AUTO-PROMOTED"


class PromoteError(ValueError):
    """Raised for invalid promote requests (unknown goal, no skill, etc.)."""


def default_skills_root() -> Path:
    """Absolute path to ``hermes-agent/skills/social-media``."""
    return Path(__file__).resolve().parents[2] / "skills" / "social-media"


def goal_to_skill(goal: str) -> str:
    """Resolve the owning drafting skill for a strategy goal."""
    skill = dispatch_router.GOAL_SKILL.get(goal)
    if not skill:
        raise PromoteError(f"no drafting skill mapped for goal {goal!r}")
    return skill


def _now(now: Optional[_dt.datetime] = None) -> _dt.datetime:
    return now or _dt.datetime.now(_dt.timezone.utc)


def _parse_ts(raw: Any) -> Optional[_dt.datetime]:
    if not raw:
        return None
    try:
        dt = _dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt


PROMOTABLE_SCOPES: Final[tuple[str, ...]] = (
    learning_store.REPLY_STRATEGY_SCOPE,
    learning_store.OUTCOME_STRATEGY_SCOPE,
)


def _ref_filename(goal: str, scope: str) -> str:
    """Distinct learned-reference filename per source scope (avoid collisions)."""
    if scope == learning_store.OUTCOME_STRATEGY_SCOPE:
        return f"{goal}.outcome.md"
    return f"{goal}.md"


def _strategy_versions(
    conn: sqlite3.Connection,
    *,
    env: str,
    scope: str = learning_store.REPLY_STRATEGY_SCOPE,
) -> list[dict[str, Any]]:
    """All policy versions for ``scope`` (oldest first) with content."""
    rows = conn.execute(
        """SELECT version, updated_at, content_md
             FROM policy_documents
            WHERE scope=? AND owner_user_id IS NULL
              AND COALESCE(env, 'LIVE')=?
            ORDER BY version ASC""",
        (scope, env),
    ).fetchall()
    return [dict(r) for r in rows]


def _goal_section_body(content_md: str, goal: str) -> str:
    """Return the merged ``## <goal>`` section body (header stripped)."""
    sliced = learning_store.slice_policy_md_for_goals(content_md, [goal])
    lines = sliced.splitlines()
    body: list[str] = []
    capture = False
    for line in lines:
        stripped = line.strip()
        if stripped.lower() == f"## {goal}".lower():
            capture = True
            continue
        if capture:
            body.append(line)
    return "\n".join(body).strip()


def select_promotable_strategy(
    conn: sqlite3.Connection,
    *,
    env: str,
    goal: str,
    min_approvals: int = DEFAULT_MIN_APPROVALS,
    min_age_days: int = DEFAULT_MIN_AGE_DAYS,
    scope: str = learning_store.REPLY_STRATEGY_SCOPE,
    now: Optional[_dt.datetime] = None,
) -> dict[str, Any]:
    """Assess whether a goal's strategy section is stable enough to promote.

    Eligibility = the ``## <goal>`` section appears in at least
    ``min_approvals`` ``reply_strategy`` versions **and** first appeared at
    least ``min_age_days`` ago. Approvals are inferred from policy history
    (each Console approval that included this goal appends a section, bumping
    the version).

    Args:
        conn: Open CAL connection.
        env: ``LIVE`` (TEST allowed for tests).
        goal: Strategy goal name (must map to a drafting skill).
        min_approvals: Minimum distinct approved versions containing the goal.
        min_age_days: Minimum age (days) of the earliest approval.
        now: Injected clock for tests.

    Returns:
        ``{goal, skill, eligible, reason, approvals, age_days, first_seen,
        latest_version, section_md}``.
    """
    if goal not in PROMOTABLE_GOALS:
        raise PromoteError(
            f"goal {goal!r} is not promotable; choose from {PROMOTABLE_GOALS}",
        )
    skill = goal_to_skill(goal)
    versions = _strategy_versions(conn, env=env, scope=scope)
    with_goal = [
        v for v in versions if f"## {goal}".lower() in (v.get("content_md") or "").lower()
    ]
    approvals = len(with_goal)
    first_seen_dt = _parse_ts(with_goal[0]["updated_at"]) if with_goal else None
    age_days = (
        (_now(now) - first_seen_dt).days if first_seen_dt is not None else 0
    )
    latest_content = versions[-1]["content_md"] if versions else ""
    section_md = _goal_section_body(latest_content, goal)

    base = {
        "goal": goal,
        "skill": skill,
        "approvals": approvals,
        "age_days": age_days,
        "first_seen": with_goal[0]["updated_at"] if with_goal else None,
        "latest_version": versions[-1]["version"] if versions else None,
        "section_md": section_md,
    }
    if not section_md:
        return {**base, "eligible": False, "reason": "no_strategy_section"}
    if approvals < min_approvals:
        return {
            **base,
            "eligible": False,
            "reason": f"below_min_approvals ({approvals}<{min_approvals})",
        }
    if age_days < min_age_days:
        return {
            **base,
            "eligible": False,
            "reason": f"below_min_age_days ({age_days}<{min_age_days})",
        }
    return {**base, "eligible": True, "reason": "eligible"}


def render_learned_reference_md(
    *,
    goal: str,
    env: str,
    section_md: str,
    approvals: int,
    first_seen: Optional[str],
    latest_version: Optional[int],
    scope: str = learning_store.REPLY_STRATEGY_SCOPE,
    now: Optional[_dt.datetime] = None,
) -> str:
    """Render the advisory playbook markdown for ``references/learned/<goal>.md``."""
    stamp = _now(now).date().isoformat()
    source_label = (
        "outcome_strategy (collaboration retrospectives)"
        if scope == learning_store.OUTCOME_STRATEGY_SCOPE
        else "reply_strategy (operator edit learning)"
    )
    intro = (
        "> Auto-promoted from repeatedly-approved collaboration outcome guidance."
        if scope == learning_store.OUTCOME_STRATEGY_SCOPE
        else "> Auto-promoted from repeatedly-approved operator edits."
    )
    header = (
        f"{_AUTO_HEADER} — do not edit by hand.\n"
        f"     Source: {source_label} (env={env}, version={latest_version}).\n"
        f"     Approvals={approvals} · first_approved={first_seen} · promoted={stamp}.\n"
        "     Retract by deleting this file + running `sync skills`. -->"
    )
    return (
        f"{header}\n\n"
        f"# Learned playbook — {goal} (advisory)\n\n"
        f"{intro} **Advisory only.** This skill's HARD rules, fact ownership, "
        "the pricing engine, and escalation gates always win on conflict. "
        "Never use a line below to invent facts/numbers or bypass a gate.\n\n"
        f"{section_md.strip()}\n"
    )


def learned_reference_path(
    skills_root: Path,
    *,
    skill: str,
    goal: str,
    scope: str = learning_store.REPLY_STRATEGY_SCOPE,
) -> Path:
    return skills_root / skill / LEARNED_REL_DIR / _ref_filename(goal, scope)


def promote_strategy_to_skill(
    conn: sqlite3.Connection,
    *,
    env: str,
    goal: str,
    min_approvals: int = DEFAULT_MIN_APPROVALS,
    min_age_days: int = DEFAULT_MIN_AGE_DAYS,
    scope: str = learning_store.REPLY_STRATEGY_SCOPE,
    skills_root: Optional[Path] = None,
    dry_run: bool = True,
    triggered_by: str = "kol_bridge_tool:promote-strategy",
    now: Optional[_dt.datetime] = None,
) -> dict[str, Any]:
    """Promote a stabilized strategy section into the owning skill (audited).

    ``scope`` selects the source policy: ``reply_strategy`` (default) or
    ``outcome_strategy`` (collaboration-outcome guidance, written to a distinct
    ``<goal>.outcome.md`` reference).

    When ``dry_run`` is True (default), returns the proposed markdown + target
    path without writing and without an audit row. When False, writes the file
    and records a ``promote_strategy`` row in ``kol_learning_job_runs``.
    """
    if scope not in PROMOTABLE_SCOPES:
        raise PromoteError(f"scope {scope!r} not promotable; choose {PROMOTABLE_SCOPES}")
    root = skills_root or default_skills_root()
    assessment = select_promotable_strategy(
        conn,
        env=env,
        goal=goal,
        min_approvals=min_approvals,
        min_age_days=min_age_days,
        scope=scope,
        now=now,
    )
    target = learned_reference_path(
        root, skill=assessment["skill"], goal=goal, scope=scope,
    )
    rel_target = str(target)
    rendered = ""
    if assessment["section_md"]:
        rendered = render_learned_reference_md(
            goal=goal,
            env=env,
            section_md=assessment["section_md"],
            approvals=assessment["approvals"],
            first_seen=assessment["first_seen"],
            latest_version=assessment["latest_version"],
            scope=scope,
            now=now,
        )
    existing = target.read_text(encoding="utf-8") if target.exists() else ""
    changed = rendered.strip() != existing.strip()

    result = {
        "goal": goal,
        "skill": assessment["skill"],
        "env": env,
        "target_path": rel_target,
        "eligible": assessment["eligible"],
        "reason": assessment["reason"],
        "approvals": assessment["approvals"],
        "age_days": assessment["age_days"],
        "changed": changed,
        "proposed_markdown": rendered,
    }

    if dry_run:
        result["dry_run"] = True
        result["needs_sync_skills"] = False
        return result

    if not assessment["eligible"]:
        raise PromoteError(
            f"goal {goal!r} not promotable: {assessment['reason']}",
        )

    run_id = job_store.start_run(
        conn,
        job_name=PROMOTE_JOB_NAME,
        env=env,
        triggered_by=triggered_by,
        input_payload={
            "goal": goal,
            "min_approvals": min_approvals,
            "min_age_days": min_age_days,
        },
    )
    started_at_row = conn.execute(
        "SELECT started_at FROM kol_learning_job_runs WHERE id=?", (run_id,),
    ).fetchone()
    started_at = started_at_row["started_at"] if started_at_row else None
    try:
        if changed:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(rendered, encoding="utf-8")
        status = (
            job_store.JOB_STATUS_OK if changed else job_store.JOB_STATUS_SKIPPED
        )
        finished = job_store.finish_run(
            conn,
            run_id,
            status=status,
            output={
                "goal": goal,
                "skill": assessment["skill"],
                "target_path": rel_target,
                "changed": changed,
                "approvals": assessment["approvals"],
            },
            started_at=started_at,
        )
    except OSError as exc:
        finished = job_store.finish_run(
            conn,
            run_id,
            status=job_store.JOB_STATUS_ERROR,
            output={"goal": goal, "target_path": rel_target},
            error_message=str(exc),
            started_at=started_at,
        )
        result["run"] = finished
        raise PromoteError(f"failed to write {rel_target}: {exc}") from exc

    result["dry_run"] = False
    result["written"] = changed
    result["needs_sync_skills"] = changed
    result["run"] = finished
    return result


__all__ = [
    "DEFAULT_MIN_AGE_DAYS",
    "DEFAULT_MIN_APPROVALS",
    "LEARNED_REL_DIR",
    "PROMOTABLE_GOALS",
    "PROMOTABLE_SCOPES",
    "PROMOTE_JOB_NAME",
    "PromoteError",
    "default_skills_root",
    "goal_to_skill",
    "learned_reference_path",
    "promote_strategy_to_skill",
    "render_learned_reference_md",
    "select_promotable_strategy",
]
