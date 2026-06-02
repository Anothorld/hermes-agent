"""Aggregate learning events into policy documents (toolized distill steps)."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any, Optional

from . import cal
from . import learning_llm
from . import learning_store
from . import policies as pol


def aggregate_reject_markdown(events: list[dict[str, Any]]) -> str:
    """Build markdown policy body grouped by goal + tag."""
    buckets: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for ev in events:
        payload = ev.get("payload") or {}
        goal = str(payload.get("goal") or "general")
        tags = payload.get("tags") or ["other"]
        note = str(payload.get("note") or "").strip()
        fix = str(payload.get("suggested_fix") or "").strip()
        skill = str(payload.get("child_skill") or "")
        snippet = str(payload.get("agent_body") or "")[:200].replace("\n", " ")
        line_parts = []
        if note:
            line_parts.append(note)
        if fix:
            line_parts.append(f"Fix: {fix}")
        if skill:
            line_parts.append(f"(skill: {skill})")
        if snippet:
            line_parts.append(f'Bad snippet: "{snippet}"')
        line = " — ".join(line_parts) or "Rejected without note"
        for tag in tags:
            buckets[goal][str(tag)].append(line)

    sections: list[str] = ["# Reply learning hints (auto-generated)", ""]
    for goal in sorted(buckets):
        sections.append(f"## {goal}")
        sections.append("")
        for tag in sorted(buckets[goal]):
            sections.append(f"### tag: {tag}")
            seen: set[str] = set()
            for line in buckets[goal][tag][:5]:
                if line in seen:
                    continue
                seen.add(line)
                sections.append(f"- {line}")
            sections.append("")
    return "\n".join(sections).strip() + "\n"


def aggregate_edit_markdown(events: list[dict[str, Any]]) -> str:
    """Build markdown policy body grouped by child skill."""
    by_skill: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ev in events:
        payload = ev.get("payload") or {}
        if not payload.get("was_edited"):
            continue
        skill = str(payload.get("child_skill") or "unknown")
        by_skill[skill].append(payload)

    lines = ["# Edit-pattern learning (auto-generated)", ""]
    for skill in sorted(by_skill):
        lines.append(f"## {skill}")
        lines.append("")
        for item in by_skill[skill][:8]:
            dist = item.get("edit_distance", 0)
            agent = str(item.get("normalized_agent_body") or "")[:120]
            sent = str(item.get("normalized_sent_body") or "")[:120]
            lines.append(f"- edit_distance={dist}: agent `{agent}` → sent `{sent}`")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def apply_reject_policy(
    conn,
    *,
    env: str,
    updated_by: str,
    limit: int = 200,
) -> dict[str, Any]:
    events = learning_store.list_learning_events(
        conn,
        env=env,
        event_types=("draft_rejected_learning",),
        limit=limit,
    )
    md = aggregate_reject_markdown(events)
    row = pol.put_policy(
        conn,
        scope=learning_store.REJECT_LEARNING_SCOPE,
        content_md=md,
        updated_by=updated_by,
        title=f"Reply learning ({env})",
        env=env,
    )
    return {"events": len(events), "version": row.get("version"), "policy_id": row.get("id")}


def _edited_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in events if (e.get("payload") or {}).get("was_edited")]


def list_consumed_edit_event_ids(conn, *, env: str) -> set[int]:
    """Event ids already tied to an approved style-learning approval."""
    rows = conn.execute(
        """SELECT fact_value FROM kol_facts_latest
            WHERE fact_namespace='approval'
              AND fact_key=?
              AND env=?""",
        (learning_store.STYLE_LEARNING_APPROVAL_FACT, env),
    ).fetchall()
    consumed: set[int] = set()
    for row in rows:
        try:
            val = json.loads(row["fact_value"]) if row["fact_value"] else {}
        except (TypeError, ValueError):
            continue
        if not isinstance(val, dict) or val.get("decision") != "approved":
            continue
        for eid in val.get("source_event_ids") or []:
            try:
                consumed.add(int(eid))
            except (TypeError, ValueError):
                continue
    return consumed


def find_pending_style_proposal(
    conn,
    *,
    env: str,
    scope: str,
    owner_user_id: Optional[int] = None,
) -> Optional[dict[str, Any]]:
    """Return latest pending ``approval.style_learning_proposal`` for scope."""
    rows = conn.execute(
        """SELECT identity_id, campaign_id, fact_value, captured_at
             FROM kol_facts_latest
            WHERE fact_namespace='approval'
              AND fact_key=?
              AND env=?
            ORDER BY id DESC""",
        (learning_store.STYLE_LEARNING_APPROVAL_FACT, env),
    ).fetchall()
    for row in rows:
        try:
            val = json.loads(row["fact_value"]) if row["fact_value"] else {}
        except (TypeError, ValueError):
            continue
        if not isinstance(val, dict):
            continue
        if val.get("decision") not in (None, "pending"):
            continue
        if val.get("scope") != scope:
            continue
        prop_owner = val.get("owner_user_id")
        if scope == "user_style" and int(prop_owner or 0) != int(owner_user_id or 0):
            continue
        return {
            "identity_id": row["identity_id"],
            "campaign_id": row["campaign_id"],
            "value": val,
            "captured_at": row["captured_at"],
        }
    return None


def list_pending_style_proposals(
    conn,
    *,
    env: str,
) -> list[dict[str, Any]]:
    """Return all pending ``approval.style_learning_proposal`` rows (newest first)."""
    rows = conn.execute(
        """SELECT identity_id, campaign_id, fact_value, captured_at
             FROM kol_facts_latest
            WHERE fact_namespace='approval'
              AND fact_key=?
              AND env=?
            ORDER BY id DESC""",
        (learning_store.STYLE_LEARNING_APPROVAL_FACT, env),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        try:
            val = json.loads(row["fact_value"]) if row["fact_value"] else {}
        except (TypeError, ValueError):
            continue
        if not isinstance(val, dict):
            continue
        if val.get("decision") not in (None, "pending"):
            continue
        out.append({
            "identity_id": row["identity_id"],
            "campaign_id": row["campaign_id"],
            "value": val,
            "captured_at": row["captured_at"],
            "scope": val.get("scope"),
            "owner_user_id": val.get("owner_user_id"),
        })
    return out


def resolve_learning_anchor_identity_id(
    conn,
    *,
    env: str,
    events: list[dict[str, Any]],
) -> int:
    import os

    raw = os.environ.get("KOL_LEARNING_ANCHOR_IDENTITY_ID", "").strip()
    if raw:
        iid = int(raw)
        if not cal.get_identity(iid):
            raise ValueError(f"KOL_LEARNING_ANCHOR_IDENTITY_ID {iid} not found")
        return iid
    for ev in events:
        iid = ev.get("identity_id")
        if iid is not None and cal.get_identity(int(iid)):
            return int(iid)
    raise ValueError("no identity for style-learning approval anchor")


_STYLE_SECTION_HEADING = "## Proposed style updates"
_STRATEGY_SECTION_HEADING = "## Proposed strategy updates"


def split_style_and_strategy_markdown(md: str) -> tuple[str, str]:
    """Split combined distill output into style + strategy markdown sections."""
    text = md.strip()
    if not text:
        return "", ""
    lower = text.lower()
    style_idx = lower.find(_STYLE_SECTION_HEADING.lower())
    strat_idx = lower.find(_STRATEGY_SECTION_HEADING.lower())
    if style_idx >= 0 and strat_idx >= 0:
        if style_idx < strat_idx:
            style_part = text[style_idx:strat_idx].strip()
            strat_part = text[strat_idx:].strip()
        else:
            strat_part = text[strat_idx:style_idx].strip()
            style_part = text[style_idx:].strip()
        return style_part, strat_part
    if strat_idx >= 0:
        return "", text[strat_idx:].strip()
    if style_idx >= 0:
        return text[style_idx:].strip(), ""
    return text, ""


def aggregate_strategy_markdown(events: list[dict[str, Any]]) -> str:
    """Deterministic fallback: group tactical patterns by goal + child_skill."""
    buckets: dict[str, dict[str, list[str]]] = defaultdict(
        lambda: defaultdict(list),
    )
    for ev in _edited_events(events):
        payload = ev.get("payload") or {}
        goal = str(ev.get("goal") or payload.get("goal") or "general")
        skill = str(payload.get("child_skill") or "unknown")
        dist = payload.get("edit_distance", 0)
        agent = str(payload.get("normalized_agent_body") or "")[:100]
        sent = str(payload.get("normalized_sent_body") or "")[:100]
        line = (
            f"edit_distance={dist}: operator sent `{sent}` instead of agent `{agent}`"
        )
        buckets[goal][skill].append(line)

    lines = [_STRATEGY_SECTION_HEADING, ""]
    for goal in sorted(buckets):
        lines.append(f"## {goal}")
        lines.append("")
        for skill in sorted(buckets[goal]):
            lines.append(f"### {skill}")
            for item in buckets[goal][skill][:4]:
                lines.append(f"- {item}")
            lines.append("")
    return "\n".join(lines).strip() + "\n"


def distill_edit_learning_llm(
    conn,
    events: list[dict[str, Any]],
    *,
    style_scope: str,
    env: str,
) -> tuple[str, str, bool]:
    """LLM-distill style + strategy markdown; fallback to deterministic aggregates."""
    edited = _edited_events(events)
    samples = [
        learning_store.build_style_learning_sample(conn, ev, env=env)
        for ev in edited
    ]
    batch_n = len(samples)
    prompt = (
        "You analyze operator edits to KOL email drafts (agent draft vs Gmail sent body).\n"
        f"Style target scope: {style_scope} (env={env}). Batch size: {batch_n} edits.\n"
        "Each sample includes `edit`, `current_facts`, and `conversation_timeline`.\n\n"
        "Produce TWO markdown sections for operator approval (output ONLY markdown):\n\n"
        f"{_STYLE_SECTION_HEADING}\n"
        "- Subsections per child_skill.\n"
        "- 3–8 bullets: tone, phrasing, length, structure, what NOT to say.\n\n"
        f"{_STRATEGY_SECTION_HEADING}\n"
        "- Subsections per **goal** (e.g. compensation_negotiation, interest_qualification).\n"
        "- 3–8 bullets: sequencing, when to ask/avoid price, barter vs paid, fact order,\n"
        "  escalation triggers implied by edits — tactical playbook, not tone.\n\n"
        "Rules:\n"
        "- Every bullet must cite evidence from diffs and/or thread/facts.\n"
        "- Do NOT invent rules not supported by samples.\n"
        "- End with `### Context notes` (batch size, distinct campaigns).\n\n"
        f"SAMPLES_JSON:\n{json.dumps(samples, indent=2, ensure_ascii=False)}"
    )
    try:
        raw = learning_llm.invoke_learning_llm(prompt)
        md = learning_llm.strip_markdown_fences(raw).strip()
        if not md:
            raise RuntimeError("empty LLM markdown")
        style_md, strategy_md = split_style_and_strategy_markdown(md)
        if not style_md and not strategy_md:
            style_md = md
        return style_md, strategy_md, True
    except Exception:
        import logging

        logging.getLogger(__name__).warning(
            "LLM edit-learning distill failed; using deterministic aggregate",
            exc_info=True,
        )
        return (
            aggregate_edit_markdown(edited),
            aggregate_strategy_markdown(edited),
            False,
        )


def distill_edit_style_llm(
    conn,
    events: list[dict[str, Any]],
    *,
    scope: str,
    env: str,
) -> tuple[str, bool]:
    """Backward-compatible wrapper returning combined markdown + llm flag."""
    style_md, strategy_md, llm_used = distill_edit_learning_llm(
        conn, events, style_scope=scope, env=env,
    )
    combined = style_md
    if strategy_md.strip():
        combined = f"{style_md.rstrip()}\n\n{strategy_md.strip()}\n"
    return combined, llm_used


def merge_strategy_policy_content(current_md: str, proposed_section: str) -> str:
    """Append approved strategy section (goal-oriented, env-scoped policy)."""
    base = (current_md or "").strip()
    section = proposed_section.strip()
    if not section:
        return base
    marker = "## Approved strategy learning"
    if marker not in base:
        tail = f"{marker}\n\n{section}\n"
        return f"{base}\n\n{tail}".strip() if base else tail.strip()
    return f"{base}\n\n{section}\n"


def merge_style_policy_content(current_md: str, proposed_section: str) -> str:
    """Append an approved proposal section to existing policy markdown."""
    base = (current_md or "").strip()
    section = proposed_section.strip()
    if not section:
        return base
    marker = "## Approved style learning"
    if marker not in base:
        tail = f"{marker}\n\n{section}\n"
        return f"{base}\n\n{tail}".strip() if base else tail.strip()
    return f"{base}\n\n{section}\n"


def propose_style_learning_approval(
    conn,
    *,
    env: str,
    scope: str,
    updated_by: str,
    owner_user_id: Optional[int] = None,
    limit: int = 200,
    batch_size: Optional[int] = None,
) -> dict[str, Any]:
    """LLM-distill edit events and open ``approval.style_learning_proposal``."""
    if scope not in learning_store.EDIT_LEARNING_SCOPES:
        raise ValueError(f"scope must be one of {learning_store.EDIT_LEARNING_SCOPES}")
    if scope == "user_style" and owner_user_id is None:
        raise ValueError("user_style requires owner_user_id")

    threshold = (
        batch_size
        if batch_size is not None
        else learning_store.style_learning_batch_size()
    )

    if find_pending_style_proposal(conn, env=env, scope=scope, owner_user_id=owner_user_id):
        return {
            "skipped": True,
            "reason": "pending style_learning_proposal already exists",
            "scope": scope,
        }

    events = learning_store.list_learning_events(
        conn, env=env, event_types=("draft_edit_learning",), limit=limit,
    )
    consumed = list_consumed_edit_event_ids(conn, env=env)
    fresh = [e for e in events if int(e.get("id") or 0) not in consumed]
    edited = _edited_events(fresh)
    edited.sort(key=lambda e: int(e.get("id") or 0))
    if not edited:
        return {
            "skipped": True,
            "reason": "no new edited sent bodies",
            "events_seen": len(events),
        }
    if len(edited) < threshold:
        return {
            "skipped": True,
            "reason": "below_style_learning_batch_threshold",
            "pending_edits": len(edited),
            "batch_threshold": threshold,
            "scope": scope,
        }

    batch = edited[:threshold]
    style_md, strategy_md, llm_used = distill_edit_learning_llm(
        conn, batch, style_scope=scope, env=env,
    )
    anchor_id = resolve_learning_anchor_identity_id(conn, env=env, events=batch)
    event_ids = [int(e["id"]) for e in batch if e.get("id") is not None]
    combined_md = style_md
    if strategy_md.strip():
        combined_md = f"{style_md.rstrip()}\n\n{strategy_md.strip()}\n"

    proposal: dict[str, Any] = {
        "decision": "pending",
        "scope": scope,
        "owner_user_id": owner_user_id,
        "env": env,
        "title": f"Edit learning ({scope} + strategy, {env})",
        "proposed_markdown": combined_md,
        "proposed_style_markdown": style_md,
        "proposed_strategy_markdown": strategy_md,
        "source_event_ids": event_ids,
        "sample_count": len(batch),
        "batch_threshold": threshold,
        "llm_used": llm_used,
        "opened_by": updated_by,
    }
    cal.write_facts(
        identity_id=anchor_id,
        campaign_id=None,
        namespace="approval",
        facts={learning_store.STYLE_LEARNING_APPROVAL_FACT: proposal},
        source=f"learning:propose:{scope}",
        env=env,
    )
    return {
        "approval_fact": learning_store.STYLE_LEARNING_APPROVAL_FACT,
        "identity_id": anchor_id,
        "scope": scope,
        "llm_used": llm_used,
        "sample_count": len(batch),
        "batch_threshold": threshold,
        "remaining_edits": max(0, len(edited) - len(batch)),
        "source_event_ids": event_ids,
        "pending": True,
    }


def apply_approved_style_proposal(
    conn,
    *,
    env: str,
    proposal: dict[str, Any],
    updated_by: str,
) -> dict[str, Any]:
    """Merge approved style + strategy sections into ``policy_documents``."""
    scope = str(proposal.get("scope") or "")
    if scope not in learning_store.EDIT_LEARNING_SCOPES:
        raise ValueError(f"invalid proposal scope: {scope!r}")
    owner_user_id = proposal.get("owner_user_id")
    if scope == "user_style":
        if owner_user_id is None:
            raise ValueError("user_style proposal missing owner_user_id")
        owner_user_id = int(owner_user_id)

    combined = str(proposal.get("proposed_markdown") or "").strip()
    style_md = str(proposal.get("proposed_style_markdown") or "").strip()
    strategy_md = str(proposal.get("proposed_strategy_markdown") or "").strip()
    if not style_md and not strategy_md and combined:
        style_md, strategy_md = split_style_and_strategy_markdown(combined)
    if not style_md and not strategy_md:
        raise ValueError("proposal missing style/strategy markdown")

    style_policy_env = env if scope in pol.ENV_SCOPED_POLICIES else None
    current_style = pol.get_policy(
        conn, scope=scope, owner_user_id=owner_user_id, env=style_policy_env,
    )
    merged_style = merge_style_policy_content(
        (current_style or {}).get("content_md") or "",
        style_md,
    )
    style_row = pol.put_policy(
        conn,
        scope=scope,
        content_md=merged_style,
        updated_by=updated_by,
        title=str(proposal.get("title") or f"Style learning ({scope})"),
        owner_user_id=owner_user_id,
    )

    strategy_result: dict[str, Any] = {"skipped": True, "reason": "no strategy section"}
    if strategy_md.strip():
        current_strat = pol.get_policy(
            conn,
            scope=learning_store.REPLY_STRATEGY_SCOPE,
            env=env,
        )
        merged_strat = merge_strategy_policy_content(
            (current_strat or {}).get("content_md") or "",
            strategy_md,
        )
        strat_row = pol.put_policy(
            conn,
            scope=learning_store.REPLY_STRATEGY_SCOPE,
            content_md=merged_strat,
            updated_by=updated_by,
            title=f"Reply strategy learning ({env})",
            env=env,
        )
        strategy_result = {
            "scope": learning_store.REPLY_STRATEGY_SCOPE,
            "version": strat_row.get("version"),
            "policy_id": strat_row.get("id"),
            "merged_chars": len(merged_strat),
        }

    return {
        "scope": scope,
        "version": style_row.get("version"),
        "policy_id": style_row.get("id"),
        "merged_chars": len(merged_style),
        "strategy_policy": strategy_result,
    }


def apply_edit_policy(
    conn,
    *,
    env: str,
    scope: str,
    updated_by: str,
    owner_user_id: Optional[int] = None,
    limit: int = 200,
) -> dict[str, Any]:
    """Create a pending approval proposal (does not write policy directly)."""
    return propose_style_learning_approval(
        conn,
        env=env,
        scope=scope,
        updated_by=updated_by,
        owner_user_id=owner_user_id,
        limit=limit,
    )


def _num(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def build_pricing_report(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate negotiation history into calibration metrics."""
    counters: list[float] = []
    quotes: list[float] = []
    agreed: list[float] = []
    for rec in records:
        facts = rec.get("facts") or {}
        quote = _num(facts.get("offer.kol_paid_quote"))
        counter = _num(facts.get("offer.latest_counter_amount"))
        req = _num(facts.get("offer.latest_requested_amount"))
        if quote is not None:
            quotes.append(quote)
        if counter is not None and req is not None and req > 0:
            counters.append(counter / req)
        if facts.get("offer.compensation_agreed") is True and counter is not None:
            agreed.append(counter)
    return {
        "sample_size": len(records),
        "avg_quote": round(mean(quotes), 2) if quotes else None,
        "avg_counter_ratio_of_request": round(mean(counters), 3) if counters else None,
        "avg_agreed_counter": round(mean(agreed), 2) if agreed else None,
        "suggested_paid_ratio_override": round(mean(counters), 3) if counters else 0.55,
    }


