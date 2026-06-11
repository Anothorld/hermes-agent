"""Aggregate learning events into policy documents (toolized distill steps)."""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any, Final, Optional

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


def list_edit_operator_ids(conn, *, env: str, limit: int = 500) -> list[int]:
    """Distinct ``operator_user_id`` among unconsumed edited events (newest-first).

    Used by the ``user_style`` job to propose per operator instead of relying on
    a single ``KOL_LEARNING_USER_STYLE_OWNER_ID``.
    """
    events = learning_store.list_learning_events(
        conn, env=env, event_types=("draft_edit_learning",), limit=limit,
    )
    consumed = list_consumed_edit_event_ids(conn, env=env)
    ordered: list[int] = []
    seen: set[int] = set()
    for ev in events:
        if int(ev.get("id") or 0) in consumed:
            continue
        payload = ev.get("payload") or {}
        if not payload.get("was_edited"):
            continue
        raw = payload.get("operator_user_id")
        if raw is None:
            continue
        try:
            oid = int(raw)
        except (TypeError, ValueError):
            continue
        if oid > 0 and oid not in seen:
            seen.add(oid)
            ordered.append(oid)
    return ordered


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


def pending_style_reserved_event_ids(
    conn,
    *,
    env: str,
) -> set[int]:
    """Event ids tied to pending (not yet approved) style-learning proposals.

    These edits are already in a distill batch awaiting approval; they should
    not count toward the next batch progress bar.
    """
    reserved: set[int] = set()
    for row in list_pending_style_proposals(conn, env=env):
        val = row.get("value") or {}
        for raw in val.get("source_event_ids") or []:
            try:
                reserved.add(int(raw))
            except (TypeError, ValueError):
                continue
    return reserved


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
_CONTEXT_NOTES_HEADING = "### Context notes"
# Operator-only batch summary headings (approval UI — never merge into policy).
_CONTEXT_NOTE_HEADINGS: Final[tuple[str, ...]] = (
    _CONTEXT_NOTES_HEADING,
    "### 背景说明",
)
_NO_NEW_RULES_MARKERS = (
    "no new style rules",
    "no new strategy rules",
    "does not provide sufficient evidence",
    "insufficient evidence",
)


def _context_notes_cut_index(text: str) -> int:
    """Index of the earliest operator-only context heading, or -1."""
    lower = text.lower()
    indices: list[int] = []
    for heading in _CONTEXT_NOTE_HEADINGS:
        if heading.isascii():
            idx = lower.find(heading.lower())
        else:
            idx = text.find(heading)
        if idx >= 0:
            indices.append(idx)
    return min(indices) if indices else -1


def strip_proposal_context_notes(md: str) -> str:
    """Remove operator batch summary (``Context notes`` / ``背景说明``) — approval UI only."""
    text = (md or "").strip()
    if not text:
        return ""
    idx = _context_notes_cut_index(text)
    if idx >= 0:
        text = text[:idx].rstrip()
    return text


def _strip_distill_section_heading(md: str) -> str:
    text = (md or "").strip()
    for heading in (_STYLE_SECTION_HEADING, _STRATEGY_SECTION_HEADING):
        if text.lower().startswith(heading.lower()):
            text = text[len(heading) :].lstrip("\n").strip()
    return text


def proposal_section_for_policy_merge(md: str) -> str:
    """Markdown body merged into policy (no Context notes or distill headings)."""
    return _strip_distill_section_heading(strip_proposal_context_notes(md))


def is_actionable_policy_delta(md: str) -> bool:
    """True when the section has at least one rule bullet worth merging."""
    body = proposal_section_for_policy_merge(md)
    if not body:
        return False
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped.startswith(("-", "*")):
            continue
        lower = stripped.lower()
        if re.search(r"\b(remove|adjust):", lower):
            return True
        if any(marker in lower for marker in _NO_NEW_RULES_MARKERS):
            continue
        return True
    return False


_ADJUST_SPLIT = re.compile(r"\s*[→]|->")


def _normalize_bullet_text(line: str) -> str:
    s = line.strip()
    if s.startswith(("-", "*")):
        s = s[1:].strip()
    s = re.sub(r"^\*\*|\*\*$", "", s).strip()
    s = re.sub(r"^(REMOVE|ADJUST):\s*", "", s, flags=re.IGNORECASE).strip()
    return re.sub(r"\s+", " ", s).lower()


def _parse_bullet_directive(line: str) -> tuple[str, str]:
    """Return kind ``add|adjust|remove`` and payload (text after directive)."""
    raw = line.strip()
    if raw.startswith(("-", "*")):
        raw = raw[1:].strip()
    raw = re.sub(r"^\*\*|\*\*$", "", raw).strip()
    m = re.match(r"^(REMOVE|ADJUST):\s*(.*)$", raw, re.IGNORECASE | re.DOTALL)
    if m:
        return m.group(1).lower(), m.group(2).strip()
    return "add", raw


