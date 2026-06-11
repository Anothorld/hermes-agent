"""Nightly distill for the shortlist-decision learning channel.

Three deterministic-entry jobs (wired in :mod:`learning_jobs`):

* ``apply_discovery_policy`` — group ``shortlist_decision_learning`` events by
  **SKU** and by **product category**, LLM-distill each group into a learned
  discovery-criteria proposal, and open a pending
  ``approval.discovery_learning_proposal`` (operator approves in Console →
  merged into ``policy_documents`` under the dynamic
  ``discovery_criteria:spu:*`` / ``discovery_criteria:category:*`` scopes).
* ``mine_discovery_tags`` — LLM-cluster operator comments into high-frequency
  reasons; new reasons become ``proposed`` rows in ``discovery_decision_tags``.
* category inference for unmapped SKUs (runs inside ``apply_discovery_policy``
  so the category grouping has data to work with).
"""

from __future__ import annotations

import json
import logging
import os
from collections import Counter, defaultdict
from typing import Any, Final, Optional

from . import cal
from . import discovery_decision_learning as ddl
from . import discovery_decision_tags as decision_tags
from . import learning_llm
from . import learning_store
from . import policies as pol

log = logging.getLogger(__name__)

DISCOVERY_LEARNING_MARKER: Final[str] = "## Approved discovery learning"


def discovery_learning_batch_size() -> int:
    """Min decision events per group (SPU or category) before distill runs."""
    raw = os.environ.get("KOL_DISCOVERY_LEARNING_BATCH_SIZE", "10").strip()
    try:
        return max(1, min(int(raw), 500))
    except ValueError:
        return 10


def tag_mine_min_count() -> int:
    """Min comment occurrences before a mined reason becomes a tag proposal."""
    raw = os.environ.get("KOL_DISCOVERY_TAG_MINE_MIN_COUNT", "5").strip()
    try:
        return max(2, min(int(raw), 100))
    except ValueError:
        return 5


# ---------------------------------------------------------------------------
# Consumed / pending bookkeeping (same pattern as style learning)
# ---------------------------------------------------------------------------


