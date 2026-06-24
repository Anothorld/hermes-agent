"""Slim dispatch-context bundle for agent tool calls (--view agent)."""

from __future__ import annotations

import copy
from typing import Any, Mapping

# Keys child skills read from campaign_config (see kol-* SKILL.md procedures).
AGENT_CAMPAIGN_CONFIG_KEYS: tuple[str, ...] = (
    "campaign_id",
    "env",
    "label",
    "status",
    "product_display_name",
    "product_url",
    "product_pitch",
    "product_specs",
    "product_link",
    "barter_policy",
    "paid_ceiling",
    "paid_target_budget",
    "paid_ratio_override",
    "product_unit_price",
    "sku_whitelist",
    "variant_candidates",
    "variant_candidates_json",
    "color_variant_policy",
    "deliverable_platforms",
    "deliverable_count_per_platform",
    "campaign_deliverables_json",
    "contract_required",
    "defer_terms_to_contract",
    "strict_explicit_accept",
    "implicit_accept_enabled",
    "default_compensation_mode",
    "test_mode_to",
    "extra_notes",
    "nox_integration_json",
    "nox_quota_enabled",
)

IDENTITY_AGENT_KEYS: tuple[str, ...] = (
    "id",
    "identity_id",
    "primary_handle",
    "platform",
    "primary_email",
    "display_name",
    "region",
    "language",
)

_GOAL_KEEP_KEYS: tuple[str, ...] = (
    "goal",
    "status",
    "lane",
    "missing_facts",
    "blocking_escalation_id",
)

_PROVENANCE_SUFFIXES: tuple[str, ...] = (
    "_source",
    "_discovered_at",
    "_discovered_url",
    "_at",
    "_url",
)

# Creator-brief passive freshness anchors (kol-creator-brief-loader 90-day check).
_BRIEF_FRESHNESS_KEEP_KEYS: frozenset[str] = frozenset({
    "identity.content_pillars_discovered_at",
    "identity.signature_hooks_discovered_at",
    "identity.voice_descriptors_discovered_at",
    "identity.hero_post_url_discovered_at",
    "identity.hero_post_note_discovered_at",
    "identity.recommendation_reason_discovered_at",
})


def _strip_provenance_keys(facts: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in facts.items():
        if key in _BRIEF_FRESHNESS_KEEP_KEYS:
            out[key] = value
            continue
        if any(key.endswith(suffix) for suffix in _PROVENANCE_SUFFIXES):
            continue
        out[key] = value
    return out


def _slim_goals(goals: Any) -> list[dict[str, Any]]:
    if not isinstance(goals, list):
        return []
    slim: list[dict[str, Any]] = []
    for item in goals:
        if not isinstance(item, dict):
            continue
        slim.append({k: item[k] for k in _GOAL_KEEP_KEYS if k in item})
    return slim


def _slim_campaign_config(cfg: Any) -> dict[str, Any] | None:
    if not isinstance(cfg, dict):
        return None
    return {k: cfg[k] for k in AGENT_CAMPAIGN_CONFIG_KEYS if k in cfg}


def _slim_relationship(rel: Any) -> dict[str, Any] | None:
    if not isinstance(rel, dict):
        return None
    return {k: v for k, v in rel.items() if k != "collab_history"}


def _redact_campaign_facts(facts: Any) -> dict[str, Any]:
    if not isinstance(facts, dict):
        return {}
    out = dict(facts)
    draft = out.get("approval.reply_draft")
    if isinstance(draft, dict):
        draft_copy = copy.deepcopy(draft)
        body = draft_copy.get("draft")
        if isinstance(body, dict) and "body" in body:
            body = dict(body)
            body["body"] = "[redacted — use get-email-conversation for full thread]"
            draft_copy["draft"] = body
        out["approval.reply_draft"] = draft_copy
    return out


def slim_identity_for_agent(raw: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not raw:
        return None
    out = {k: raw[k] for k in IDENTITY_AGENT_KEYS if k in raw}
    if "identity_id" not in out and "id" in out:
        out["identity_id"] = out["id"]
    return out or None


def slim_dispatch_context_for_agent(
    bundle: Mapping[str, Any],
    *,
    identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a token-efficient dispatch snapshot for agent runs."""
    out: dict[str, Any] = {
        "identity_id": bundle.get("identity_id"),
        "campaign_id": bundle.get("campaign_id"),
        "env": bundle.get("env"),
        "view": "agent",
    }
    if identity is not None:
        slim_id = slim_identity_for_agent(identity)
        if slim_id:
            out["identity"] = slim_id

    goals = _slim_goals(bundle.get("goals"))
    if goals:
        out["goals"] = goals

    rel = _slim_relationship(bundle.get("relationship"))
    if rel:
        out["relationship"] = rel

    reusable = bundle.get("reusable_facts")
    if isinstance(reusable, dict):
        facts = reusable.get("facts")
        if isinstance(facts, dict):
            out["reusable_facts"] = {
                "identity_id": reusable.get("identity_id"),
                "facts": _strip_provenance_keys(facts),
            }

    hints = bundle.get("learning_hints")
    if hints is not None:
        out["learning_hints"] = hints

    cfg = _slim_campaign_config(bundle.get("campaign_config"))
    if cfg:
        out["campaign_config"] = cfg

    campaign_facts = _redact_campaign_facts(bundle.get("campaign_facts"))
    if campaign_facts:
        out["campaign_facts"] = campaign_facts

    candidate = bundle.get("candidate")
    if candidate is not None:
        out["candidate"] = candidate

    identity_facts = bundle.get("identity_facts")
    if isinstance(identity_facts, dict):
        out["identity_facts"] = _strip_provenance_keys(identity_facts)

    return out