def _adjust_target_and_replacement(payload: str) -> tuple[str, str]:
    parts = _ADJUST_SPLIT.split(payload, maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return payload.strip(), payload.strip()


def _match_needle(bullet: str, needle: str) -> bool:
    if not needle:
        return False
    nb = _normalize_bullet_text(bullet)
    nn = _normalize_bullet_text(needle)
    if not nn:
        return False
    if nn in nb or nb in nn:
        return True
    n_words = {w for w in re.findall(r"[a-z0-9']+", nn) if len(w) > 3}
    b_words = {w for w in re.findall(r"[a-z0-9']+", nb) if len(w) > 3}
    if not n_words:
        return False
    return len(n_words & b_words) / len(n_words) >= 0.5


def _is_no_op_bullet_line(line: str) -> bool:
    return any(marker in line.lower() for marker in _NO_NEW_RULES_MARKERS)


def _parse_md_sections(md: str) -> list[tuple[str | None, list[str]]]:
    sections: list[tuple[str | None, list[str]]] = []
    heading: str | None = None
    lines: list[str] = []
    for raw in md.splitlines():
        stripped = raw.strip()
        if stripped.startswith("#"):
            if lines or heading is not None:
                sections.append((heading, lines))
            heading = stripped
            lines = []
        else:
            lines.append(raw.rstrip())
    sections.append((heading, lines))
    return sections


def apply_policy_delta_patch(existing_block: str, delta: str) -> str:
    """Apply an approved delta onto an existing approved block.

    - ``REMOVE:`` drops matching bullets from the existing block
    - ``ADJUST: old → new`` replaces matching bullets
    - other bullets append (deduped); new ``###`` sections append when absent
    """
    existing_block = (existing_block or "").strip()
    delta = (delta or "").strip()
    if not delta:
        return existing_block
    if not existing_block:
        return delta

    prose_lines: list[str] = []
    bullets: list[str] = []
    for line in existing_block.splitlines():
        if line.strip().startswith(("-", "*")):
            bullets.append(line.rstrip())
        else:
            prose_lines.append(line.rstrip())

    existing_headings = {
        ln.strip() for ln in existing_block.splitlines() if ln.strip().startswith("#")
    }
    extra_sections: list[str] = []

    for heading, lines in _parse_md_sections(delta):
        sec_bullets = [ln.rstrip() for ln in lines if ln.strip().startswith(("-", "*"))]
        if heading and heading not in existing_headings:
            actionable = [b for b in sec_bullets if not _is_no_op_bullet_line(b)]
            if actionable:
                extra_sections.append("\n".join([heading, *actionable]))
            continue
        for bullet in sec_bullets:
            if _is_no_op_bullet_line(bullet):
                continue
            kind, payload = _parse_bullet_directive(bullet)
            if kind == "remove":
                bullets = [b for b in bullets if not _match_needle(b, payload)]
            elif kind == "adjust":
                target, replacement = _adjust_target_and_replacement(payload)
                new_line = bullet
                if replacement and replacement != target:
                    new_line = f"- {replacement}"
                replaced = False
                for i, b in enumerate(bullets):
                    if _match_needle(b, target):
                        bullets[i] = new_line
                        replaced = True
                        break
                if not replaced:
                    bullets.append(new_line)
            elif not any(
                _normalize_bullet_text(b) == _normalize_bullet_text(bullet) for b in bullets
            ):
                bullets.append(bullet)

    out: list[str] = [ln for ln in prose_lines if ln]
    if bullets:
        if out:
            out.append("")
        out.extend(bullets)
    for block in extra_sections:
        out.append("")
        out.extend(block.splitlines())

    text = "\n".join(out).strip()
    return f"{text}\n" if text else ""


def sanitize_stored_policy_learning_metadata(
    conn,
    *,
    scope: str,
    updated_by: str,
    env: Optional[str] = None,
    owner_user_id: Optional[int] = None,
) -> dict[str, Any]:
    """Strip Context notes / distill headings from a stored policy (maintenance)."""
    row = pol.get_policy(
        conn, scope=scope, env=env, owner_user_id=owner_user_id,
    )
    if not row:
        return {"skipped": True, "reason": "no_policy", "scope": scope}
    content = (row.get("content_md") or "").strip()
    if not content:
        return {"skipped": True, "reason": "empty", "scope": scope}

    marker = _approved_block_marker(scope=scope)
    had_context_notes = _context_notes_cut_index(content) >= 0
    if marker in content:
        head, block = content.split(marker, 1)
        head = head.rstrip()
        block_clean = proposal_section_for_policy_merge(block)
        if block_clean.strip():
            cleaned = f"{head}\n\n{marker}\n\n{block_clean}".strip() + "\n"
        elif head.strip():
            cleaned = f"{head.strip()}\n"
        else:
            cleaned = ""
    else:
        cleaned = proposal_section_for_policy_merge(content)
        cleaned = f"{cleaned.strip()}\n" if cleaned.strip() else ""

    if cleaned.strip() == content.strip():
        return {
            "skipped": True,
            "reason": "already_clean",
            "scope": scope,
            "had_context_notes": had_context_notes,
        }

    new_row = pol.put_policy(
        conn,
        scope=scope,
        content_md=cleaned,
        updated_by=updated_by,
        title=str(row.get("title") or scope),
        env=env,
        owner_user_id=owner_user_id,
    )
    return {
        "scope": scope,
        "version": new_row.get("version"),
        "policy_id": new_row.get("id"),
        "removed_chars": len(content) - len(cleaned),
        "had_context_notes": had_context_notes,
    }


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


def _current_policy_baseline(
    conn,
    *,
    style_scope: str,
    env: str,
    owner_user_id: Optional[int],
    max_chars: int = 2000,
) -> tuple[str, str]:
    """Return (current_style_md, current_strategy_md) trimmed for the distill prompt."""
    style_env = env if style_scope in pol.ENV_SCOPED_POLICIES else None
    try:
        style_row = pol.get_policy(
            conn, scope=style_scope, owner_user_id=owner_user_id, env=style_env,
        )
    except Exception:
        style_row = None
    try:
        strat_row = pol.get_policy(
            conn, scope=learning_store.REPLY_STRATEGY_SCOPE, env=env,
        )
    except Exception:
        strat_row = None
    style_md = ((style_row or {}).get("content_md") or "").strip()[:max_chars]
    strat_md = ((strat_row or {}).get("content_md") or "").strip()[:max_chars]
    return style_md, strat_md


def _recent_rejection_feedback_block(
    conn,
    *,
    env: str,
    scope: str,
    owner_user_id: Optional[int],
    limit: int = 5,
) -> str:
    """Summarize recent operator rejections of style proposals for the prompt.

    Closes the feedback loop: the model is told what operators previously
    rejected (and why) so it avoids repeating the same suggestion.
    """
    try:
        events = learning_store.list_learning_events(
            conn, env=env, event_types=("style_proposal_rejected",), limit=limit,
        )
    except Exception:
        return ""
    lines: list[str] = []
    for ev in events:
        payload = ev.get("payload") or {}
        if str(payload.get("scope") or "") not in ("", scope):
            continue
        if scope == "user_style" and owner_user_id is not None:
            ev_owner = payload.get("owner_user_id")
            if ev_owner is not None and int(ev_owner) != int(owner_user_id):
                continue
        note = str(payload.get("note") or "").strip()
        tags = payload.get("tags") or []
        if note or tags:
            tag_str = f" [{', '.join(tags)}]" if tags else ""
            lines.append(f"- {note or '(no note)'}{tag_str}")
    if not lines:
        return ""
    return (
        "PREVIOUSLY REJECTED SUGGESTIONS (do NOT repeat these; address the reason):\n"
        + "\n".join(lines[:limit])
        + "\n\n"
    )


def distill_edit_learning_llm(
    conn,
    events: list[dict[str, Any]],
    *,
    style_scope: str,
    env: str,
    owner_user_id: Optional[int] = None,
) -> tuple[str, str, bool]:
    """LLM-distill style + strategy markdown; fallback to deterministic aggregates.

    The prompt includes the CURRENT approved guidelines as a baseline and asks
    the model for INCREMENTAL revisions (delta), so each approved batch refines
    rather than rewrites the policy — the system converges on operator intent.
    """
    edited = _edited_events(events)
    samples = [
        learning_store.build_style_learning_sample(conn, ev, env=env)
        for ev in edited
    ]
    batch_n = len(samples)
    cur_style_md, cur_strat_md = _current_policy_baseline(
        conn, style_scope=style_scope, env=env, owner_user_id=owner_user_id,
    )
    baseline_block = (
        "CURRENT APPROVED GUIDELINES (baseline — refine these, do NOT restate verbatim):\n\n"
        f"### Current {style_scope}\n{cur_style_md or '(none yet)'}\n\n"
        f"### Current reply_strategy\n{cur_strat_md or '(none yet)'}\n\n"
    )
    rejected_block = _recent_rejection_feedback_block(
        conn, env=env, scope=style_scope, owner_user_id=owner_user_id,
    )
    prompt = (
        "You analyze operator edits to KOL email drafts (agent draft vs Gmail sent body).\n"
        f"Style target scope: {style_scope} (env={env}). Batch size: {batch_n} edits.\n"
        "Each sample includes `edit`, `current_facts`, and `conversation_timeline`.\n\n"
        f"{baseline_block}"
        f"{rejected_block}"
        "Produce TWO markdown sections of INCREMENTAL revisions for operator approval "
        "(output ONLY markdown):\n\n"
        f"{_STYLE_SECTION_HEADING}\n"
        "- Subsections per child_skill.\n"
        "- 3–8 bullets: tone, phrasing, length, structure, what NOT to say.\n\n"
        f"{_STRATEGY_SECTION_HEADING}\n"
        "- Subsections per **goal** (e.g. compensation_negotiation, interest_qualification).\n"
        "- 3–8 bullets: sequencing, when to ask/avoid price, barter vs paid, fact order,\n"
        "  escalation triggers implied by edits — tactical playbook, not tone.\n\n"
        "Rules:\n"
        "- Output only the DELTA vs the baseline: new rules, adjustments, or removals.\n"
        "- Do NOT repeat baseline rules that the samples leave unchanged.\n"
        "- On approval, deltas are merged into policy"
        f" ({'LLM intelligent merge' if _merge_mode() == 'llm_compress' else 'deterministic patch'}): "
        "new bullets append, `ADJUST: old → new` replaces, `REMOVE: …` drops.\n"
        "- If a sample CONTRADICTS a baseline rule, use `ADJUST: old → new` "
        "(or `REMOVE:` when the rule should be dropped).\n"
        "- If this batch adds no style/strategy signal, say so in ONE bullet under that "
        "section (e.g. \"No new … rules\") — do not restate unchanged baseline rules.\n"
        "- Every actionable bullet must cite evidence from diffs and/or thread/facts.\n"
        "- Do NOT invent rules not supported by samples.\n"
        "- End with `### Context notes` (batch size, invalid samples, what changed vs baseline).\n"
        "  Context notes are for operator review only — they are NOT written into policy.\n\n"
        f"SAMPLES_JSON:\n{json.dumps(samples, indent=2, ensure_ascii=False)}"
    )
    try:
        raw = learning_llm.invoke_learning_llm(prompt)
    except learning_llm.LearningLlmError:
        raise
    except Exception as exc:
        raise learning_llm.LearningLlmError(
            f"LLM edit-learning distill failed: {exc}",
        ) from exc
    md = learning_llm.strip_markdown_fences(raw).strip()
    if not md:
        raise learning_llm.LearningLlmError("LLM returned empty markdown for edit learning")
    style_md, strategy_md = split_style_and_strategy_markdown(md)
    if not style_md and not strategy_md:
        style_md = md
    return style_md, strategy_md, True


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


def _merge_mode() -> str:
    """``llm_compress`` (default) | ``replace_section`` | ``append``.

    - llm_compress: LLM merges delta into the approved block (fallback: patch).
    - replace_section: deterministic patch — REMOVE/ADJUST/append.
    - append: accumulate each approved delta under the marker (may duplicate).
    """
    raw = os.environ.get(
        "KOL_STYLE_LEARNING_MERGE_MODE", "llm_compress",
    ).strip().lower()
    if raw in ("append", "replace_section", "llm_compress"):
        return raw
    return "llm_compress"


def _preview_merge_mode(mode: str) -> str:
    """Approval-card preview uses deterministic patch when approve-time merge is LLM."""
    return "replace_section" if mode == "llm_compress" else mode


def _approved_block_marker(*, scope: str) -> str:
    if scope == learning_store.REPLY_STRATEGY_SCOPE:
        return "## Approved strategy learning"
    if scope == learning_store.OUTCOME_STRATEGY_SCOPE:
        return OUTCOME_LEARNING_MARKER
    return "## Approved style learning"


def _merge_section_patch(current_md: str, proposed_section: str, *, marker: str) -> str:
    """Deterministic patch merge (replace_section semantics)."""
    base = (current_md or "").strip()
    section = (proposed_section or "").strip()
    if section.startswith(marker):
        section = section[len(marker) :].lstrip("\n").strip()
    if not section:
        return base
    head = base.split(marker, 1)[0].rstrip() if marker in base else base
    if marker in base:
        old_block = base.split(marker, 1)[1].lstrip("\n").strip()
        patched = apply_policy_delta_patch(old_block, section)
        tail = f"{marker}\n\n{patched}".rstrip() + "\n"
    else:
        tail = f"{marker}\n\n{section}\n"
    return f"{head}\n\n{tail}".strip() if head else tail.strip()


def _merge_section(current_md: str, proposed_section: str, *, marker: str, mode: str, scope: str = "") -> str:
    base = (current_md or "").strip()
    section = (proposed_section or "").strip()
    if section.startswith(marker):
        section = section[len(marker) :].lstrip("\n").strip()
    if not section:
        return base
    if mode == "llm_compress":
        try:
            return consolidate_policy_llm(
                base, section, scope=scope or marker, marker=marker,
            )
        except Exception:
            import logging

            logging.getLogger(__name__).warning(
                "llm_compress merge failed for %s; falling back to patch", scope or marker,
                exc_info=True,
            )
            return _merge_section_patch(base, section, marker=marker)
    if mode == "replace_section":
        return _merge_section_patch(base, section, marker=marker)
    # append: accumulate under a single marker.
    if marker not in base:
        tail = f"{marker}\n\n{section}\n"
        return f"{base}\n\n{tail}".strip() if base else tail.strip()
    return f"{base}\n\n{section}\n"


def consolidate_policy_llm(
    current_md: str,
    proposed_section: str,
    *,
    scope: str,
    marker: Optional[str] = None,
) -> str:
    """LLM-merge approved delta into the approved block; preserves preamble before marker.

    Raises on failure so callers can fall back to deterministic patch merge.
    """
    marker = marker or _approved_block_marker(scope=scope)
    base = (current_md or "").strip()
    section = proposal_section_for_policy_merge(proposed_section)
    if not section:
        return base

    head = ""
    existing_block = base
    if marker in base:
        head, existing_block = base.split(marker, 1)
        head = head.rstrip()
        existing_block = existing_block.lstrip("\n").strip()

    prompt = (
        f"You maintain the `{scope}` guideline document for KOL outreach emails.\n"
        "Merge APPROVED DELTA into EXISTING APPROVED RULES.\n"
        "Output one clean, de-duplicated, contradiction-free markdown block.\n\n"
        "Merge rules:\n"
        "- Apply `ADJUST: old → new` by replacing the matching existing bullet\n"
        "- Apply `REMOVE: …` by dropping the matching existing bullet\n"
        "- Append genuinely new rule bullets from the delta\n"
        "- Do NOT repeat existing rules unchanged\n"
        "- Omit 'no new rules' sentinel bullets and any Context notes / batch metadata\n"
        "- Keep ### goal / child_skill subsection headings stable\n"
        "- Output ONLY the merged approved-block body (no preamble, no outer marker)\n\n"
        f"EXISTING APPROVED RULES:\n{(existing_block or '(empty)').strip()}\n\n"
        f"APPROVED DELTA:\n{section}\n"
    )
    raw = learning_llm.invoke_learning_llm(prompt)
    merged_block = proposal_section_for_policy_merge(
        learning_llm.strip_markdown_fences(raw).strip(),
    )
    if not merged_block:
        raise RuntimeError("empty consolidation output")

    tail = f"{marker}\n\n{merged_block}".rstrip() + "\n"
    if head:
        return f"{head}\n\n{tail}".strip() + "\n"
    return tail.strip() + "\n"


OUTCOME_LEARNING_MARKER = "## Approved outcome learning"


def merge_strategy_policy_content(
    current_md: str, proposed_section: str, *, mode: Optional[str] = None,
) -> str:
    """Merge approved strategy section (goal-oriented, env-scoped policy)."""
    marker = _approved_block_marker(scope=learning_store.REPLY_STRATEGY_SCOPE)
    return _merge_section(
        current_md,
        proposed_section,
        marker=marker,
        mode=mode or _merge_mode(),
        scope=learning_store.REPLY_STRATEGY_SCOPE,
    )


def merge_outcome_policy_content(
    current_md: str, proposed_section: str, *, mode: Optional[str] = None,
) -> str:
    """Merge approved outcome guidance into ``outcome_strategy`` policy."""
    marker = _approved_block_marker(scope=learning_store.OUTCOME_STRATEGY_SCOPE)
    return _merge_section(
        current_md,
        proposed_section,
        marker=marker,
        mode=mode or _merge_mode(),
        scope=learning_store.OUTCOME_STRATEGY_SCOPE,
    )


def merge_style_policy_content(
    current_md: str,
    proposed_section: str,
    *,
    mode: Optional[str] = None,
    policy_scope: str = "company_style",
) -> str:
    """Merge an approved proposal section into existing policy markdown."""
    marker = _approved_block_marker(scope=policy_scope)
    return _merge_section(
        current_md,
        proposed_section,
        marker=marker,
        mode=mode or _merge_mode(),
        scope=policy_scope,
    )


def _batch_order() -> str:
    """``newest`` (default) prioritises the most recent edits for calibration."""
    raw = os.environ.get("KOL_STYLE_LEARNING_BATCH_ORDER", "newest").strip().lower()
    return raw if raw in ("newest", "oldest") else "newest"


def _event_recency_key(ev: dict[str, Any]) -> tuple[float, int]:
    """Sort key: prefer ``ts`` (newest first), tie-break on monotonic event id."""
    dt = learning_store._parse_event_ts(ev.get("ts"))
    ts_ord = dt.timestamp() if dt is not None else 0.0
    return (ts_ord, int(ev.get("id") or 0))


def _min_distinct_identities() -> int:
    raw = os.environ.get("KOL_STYLE_LEARNING_MIN_DISTINCT_IDENTITIES", "0").strip()
    try:
        return max(0, min(int(raw), 100))
    except ValueError:
        return 0


def _select_edit_batch(
    edited: list[dict[str, Any]],
    *,
    threshold: int,
    order: str,
    min_distinct: int,
) -> list[dict[str, Any]]:
    """Pick ``threshold`` edit events honouring recency + KOL diversity.

    ``order='newest'`` selects the most recent edits (so the proposal tracks the
    operator's latest habits); ``min_distinct`` greedily covers that many distinct
    identities first (avoid 10 near-duplicate edits from one campaign) before
    filling remaining slots by recency.
    """
    if order == "newest":
        candidates = sorted(edited, key=_event_recency_key, reverse=True)
    else:
        candidates = sorted(edited, key=_event_recency_key)

    if min_distinct <= 1 or threshold <= 1:
        chosen = candidates[:threshold]
    else:
        chosen = []
        seen_ids: set[int] = set()
        used_event = set()
        # Pass 1: cover distinct identities up to min_distinct (or batch full).
        for ev in candidates:
            if len(chosen) >= threshold:
                break
            iid = ev.get("identity_id")
            if iid is None or int(iid) in seen_ids:
                continue
            seen_ids.add(int(iid))
            chosen.append(ev)
            used_event.add(id(ev))
            if len(seen_ids) >= min_distinct:
                break
        # Pass 2: fill remaining slots by recency order.
        for ev in candidates:
            if len(chosen) >= threshold:
                break
            if id(ev) in used_event:
                continue
            chosen.append(ev)
            used_event.add(id(ev))

    # Always present the batch to the LLM in chronological order.
    chosen.sort(key=_event_recency_key)
    return chosen


def _gather_edited_for_style_proposal(
    conn,
    *,
    env: str,
    scope: str,
    owner_user_id: Optional[int],
    limit: int,
) -> dict[str, Any]:
    """Shared filter pipeline for preview + propose (newest/window/operator)."""
    events = learning_store.list_learning_events(
        conn, env=env, event_types=("draft_edit_learning",), limit=limit,
    )
    consumed = list_consumed_edit_event_ids(conn, env=env)
    fresh = [e for e in events if int(e.get("id") or 0) not in consumed]
    fresh = learning_store.filter_events_within_days(
        fresh, learning_store.learning_window_days(),
    )
    edited = _edited_events(fresh)
    if scope == "user_style" and owner_user_id is not None:
        attributed = [
            e for e in edited
            if (e.get("payload") or {}).get("operator_user_id") is not None
        ]
        if attributed:
            edited = [
                e for e in attributed
                if int((e.get("payload") or {}).get("operator_user_id") or 0)
                == int(owner_user_id)
            ]
    reserved_ids = pending_style_reserved_event_ids(conn, env=env)
    edited_available = [
        e for e in edited
        if int(e.get("id") or 0) not in reserved_ids
    ]
    return {
        "events_seen": len(events),
        "edited_unconsumed": len(edited),
        "edited_available": edited_available,
        "edited_queued_in_pending": len(edited) - len(edited_available),
        "reserved_ids": reserved_ids,
    }


def _gathered_edit_counts(gathered: dict[str, Any]) -> dict[str, Any]:
    """Operator-facing counts from ``_gather_edited_for_style_proposal``."""
    avail = gathered.get("edited_available") or []
    return {
        "events_seen": gathered.get("events_seen", 0),
        "edited_unconsumed": gathered.get("edited_unconsumed", 0),
        "edited_available": len(avail),
        "edited_queued_in_pending": gathered.get("edited_queued_in_pending", 0),
    }


def preview_next_style_edit_batch(
    conn,
    *,
    env: str,
    scope: str,
    owner_user_id: Optional[int] = None,
    limit: int = 200,
    batch_size: Optional[int] = None,
) -> dict[str, Any]:
    """Read-only: which edit events would be taken for the next distill batch."""
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
            "ready": False,
            "skipped": True,
            "reason": "pending style_learning_proposal already exists",
            "scope": scope,
            "batch_threshold": threshold,
        }

    gathered = _gather_edited_for_style_proposal(
        conn, env=env, scope=scope, owner_user_id=owner_user_id, limit=limit,
    )
    edited_available: list[dict[str, Any]] = gathered["edited_available"]
    if not edited_available:
        return {
            "ready": False,
            "skipped": True,
            "reason": "no new edited sent bodies",
            "scope": scope,
            "batch_threshold": threshold,
            **_gathered_edit_counts(gathered),
        }
    if len(edited_available) < threshold:
        return {
            "ready": False,
            "skipped": True,
            "reason": "below_style_learning_batch_threshold",
            "pending_edits": len(edited_available),
            "scope": scope,
            "batch_threshold": threshold,
            **_gathered_edit_counts(gathered),
        }

    batch = _select_edit_batch(
        edited_available,
        threshold=threshold,
        order=_batch_order(),
        min_distinct=_min_distinct_identities(),
    )
    samples: list[dict[str, Any]] = []
    for ev in batch:
        payload = ev.get("payload") or {}
        samples.append({
            "event_id": ev.get("id"),
            "identity_id": ev.get("identity_id"),
            "campaign_id": ev.get("campaign_id"),
            "goal": ev.get("goal") or payload.get("goal"),
            "ts": ev.get("ts"),
            "edit_distance": payload.get("edit_distance"),
            "child_skill": payload.get("child_skill"),
            "operator_user_id": payload.get("operator_user_id"),
            "was_edited": payload.get("was_edited"),
        })
    distinct_identity_ids = {
        int(e["identity_id"]) for e in batch if e.get("identity_id") is not None
    }
    return {
        "ready": True,
        "scope": scope,
        "owner_user_id": owner_user_id,
        "batch_threshold": threshold,
        "sample_count": len(batch),
        "sample_identity_count": len(distinct_identity_ids),
        "remaining_after_batch": max(0, len(edited_available) - len(batch)),
        "samples": samples,
        **_gathered_edit_counts(gathered),
    }