def apply_pricing_calibration_policy(
    conn,
    *,
    env: str,
    updated_by: str,
    limit: int = 500,
) -> dict[str, Any]:
    records = learning_store.list_negotiation_history(
        conn, env=env, limit=limit,
    )
    report = build_pricing_report(records)
    md = (
        "# Pricing calibration report (auto-generated)\n\n"
        f"```json\n{json.dumps(report, indent=2)}\n```\n"
    )
    row = pol.put_policy(
        conn,
        scope=learning_store.PRICING_CALIBRATION_SCOPE,
        content_md=md,
        updated_by=updated_by,
        title=f"Pricing calibration ({env})",
        env=env,
    )
    return {**report, "version": row.get("version"), "policy_id": row.get("id")}


def failure_examples_path() -> Path:
    """Default path to classifier failure-examples reference doc."""
    return (
        Path(__file__).resolve().parents[2]
        / "skills"
        / "social-media"
        / "kol-email-stage-classifier"
        / "references"
        / "failure-examples.md"
    )


def sync_failure_examples_md(
    conn,
    *,
    env: str,
    limit: int = 30,
    target_path: Optional[Path] = None,
) -> dict[str, Any]:
    """Append LIVE manual-over-email corrections into failure-examples.md."""
    rows = learning_store.list_fact_corrections(conn, env=env, limit=limit)
    path = Path(target_path) if target_path else failure_examples_path()
    if not rows:
        return {"appended": 0, "path": str(path), "skipped": True, "reason": "no corrections"}

    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    blocks: list[str] = []
    appended = 0
    for row in rows:
        key = str(row.get("fact_key") or "")
        marker = f"<!-- auto:{row.get('manual_id')}:{key} -->"
        if marker in existing:
            continue
        email_val = row.get("email_value")
        manual_val = row.get("manual_value")
        blocks.append(
            f"\n{marker}\n"
            f"## Operator correction: `{key}`\n\n"
            f"- identity={row.get('identity_id')} campaign={row.get('campaign_id')}\n"
            f"- classifier wrote: `{email_val}`\n"
            f"- operator corrected: `{manual_val}`\n"
        )
        appended += 1

    if not blocks:
        return {"appended": 0, "path": str(path), "reason": "all corrections already recorded"}

    section = "\n## Auto-synced from LIVE fact corrections\n\n"
    if "## Auto-synced from LIVE fact corrections" not in existing:
        new_body = existing.rstrip() + section + "".join(blocks) + "\n"
    else:
        new_body = existing.rstrip() + "".join(blocks) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new_body, encoding="utf-8")
    return {"appended": appended, "path": str(path), "corrections_seen": len(rows)}


def suggest_campaign_paid_ratio(
    conn,
    *,
    env: str,
    campaign_id: str,
    min_samples: int = 3,
) -> Optional[float]:
    """Return a ratio when enough per-campaign negotiation data exists."""
    records = learning_store.list_negotiation_history(
        conn, env=env, campaign_id=campaign_id, limit=200,
    )
    if len(records) < min_samples:
        return None
    report = build_pricing_report(records)
    return float(report["suggested_paid_ratio_override"])