def _iter_proposal_facts(conn, *, env: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT identity_id, campaign_id, fact_value, captured_at
             FROM kol_facts_latest
            WHERE fact_namespace='approval'
              AND fact_key=?
              AND env=?
            ORDER BY id DESC""",
        (ddl.DISCOVERY_LEARNING_APPROVAL_FACT, env),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        try:
            val = json.loads(row["fact_value"]) if row["fact_value"] else {}
        except (TypeError, ValueError):
            continue
        if isinstance(val, dict):
            out.append({
                "identity_id": row["identity_id"],
                "campaign_id": row["campaign_id"],
                "captured_at": row["captured_at"],
                "value": val,
            })
    return out


def list_consumed_decision_event_ids(conn, *, env: str) -> set[int]:
    """Event ids already merged via an approved discovery proposal."""
    consumed: set[int] = set()
    for row in _iter_proposal_facts(conn, env=env):
        val = row["value"]
        if val.get("decision") != "approved":
            continue
        for eid in val.get("source_event_ids") or []:
            try:
                consumed.add(int(eid))
            except (TypeError, ValueError):
                continue
    return consumed


def list_pending_discovery_proposals(conn, *, env: str) -> list[dict[str, Any]]:
    """All pending ``approval.discovery_learning_proposal`` rows (newest first)."""
    out: list[dict[str, Any]] = []
    for row in _iter_proposal_facts(conn, env=env):
        val = row["value"]
        if val.get("decision") in (None, "pending"):
            out.append({**row, "scope": val.get("scope")})
    return out


def find_pending_discovery_proposal(
    conn, *, env: str, scope: str,
) -> Optional[dict[str, Any]]:
    for row in list_pending_discovery_proposals(conn, env=env):
        if str(row["value"].get("scope") or "") == scope:
            return row
    return None


def pending_discovery_reserved_event_ids(conn, *, env: str) -> set[int]:
    reserved: set[int] = set()
    for row in list_pending_discovery_proposals(conn, env=env):
        for raw in row["value"].get("source_event_ids") or []:
            try:
                reserved.add(int(raw))
            except (TypeError, ValueError):
                continue
    return reserved


# ---------------------------------------------------------------------------
# Sample gathering / grouping
# ---------------------------------------------------------------------------


def _fresh_decision_events(conn, *, env: str, limit: int = 0) -> list[dict[str, Any]]:
    """Unconsumed decision events within the learning window.

    Paginates through ``kol_conversation_events`` (500 rows per page) so high-
    volume SPUs are not starved by a single newest-first cap.
    """
    consumed = list_consumed_decision_event_ids(conn, env=env)
    reserved = pending_discovery_reserved_event_ids(conn, env=env)
    blocked = consumed | reserved
    window_days = learning_store.learning_window_days()
    fresh: list[dict[str, Any]] = []
    before_id: Optional[int] = None
    page_size = 500
    while True:
        page = learning_store.list_learning_events(
            conn,
            env=env,
            event_types=(ddl.SHORTLIST_DECISION_EVENT,),
            limit=page_size,
            before_id=before_id,
        )
        if not page:
            break
        in_window = learning_store.filter_events_within_days(page, window_days)
        for ev in in_window:
            if int(ev.get("id") or 0) not in blocked:
                fresh.append(ev)
        oldest_id = min(int(e["id"]) for e in page if e.get("id") is not None)
        before_id = oldest_id
        if len(page) < page_size:
            break
        # Stop paging once the oldest row in this batch is outside the window.
        if len(in_window) < len(page):
            break
        if limit > 0 and len(fresh) >= limit:
            fresh = fresh[:limit]
            break
    return fresh


def _group_events(
    conn, events: list[dict[str, Any]],
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Group by ('spu', sku) and ('category', category)."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for ev in events:
        payload = ev.get("payload") or {}
        sku = str(payload.get("sku") or "").strip()
        if sku:
            groups[("spu", sku)].append(ev)
            category = str(payload.get("category") or "").strip() or (
                ddl.get_category_for_sku(conn, sku=sku) or ""
            )
            if category:
                groups[("category", category)].append(ev)
    return groups


def _shape_sample(ev: dict[str, Any]) -> dict[str, Any]:
    payload = ev.get("payload") or {}
    snapshot = payload.get("kol_snapshot") or {}
    return {
        "event_id": ev.get("id"),
        "ts": ev.get("ts"),
        "action": payload.get("action"),
        "reason_tags": payload.get("reason_tags") or [],
        "comment": str(payload.get("comment") or "")[:600],
        "sku": payload.get("sku"),
        "category": payload.get("category"),
        "transfer_to_campaign_id": payload.get("transfer_to_campaign_id"),
        "kol": {
            "identity": snapshot.get("identity") or {},
            "candidate": snapshot.get("candidate") or {},
            "facts": snapshot.get("facts") or {},
        },
    }


# ---------------------------------------------------------------------------
# LLM distill → pending approval
# ---------------------------------------------------------------------------


def _current_criteria_baseline(conn, *, scope: str, env: str, max_chars: int = 2000) -> str:
    try:
        row = pol.get_policy(conn, scope=scope, env=env)
    except Exception:
        row = None
    return ((row or {}).get("content_md") or "").strip()[:max_chars]


def _recent_rejection_feedback_block(
    conn, *, env: str, scope: str, limit: int = 200, max_items: int = 3,
) -> str:
    """Operator feedback from recently rejected proposals for this scope.

    Injected into the distill prompt so the next batch does not repeat a
    suggestion the operator already declined (same pattern as style learning).
    """
    events = learning_store.list_learning_events(
        conn, env=env, event_types=("discovery_proposal_rejected",), limit=limit,
    )
    items: list[str] = []
    for ev in events:
        payload = ev.get("payload") or {}
        if str(payload.get("scope") or "") != scope:
            continue
        note = str(payload.get("note") or "").strip()
        tags = [str(t) for t in (payload.get("tags") or []) if t]
        rejected_md = str(payload.get("rejected_markdown") or "").strip()
        line = f"- rejected_at={ev.get('ts')}"
        if tags:
            line += f" tags={','.join(tags)}"
        if note:
            line += f" note={note[:200]}"
        if rejected_md:
            line += f"\n  rejected_excerpt: {rejected_md[:400]}"
        items.append(line)
        if len(items) >= max_items:
            break
    if not items:
        return ""
    return (
        "\nPREVIOUSLY REJECTED PROPOSALS for this scope (operator feedback — "
        "do NOT repeat these suggestions unless new samples strongly "
        "contradict the rejection):\n" + "\n".join(items) + "\n"
    )


def distill_discovery_criteria_llm(
    conn,
    events: list[dict[str, Any]],
    *,
    scope: str,
    group_kind: str,
    group_key: str,
    env: str,
) -> str:
    """LLM-distill one group of decision samples into criteria markdown."""
    samples = [_shape_sample(ev) for ev in events]
    baseline = _current_criteria_baseline(conn, scope=scope, env=env)
    rejection_block = _recent_rejection_feedback_block(conn, env=env, scope=scope)
    level = "single product (SPU)" if group_kind == "spu" else "product category"
    prompt = (
        "You analyze operator decisions on Instagram KOL shortlists "
        "(approve / remove-from-shortlist / transfer-to-other-campaign).\n"
        f"Learning level: {level} `{group_key}` (env={env}). "
        f"Batch size: {len(samples)} decisions.\n"
        "Each sample includes the operator's reason_tags, free-text comment, and a "
        "frozen KOL feature snapshot (creator type, followers, region, content "
        "pillars, Nox due-diligence facts, reels stats, agent scores).\n\n"
        "CURRENT APPROVED CRITERIA (baseline — refine, do NOT restate verbatim):\n"
        f"{baseline or '(none yet)'}\n"
        f"{rejection_block}\n"
        "Produce ONE markdown section of INCREMENTAL revisions for operator "
        "approval (output ONLY markdown):\n\n"
        f"{DISCOVERY_LEARNING_MARKER}\n"
        "### Preferred KOL profile\n"
        "- 3-8 bullets: traits the operator consistently approves "
        "(audience, tone, content style, follower band, region...).\n"
        "### Veto signals\n"
        "- 3-8 bullets: traits that consistently lead to removal "
        "(soft veto signals — do not relax the skill's HARD thresholds).\n"
        "### Scoring adjustments\n"
        "- bullets: how Match/Showcase scoring emphasis should shift.\n"
        "### Exemplars\n"
        "- 1-3 approved + 1-3 removed examples with the deciding evidence.\n\n"
        "Rules:\n"
        "- Output only the DELTA vs the baseline; prefix contradictions with "
        "`ADJUST:` (or `REMOVE:` to drop a baseline rule).\n"
        "- Every bullet must cite evidence from tags/comments/snapshots.\n"
        "- Do NOT invent rules unsupported by samples.\n"
        "- Never include operator names or ids in the output.\n"
        "- End with `### Context notes` (batch size, action mix, dominant tags).\n"
        "  Context notes are for operator review only — they are NOT written into policy.\n\n"
        f"SAMPLES_JSON:\n{json.dumps(samples, indent=2, ensure_ascii=False)}"
    )
    try:
        raw = learning_llm.invoke_learning_llm(prompt)
    except learning_llm.LearningLlmError:
        raise
    except Exception as exc:
        raise learning_llm.LearningLlmError(
            f"LLM discovery-criteria distill failed: {exc}",
        ) from exc
    md = learning_llm.strip_markdown_fences(raw).strip()
    if not md:
        raise learning_llm.LearningLlmError(
            "LLM returned empty markdown for discovery learning",
        )
    return md


def propose_discovery_learning_approval(
    conn,
    *,
    env: str,
    updated_by: str,
    limit: int = 500,
    batch_size: Optional[int] = None,
) -> dict[str, Any]:
    """Distill ready groups and open pending discovery-learning proposals."""
    threshold = batch_size if batch_size is not None else discovery_learning_batch_size()
    fresh = _fresh_decision_events(conn, env=env)
    if not fresh:
        return {"skipped": True, "reason": "no new shortlist decision events"}
    groups = _group_events(conn, fresh)
    proposed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for (kind, key), events in sorted(groups.items()):
        scope = pol.discovery_criteria_scope(kind, key)
        if len(events) < threshold:
            skipped.append({
                "scope": scope, "reason": "below_batch_threshold",
                "samples": len(events), "batch_threshold": threshold,
            })
            continue
        if find_pending_discovery_proposal(conn, env=env, scope=scope):
            skipped.append({"scope": scope, "reason": "pending_proposal_exists"})
            continue
        md = distill_discovery_criteria_llm(
            conn, events, scope=scope, group_kind=kind, group_key=key, env=env,
        )
        from . import learning_distill

        anchor_id = learning_distill.resolve_learning_anchor_identity_id(
            conn, env=env, events=events,
        )
        event_ids = [int(e["id"]) for e in events if e.get("id") is not None]
        distinct_identity_ids = {
            int(e["identity_id"]) for e in events if e.get("identity_id") is not None
        }
        action_mix = Counter(
            str((e.get("payload") or {}).get("action") or "") for e in events
        )
        proposal = {
            "decision": "pending",
            "scope": scope,
            "group_kind": kind,
            "group_key": key,
            "env": env,
            "title": f"Discovery learning ({kind}:{key}, {env})",
            "proposed_markdown": md,
            "source_event_ids": event_ids,
            "sample_count": len(events),
            "sample_identity_count": len(distinct_identity_ids),
            "action_mix": dict(action_mix),
            "batch_threshold": threshold,
            "llm_used": True,
            "opened_by": updated_by,
        }
        cal.write_facts(
            identity_id=anchor_id,
            campaign_id=None,
            namespace="approval",
            facts={ddl.DISCOVERY_LEARNING_APPROVAL_FACT: proposal},
            source="learning:propose:discovery_criteria",
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
                            "hypothesisId": "H2",
                            "location": "learning_discovery.py:propose_discovery_learning_approval",
                            "message": "discovery_proposal_created",
                            "data": {
                                "env": env,
                                "scope": scope,
                                "group_kind": kind,
                                "group_key": key,
                                "batch_threshold": threshold,
                                "sample_count": len(events),
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
        proposed.append({
            "scope": scope,
            "identity_id": anchor_id,
            "sample_count": len(events),
            "source_event_ids": event_ids,
        })
    if not proposed and skipped:
        return {"skipped": True, "reason": "no group ready", "groups": skipped}
    return {
        "proposed_count": len(proposed),
        "proposals": proposed,
        "groups_skipped": skipped,
        "pending": bool(proposed),
    }


def merge_discovery_policy_content(
    current_md: str, proposed_section: str, *, mode: Optional[str] = None,
) -> str:
    from . import learning_distill

    merge_body = learning_distill.proposal_section_for_policy_merge(proposed_section)
    return learning_distill._merge_section(
        current_md,
        merge_body,
        marker=DISCOVERY_LEARNING_MARKER,
        mode=mode or learning_distill._merge_mode(),
    )


def apply_approved_discovery_proposal(
    conn,
    *,
    env: str,
    proposal: dict[str, Any],
    updated_by: str,
) -> dict[str, Any]:
    """Merge an approved discovery proposal into its ``discovery_criteria:*`` scope."""
    scope = str(proposal.get("scope") or "")
    if not pol.is_discovery_criteria_scope(scope):
        raise ValueError(f"invalid discovery proposal scope: {scope!r}")
    md = str(proposal.get("proposed_markdown") or "").strip()
    if not md:
        raise ValueError("proposal missing proposed_markdown")
    current = pol.get_policy(conn, scope=scope, env=env)
    merged = merge_discovery_policy_content(
        (current or {}).get("content_md") or "", md,
    )
    row = pol.put_policy(
        conn,
        scope=scope,
        content_md=merged,
        updated_by=updated_by,
        title=str(proposal.get("title") or scope),
        env=env,
    )
    return {
        "scope": scope,
        "version": row.get("version"),
        "policy_id": row.get("id"),
        "merged_chars": len(merged),
    }


# ---------------------------------------------------------------------------
# Brief injection (consumed by Console at campaign launch)
# ---------------------------------------------------------------------------


def build_learned_discovery_criteria(
    conn,
    *,
    env: str,
    sku: Optional[str],
    max_chars: int = 4000,
) -> dict[str, Any]:
    """SPU + category criteria markdown for the discovery launch brief.

    SPU criteria take priority; category criteria fill the remaining budget.
    Returns empty strings when nothing is learned yet (caller skips section).
    """
    out: dict[str, Any] = {"sku": sku, "category": None, "spu_md": "", "category_md": ""}
    if not sku:
        return out
    spu_scope = pol.discovery_criteria_scope("spu", sku)
    from . import learning_distill

    spu_row = pol.get_policy(conn, scope=spu_scope, env=env)
    spu_md = learning_distill.strip_proposal_context_notes(
        ((spu_row or {}).get("content_md") or "").strip(),
    )
    category = ddl.get_category_for_sku(conn, sku=sku)
    category_md = ""
    if category:
        out["category"] = category
        cat_scope = pol.discovery_criteria_scope("category", category)
        cat_row = pol.get_policy(conn, scope=cat_scope, env=env)
        category_md = learning_distill.strip_proposal_context_notes(
            ((cat_row or {}).get("content_md") or "").strip(),
        )
    out["spu_md"] = spu_md[:max_chars]
    remaining = max(0, max_chars - len(out["spu_md"]))
    out["category_md"] = category_md[:remaining]
    # #region agent log
    try:
        import json as _json
        import time as _time
        from pathlib import Path as _Path

        with _Path("/Users/arnold/agent_prj/.cursor/debug-8ea4a0.log").open(
            "a", encoding="utf-8",
        ) as _fh:
            _fh.write(
                _json.dumps(
                    {
                        "sessionId": "8ea4a0",
                        "hypothesisId": "H1",
                        "location": "learning_discovery.py:build_learned_discovery_criteria",
                        "message": "discovery_criteria_built",
                        "data": {
                            "env": env,
                            "sku": sku,
                            "category": out.get("category"),
                            "spu_scope": spu_scope,
                            "spu_md_len": len(out["spu_md"]),
                            "category_md_len": len(out["category_md"]),
                            "spu_row_present": bool(spu_row),
                        },
                        "timestamp": int(_time.time() * 1000),
                    },
                    ensure_ascii=False,
                )
                + "\n",
            )
    except Exception:
        pass
    # #endregion
    return out


def discovery_overview_stats(conn, *, env: str) -> dict[str, Any]:
    """Batch-progress snapshot for the learning dashboard.

    Per SPU/category group: fresh (unconsumed, unreserved) sample count vs the
    distill threshold, plus pending-proposal count — so operators can see how
    far each product is from the next learned-criteria proposal.
    """
    threshold = discovery_learning_batch_size()
    fresh = _fresh_decision_events(conn, env=env)
    groups = _group_events(conn, fresh)
    pending = list_pending_discovery_proposals(conn, env=env)
    pending_scopes = {str(p["value"].get("scope") or "") for p in pending}
    group_rows: list[dict[str, Any]] = []
    for (kind, key), events in sorted(groups.items()):
        scope = pol.discovery_criteria_scope(kind, key)
        group_rows.append({
            "group_kind": kind,
            "group_key": key,
            "scope": scope,
            "fresh_samples": len(events),
            "batch_threshold": threshold,
            "ready_for_distill": len(events) >= threshold,
            "has_pending_proposal": scope in pending_scopes,
        })
    return {
        "fresh_decisions": len(fresh),
        "batch_threshold": threshold,
        "groups": group_rows,
        "pending_proposals": len(pending),
    }


# ---------------------------------------------------------------------------
# Tag mining
# ---------------------------------------------------------------------------


def mine_discovery_tags(
    conn,
    *,
    env: str,
    limit: int = 500,
    min_count: Optional[int] = None,
) -> dict[str, Any]:
    """LLM-cluster operator comments → ``proposed`` tag rows.

    Existing tags (active / proposed / rejected) are passed to the LLM as the
    known vocabulary; semantically overlapping reasons must not be re-proposed.
    ``INSERT OR IGNORE`` semantics make repeat mining idempotent either way.
    """
    threshold = min_count if min_count is not None else tag_mine_min_count()
    events = learning_store.list_learning_events(
        conn, env=env, event_types=(ddl.SHORTLIST_DECISION_EVENT,), limit=limit,
    )
    events = learning_store.filter_events_within_days(
        events, learning_store.learning_window_days(),
    )
    comments: list[dict[str, Any]] = []
    for ev in events:
        payload = ev.get("payload") or {}
        comment = str(payload.get("comment") or "").strip()
        if comment:
            comments.append({
                "action": payload.get("action"),
                "comment": comment[:300],
            })
    if len(comments) < threshold:
        return {
            "skipped": True,
            "reason": "not enough comments to mine",
            "comments": len(comments),
            "min_count": threshold,
        }
    existing = conn.execute(
        "SELECT tag, label_zh, status FROM discovery_decision_tags",
    ).fetchall()
    known = [
        {"tag": r["tag"], "label_zh": r["label_zh"], "status": r["status"]}
        for r in existing
    ]
    prompt = (
        "You cluster operator comments about Instagram KOL shortlist decisions "
        "into recurring REASONS so they can become quick-select tags.\n\n"
        f"KNOWN TAGS (do NOT re-propose these or semantic duplicates):\n"
        f"{json.dumps(known, ensure_ascii=False)}\n\n"
        "Output ONLY a JSON array. Each element:\n"
        '{"tag": "<ascii_snake_case_slug>", "label_zh": "<short Chinese label>", '
        '"action_scope": "approve|remove|transfer|any", "count": <int occurrences>, '
        '"examples": ["<comment excerpt>", ...]}\n\n'
        f"Only include reasons appearing in at least {threshold} comments. "
        "If nothing qualifies, output [].\n\n"
        f"COMMENTS_JSON:\n{json.dumps(comments, indent=2, ensure_ascii=False)}"
    )
    try:
        raw = learning_llm.invoke_learning_llm(prompt)
    except learning_llm.LearningLlmError:
        raise
    except Exception as exc:
        raise learning_llm.LearningLlmError(
            f"LLM tag mining failed: {exc}",
        ) from exc
    text = learning_llm.strip_markdown_fences(raw).strip()
    try:
        items = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise learning_llm.LearningLlmError(
            f"tag mining output is not valid JSON: {text[:200]!r}",
        ) from exc
    if not isinstance(items, list):
        raise learning_llm.LearningLlmError("tag mining output must be a JSON array")
    proposed: list[str] = []
    ignored: list[dict[str, Any]] = []
    comment_texts = [c["comment"] for c in comments]
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            count = int(item.get("count") or 0)
        except (TypeError, ValueError):
            count = 0
        if count < threshold:
            ignored.append({"tag": item.get("tag"), "reason": "below_min_count"})
            continue
        # Deterministic guard against LLM-inflated counts: each cited example
        # must actually appear in the source comments (substring, both ways
        # because excerpts may be shortened).
        examples = [str(e) for e in (item.get("examples") or []) if str(e).strip()]
        verified = [
            e for e in examples
            if any(e[:80] in c or c[:80] in e for c in comment_texts)
        ]
        if not verified:
            ignored.append({"tag": item.get("tag"), "reason": "examples_not_found_in_comments"})
            continue
        try:
            result = decision_tags.propose_tag(
                conn,
                tag=str(item.get("tag") or ""),
                label_zh=str(item.get("label_zh") or ""),
                action_scope=str(item.get("action_scope") or "any"),
                evidence=verified,
            )
        except ValueError as exc:
            ignored.append({"tag": item.get("tag"), "reason": str(exc)})
            continue
        if result["created"]:
            proposed.append(result["tag"])
        else:
            ignored.append({"tag": result["tag"], "reason": "already_exists"})
    return {
        "comments_seen": len(comments),
        "proposed_tags": proposed,
        "proposed_count": len(proposed),
        "ignored": ignored,
    }


# ---------------------------------------------------------------------------
# Category inference (LLM for unmapped SKUs)
# ---------------------------------------------------------------------------


def infer_missing_product_categories(
    conn,
    *,
    env: str,
    updated_by: str,
    limit: int = 500,
) -> dict[str, Any]:
    """LLM-categorize SKUs seen in decision events but absent from the map."""
    events = learning_store.list_learning_events(
        conn, env=env, event_types=(ddl.SHORTLIST_DECISION_EVENT,), limit=limit,
    )
    pending: dict[str, dict[str, Any]] = {}
    for ev in events:
        payload = ev.get("payload") or {}
        sku = str(payload.get("sku") or "").strip()
        if not sku or sku in pending:
            continue
        if ddl.get_category_for_sku(conn, sku=sku):
            continue
        pending[sku] = {
            "sku": sku,
            "product_name": payload.get("product_name"),
            "pitch_excerpt": payload.get("pitch_excerpt"),
        }
    if not pending:
        return {"skipped": True, "reason": "no unmapped skus"}
    known_categories = sorted({
        str(r["category"])
        for r in conn.execute("SELECT DISTINCT category FROM product_category_map")
    })
    prompt = (
        "Assign a product CATEGORY to each SKU below (KOL marketing context).\n"
        "Prefer reusing an existing category when it fits; otherwise create a "
        "new concise ascii snake_case category slug (e.g. `ergonomic_chair`).\n\n"
        f"EXISTING CATEGORIES: {json.dumps(known_categories, ensure_ascii=False)}\n\n"
        "Output ONLY a JSON object mapping sku → "
        '{"category": "<slug>", "confidence": <0..1>}.\n\n'
        f"PRODUCTS_JSON:\n{json.dumps(list(pending.values()), indent=2, ensure_ascii=False)}"
    )
    try:
        raw = learning_llm.invoke_learning_llm(prompt)
    except learning_llm.LearningLlmError:
        raise
    except Exception as exc:
        raise learning_llm.LearningLlmError(
            f"LLM category inference failed: {exc}",
        ) from exc
    text = learning_llm.strip_markdown_fences(raw).strip()
    try:
        mapping = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise learning_llm.LearningLlmError(
            f"category inference output is not valid JSON: {text[:200]!r}",
        ) from exc
    if not isinstance(mapping, dict):
        raise learning_llm.LearningLlmError("category inference output must be a JSON object")
    written: list[dict[str, Any]] = []
    for sku, info in mapping.items():
        if sku not in pending or not isinstance(info, dict):
            continue
        category = str(info.get("category") or "").strip()
        if not category:
            continue
        try:
            confidence = float(info.get("confidence")) if info.get("confidence") is not None else None
        except (TypeError, ValueError):
            confidence = None
        result = ddl.set_product_category(
            conn,
            sku=sku,
            category=category,
            source="llm",
            updated_by=updated_by,
            confidence=confidence,
            product_name=str(pending[sku].get("product_name") or "") or None,
        )
        written.append(result)
    return {"unmapped_seen": len(pending), "written": written, "written_count": len(written)}


__all__ = [
    "DISCOVERY_LEARNING_MARKER",
    "apply_approved_discovery_proposal",
    "build_learned_discovery_criteria",
    "discovery_learning_batch_size",
    "discovery_overview_stats",
    "distill_discovery_criteria_llm",
    "find_pending_discovery_proposal",
    "infer_missing_product_categories",
    "list_consumed_decision_event_ids",
    "list_pending_discovery_proposals",
    "merge_discovery_policy_content",
    "mine_discovery_tags",
    "pending_discovery_reserved_event_ids",
    "propose_discovery_learning_approval",
    "tag_mine_min_count",
]