def _section_merge_effect(
    current_md: str,
    *,
    marker: str,
    mode: str,
) -> str:
    """Operator-facing hint: how approval will change the policy document."""
    base = (current_md or "").strip()
    has_marker = marker in base
    if mode == "replace_section":
        return "patch_delta" if has_marker else "add_new"
    if mode == "llm_compress":
        return "llm_merge" if has_marker else "add_new"
    return "append_delta" if has_marker else "add_new"


def build_edit_stats_by_scope(
    conn,
    *,
    env: str,
    threshold: int,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Per-scope (and per-operator user_style) distill batch progress."""
    rows: list[dict[str, Any]] = []

    def _row(scope: str, owner_user_id: Optional[int]) -> dict[str, Any]:
        gathered = _gather_edited_for_style_proposal(
            conn, env=env, scope=scope, owner_user_id=owner_user_id, limit=limit,
        )
        avail = len(gathered["edited_available"])
        pending = find_pending_style_proposal(
            conn, env=env, scope=scope, owner_user_id=owner_user_id,
        )
        return {
            "scope": scope,
            "owner_user_id": owner_user_id,
            "edited_available": avail,
            "edited_queued_in_pending": gathered["edited_queued_in_pending"],
            "ready_for_distill": avail >= threshold,
            "has_pending_proposal": pending is not None,
        }

    rows.append(_row("company_style", None))

    events = learning_store.list_learning_events(
        conn, env=env, event_types=("draft_edit_learning",), limit=limit,
    )
    consumed = list_consumed_edit_event_ids(conn, env=env)
    fresh = [e for e in events if int(e.get("id") or 0) not in consumed]
    fresh = learning_store.filter_events_within_days(
        fresh, learning_store.learning_window_days(),
    )
    operator_ids: set[int] = set()
    for ev in _edited_events(fresh):
        oid = (ev.get("payload") or {}).get("operator_user_id")
        if oid is not None:
            try:
                operator_ids.add(int(oid))
            except (TypeError, ValueError):
                continue
    for oid in sorted(operator_ids):
        rows.append(_row("user_style", oid))
    return rows


def preview_policy_merge_from_proposal(
    conn,
    *,
    env: str,
    proposal: dict[str, Any],
) -> dict[str, Any]:
    """Read-only: show current vs merged policy if this proposal were approved."""
    scope = str(proposal.get("scope") or "")
    owner_user_id = proposal.get("owner_user_id")
    if owner_user_id is not None:
        owner_user_id = int(owner_user_id)
    mode = _merge_mode()
    preview_mode = _preview_merge_mode(mode)

    style_md = str(proposal.get("proposed_style_markdown") or "").strip()
    strategy_md = str(proposal.get("proposed_strategy_markdown") or "").strip()
    combined = str(proposal.get("proposed_markdown") or "").strip()
    if not style_md and not strategy_md and combined:
        style_md, strategy_md = split_style_and_strategy_markdown(combined)

    out: dict[str, Any] = {
        "env": env,
        "merge_mode": mode,
        "preview_merge_mode": preview_mode,
        "preview_note": (
            "预览为 patch 近似；批准时将 LLM 智能合并"
            if mode == "llm_compress"
            else None
        ),
        "sections": {},
    }

    if scope in learning_store.EDIT_LEARNING_SCOPES and style_md:
        style_env = env if scope in pol.ENV_SCOPED_POLICIES else None
        cur = pol.get_policy(
            conn, scope=scope, owner_user_id=owner_user_id, env=style_env,
        )
        current_md = (cur or {}).get("content_md") or ""
        style_marker = "## Approved style learning"
        style_merge_md = proposal_section_for_policy_merge(style_md)
        style_actionable = is_actionable_policy_delta(style_md)
        if style_actionable:
            merged_md, mode_used = _merge_policy_section(
                current_md, style_merge_md, scope=scope, mode=preview_mode,
            )
            merge_effect = _section_merge_effect(
                current_md, marker=style_marker, mode=mode,
            )
        else:
            merged_md, mode_used = current_md, mode
            merge_effect = "unchanged"
        out["sections"]["style"] = {
            "scope": scope,
            "current_md": current_md,
            "proposed_section_md": style_md,
            "policy_merge_section_md": style_merge_md,
            "merge_skipped": not style_actionable,
            "merge_skip_reason": None if style_actionable else "no_actionable_rules",
            "merged_md": merged_md,
            "merge_mode_used": mode_used,
            "merge_effect": merge_effect,
            "current_chars": len(current_md),
            "merged_chars": len(merged_md),
            "delta_chars": len(merged_md) - len(current_md),
        }

    if strategy_md.strip():
        cur_s = pol.get_policy(conn, scope=learning_store.REPLY_STRATEGY_SCOPE, env=env)
        current_s = (cur_s or {}).get("content_md") or ""
        strat_marker = "## Approved strategy learning"
        strategy_merge_md = proposal_section_for_policy_merge(strategy_md)
        strategy_actionable = is_actionable_policy_delta(strategy_md)
        if strategy_actionable:
            merged_s, mode_used_s = _merge_policy_section(
                current_s,
                strategy_merge_md,
                scope=learning_store.REPLY_STRATEGY_SCOPE,
                mode=preview_mode,
            )
            merge_effect_s = _section_merge_effect(
                current_s, marker=strat_marker, mode=mode,
            )
        else:
            merged_s, mode_used_s = current_s, mode
            merge_effect_s = "unchanged"
        out["sections"]["strategy"] = {
            "scope": learning_store.REPLY_STRATEGY_SCOPE,
            "current_md": current_s,
            "proposed_section_md": strategy_md,
            "policy_merge_section_md": strategy_merge_md,
            "merge_skipped": not strategy_actionable,
            "merge_skip_reason": None if strategy_actionable else "no_actionable_rules",
            "merged_md": merged_s,
            "merge_mode_used": mode_used_s,
            "merge_effect": merge_effect_s,
            "current_chars": len(current_s),
            "merged_chars": len(merged_s),
            "delta_chars": len(merged_s) - len(current_s),
        }

    if pol.is_discovery_criteria_scope(scope) and combined:
        # Lazy import: learning_discovery imports this module for merging.
        from . import learning_discovery

        cur_d = pol.get_policy(conn, scope=scope, env=env)
        current_d = (cur_d or {}).get("content_md") or ""
        merged_d = learning_discovery.merge_discovery_policy_content(
            current_d, combined, mode=mode,
        )
        policy_merge_md = proposal_section_for_policy_merge(combined)
        out["sections"]["discovery"] = {
            "scope": scope,
            "current_md": current_d,
            "proposed_section_md": combined,
            "policy_merge_section_md": policy_merge_md,
            "merged_md": merged_d,
            "merge_mode_used": mode,
            "merge_effect": _section_merge_effect(
                current_d,
                marker=learning_discovery.DISCOVERY_LEARNING_MARKER,
                mode=mode,
            ),
            "current_chars": len(current_d),
            "merged_chars": len(merged_d),
            "delta_chars": len(merged_d) - len(current_d),
        }
        return out

    is_outcome = scope == learning_store.OUTCOME_STRATEGY_SCOPE
    if is_outcome and combined:
        cur_o = pol.get_policy(
            conn, scope=learning_store.OUTCOME_STRATEGY_SCOPE, env=env,
        )
        current_o = (cur_o or {}).get("content_md") or ""
        merged_o = merge_outcome_policy_content(current_o, combined, mode=mode)
        out["sections"]["outcome"] = {
            "scope": learning_store.OUTCOME_STRATEGY_SCOPE,
            "current_md": current_o,
            "proposed_section_md": combined,
            "merged_md": merged_o,
            "merge_mode_used": mode,
            "merge_effect": _section_merge_effect(
                current_o, marker=OUTCOME_LEARNING_MARKER, mode=mode,
            ),
            "current_chars": len(current_o),
            "merged_chars": len(merged_o),
            "delta_chars": len(merged_o) - len(current_o),
        }

    return out


def _merge_policy_section(
    current_md: str, section: str, *, scope: str, mode: str,
) -> tuple[str, str]:
    """Merge one section; return (merged_md, mode_used)."""
    if scope == learning_store.REPLY_STRATEGY_SCOPE:
        merged = merge_strategy_policy_content(current_md, section, mode=mode)
    elif scope == learning_store.OUTCOME_STRATEGY_SCOPE:
        merged = merge_outcome_policy_content(current_md, section, mode=mode)
    else:
        merged = merge_style_policy_content(
            current_md, section, mode=mode, policy_scope=scope,
        )
    return merged, mode


def list_style_approval_markers(
    conn,
    *,
    env: str,
    days: int = 90,
) -> list[dict[str, Any]]:
    """Approved style-learning batches for trend chart annotations."""
    cutoff = learning_store._parse_event_ts(
        (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=max(1, days))).isoformat()
    )
    rows = conn.execute(
        """SELECT fact_value, captured_at FROM kol_facts_latest
            WHERE fact_namespace='approval'
              AND fact_key=?
              AND env=?""",
        (learning_store.STYLE_LEARNING_APPROVAL_FACT, env),
    ).fetchall()
    markers: list[dict[str, Any]] = []
    for row in rows:
        try:
            val = json.loads(row["fact_value"]) if row["fact_value"] else {}
        except (TypeError, ValueError):
            continue
        if not isinstance(val, dict) or val.get("decision") != "approved":
            continue
        at = row["captured_at"]
        dt = learning_store._parse_event_ts(at)
        if cutoff and dt and dt < cutoff:
            continue
        markers.append({
            "at": at,
            "scope": val.get("scope"),
            "sample_count": val.get("sample_count"),
            "owner_user_id": val.get("owner_user_id"),
        })
    markers.sort(key=lambda m: str(m.get("at") or ""))
    return markers


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

    gathered = _gather_edited_for_style_proposal(
        conn, env=env, scope=scope, owner_user_id=owner_user_id, limit=limit,
    )
    edited_available: list[dict[str, Any]] = gathered["edited_available"]
    if not edited_available:
        return {
            "skipped": True,
            "reason": "no new edited sent bodies",
            "events_seen": gathered["events_seen"],
        }
    if len(edited_available) < threshold:
        return {
            "skipped": True,
            "reason": "below_style_learning_batch_threshold",
            "pending_edits": len(edited_available),
            "batch_threshold": threshold,
            "scope": scope,
        }

    batch = _select_edit_batch(
        edited_available,
        threshold=threshold,
        order=_batch_order(),
        min_distinct=_min_distinct_identities(),
    )
    style_md, strategy_md, llm_used = distill_edit_learning_llm(
        conn, batch, style_scope=scope, env=env, owner_user_id=owner_user_id,
    )
    anchor_id = resolve_learning_anchor_identity_id(conn, env=env, events=batch)
    event_ids = [int(e["id"]) for e in batch if e.get("id") is not None]
    distinct_identity_ids = {
        int(e["identity_id"])
        for e in batch
        if e.get("identity_id") is not None
    }
    distinct_campaign_ids = {
        str(e.get("campaign_id") or "").strip()
        for e in batch
        if e.get("campaign_id")
    }
    distinct_campaign_ids.discard("")
    distinct_operator_ids = sorted({
        int((e.get("payload") or {}).get("operator_user_id"))
        for e in batch
        if (e.get("payload") or {}).get("operator_user_id") is not None
    })
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
        "sample_identity_count": len(distinct_identity_ids),
        "sample_campaign_count": len(distinct_campaign_ids),
        "sample_operator_ids": distinct_operator_ids,
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
    # #region agent log
    try:
        import json as _json
        import time as _time
        from pathlib import Path as _Path

        _log_path = _Path("/Users/arnold/agent_prj/.cursor/debug-cfcf5c.log")
        _log_path.parent.mkdir(parents=True, exist_ok=True)
        with _log_path.open("a", encoding="utf-8") as _fh:
            _fh.write(
                _json.dumps(
                    {
                        "sessionId": "cfcf5c",
                        "runId": "pre-fix",
                        "hypothesisId": "H3",
                        "location": "learning_distill.py:propose_style_learning_approval",
                        "message": "style_proposal_created",
                        "data": {
                            "env": env,
                            "scope": scope,
                            "batch_threshold": threshold,
                            "edited_available": len(edited_available),
                            "sample_count": len(batch),
                            "source_event_ids_len": len(event_ids),
                        },
                        "timestamp": int(_time.time() * 1000),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except Exception:
        pass
    # #endregion
    return {
        "approval_fact": learning_store.STYLE_LEARNING_APPROVAL_FACT,
        "identity_id": anchor_id,
        "scope": scope,
        "llm_used": llm_used,
        "sample_identity_count": len(distinct_identity_ids),
        "sample_operator_ids": distinct_operator_ids,
        "sample_count": len(batch),
        "batch_threshold": threshold,
        "remaining_edits": max(0, len(edited_available) - len(batch)),
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

    style_actionable = bool(style_md) and is_actionable_policy_delta(style_md)
    strategy_actionable = bool(strategy_md.strip()) and is_actionable_policy_delta(
        strategy_md,
    )
    if not style_actionable and not strategy_actionable:
        return {
            "scope": scope,
            "skipped": True,
            "reason": "no_actionable_policy_delta",
            "merge_mode": _merge_mode(),
            "strategy_policy": {
                "skipped": True,
                "reason": "no_actionable_rules",
            },
        }

    mode = _merge_mode()

    def _merge(current_md: str, section: str, *, kind: str) -> tuple[str, str]:
        """Return (merged_md, mode_used). llm_compress falls back to patch in _merge_section."""
        if kind == learning_store.REPLY_STRATEGY_SCOPE:
            merged = merge_strategy_policy_content(current_md, section, mode=mode)
        else:
            merged = merge_style_policy_content(
                current_md, section, mode=mode, policy_scope=kind,
            )
        return merged, mode

    style_policy_env = env if scope in pol.ENV_SCOPED_POLICIES else None
    current_style = pol.get_policy(
        conn, scope=scope, owner_user_id=owner_user_id, env=style_policy_env,
    )
    current_style_md = (current_style or {}).get("content_md") or ""
    style_row: dict[str, Any] = {}
    style_mode = mode
    merged_style = current_style_md
    if style_actionable:
        style_merge_md = proposal_section_for_policy_merge(style_md)
        merged_style, style_mode = _merge(
            current_style_md, style_merge_md, kind=scope,
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
        if strategy_actionable:
            current_strat = pol.get_policy(
                conn,
                scope=learning_store.REPLY_STRATEGY_SCOPE,
                env=env,
            )
            strategy_merge_md = proposal_section_for_policy_merge(strategy_md)
            merged_strat, _strat_mode = _merge(
                (current_strat or {}).get("content_md") or "",
                strategy_merge_md,
                kind=learning_store.REPLY_STRATEGY_SCOPE,
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
        else:
            strategy_result = {
                "skipped": True,
                "reason": "no_actionable_rules",
            }

    out: dict[str, Any] = {
        "scope": scope,
        "merge_mode": style_mode,
        "strategy_policy": strategy_result,
    }
    if style_actionable:
        out["version"] = style_row.get("version")
        out["policy_id"] = style_row.get("id")
        out["merged_chars"] = len(merged_style)
    else:
        out["style_policy"] = {"skipped": True, "reason": "no_actionable_rules"}
    return out


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
