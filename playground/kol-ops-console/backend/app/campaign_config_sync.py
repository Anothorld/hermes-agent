"""Helpers for keeping CAL's ``campaign_config`` in sync with the console
side, and for guarding fresh-session draft paths against running with an
incomplete CAL row.

Two exports:

* :func:`build_campaign_config_upsert_body` — pure function that turns a
  console-side ``products`` row + ``StartCampaignBody`` into the
  ``CampaignConfigUpsertBody`` payload the bridge expects. Extracted from
  the inline construction at ``routers/campaigns.py`` launch handler so
  the same shape is used everywhere.
* :func:`assert_campaign_config_complete` — read-validate guard used by
  the three fresh-session brief paths (redraft, refine, preview-draft)
  before invoking the gateway. Raises HTTP 400 with a machine-readable
  body if any required field is missing in CAL. Does NOT re-upsert; that
  would silently clobber operator edits made via
  ``PATCH /campaigns/{id}/config``.

The asymmetry between the launch path (writes to CAL) and the draft
paths (only validate) is deliberate: launch is the single
canonical-write moment for these fields; everything afterwards either
reads CAL or goes through the operator-driven PATCH endpoint.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, TYPE_CHECKING

from fastapi import HTTPException, status

from .bridge_client import BridgeClient, BridgeError

if TYPE_CHECKING:  # pragma: no cover — typing-only import
    from .routers.campaigns import StartCampaignBody


def build_campaign_config_upsert_body(
    *,
    product: sqlite3.Row,
    body: "StartCampaignBody",
    selected_variants: list[dict[str, Any]],
    sku_ref: str,
) -> dict[str, Any]:
    """Build the ``CampaignConfigUpsertBody`` payload at launch time.

    Mirrors the inline construction that used to live in
    ``routers/campaigns.py`` (start-campaign handler). Pulled out so the
    same shape is testable and so the launch flow can be edited without
    accidentally diverging from the bridge schema.

    ``selected_variants`` is the operator-filtered variant list (output
    of ``_selected_variants(product, body.product_variant_ids)``);
    passed in rather than recomputed so this helper stays free of the
    router's private helpers.
    """
    color_variant_policy: str | None = None
    if selected_variants:
        labels = [v.get("label") or v.get("id") for v in selected_variants]
        color_variant_policy = "operator_selected: " + " | ".join(
            str(x) for x in labels
        )
    extra_notes_chunks: list[str] = []
    if product["selling_points"]:
        extra_notes_chunks.append(
            f"# selling_points\n{product['selling_points']}"
        )
    if selected_variants:
        extra_notes_chunks.append(
            "# product_variants\n"
            + json.dumps(selected_variants, ensure_ascii=False)
        )
    extra_notes = "\n\n".join(extra_notes_chunks) or None

    upsert_body: dict[str, Any] = {
        "label": product["name"],
        "product_display_name": body.product_display_name,
        "sku_whitelist": [sku_ref],
        "paid_ceiling": body.budget_per_kol,
        "contract_required": True,
        "test_mode_to": body.test_mode_to,
        "env": body.env,
    }
    product_url = product["url"] if "url" in product.keys() else None
    if product_url:
        upsert_body["product_url"] = product_url
    if color_variant_policy:
        upsert_body["color_variant_policy"] = color_variant_policy
    if extra_notes:
        upsert_body["extra_notes"] = extra_notes
    if body.deliverable_platforms:
        upsert_body["deliverable_platforms"] = body.deliverable_platforms
    if body.deliverable_count_per_platform is not None:
        upsert_body["deliverable_count_per_platform"] = (
            body.deliverable_count_per_platform
        )
    if body.audit_standards_md and body.audit_standards_md.strip():
        upsert_body["audit_standards_md"] = body.audit_standards_md.strip()
    return upsert_body


# Fields the cold-outreach / reengagement-outreach skills hard-require
# to write a draft. ``product_display_name`` is the load-bearing one — the
# Step 2b SKU-leak guard in
# ``skills/social-media/kol-cold-outreach/SKILL.md`` will escalate
# rather than fall back to the SKU code.
DEFAULT_REQUIRED_FIELDS: tuple[str, ...] = ("product_display_name",)

# Strings that look like a campaign id but actually carry no information.
# Seen historically when a JS caller built a URL via
# ``encodeURIComponent(null|undefined|NaN)`` and the resulting 4–9 char
# sentinel string travelled all the way to a CAL write. The bridge
# rejects these at its boundary (plugin_api._reject_null_sentinel_…),
# but we also reject upfront here so the console returns a tight 400
# with a frontend-routable error code instead of a misleading
# downstream "campaign_config is missing in CAL" message.
_NULL_SENTINEL_CAMPAIGN_IDS: frozenset[str] = frozenset({
    "null", "undefined", "nan", "none",
})


async def assert_campaign_config_complete(
    bridge: BridgeClient,
    campaign_id: str,
    *,
    required: tuple[str, ...] = DEFAULT_REQUIRED_FIELDS,
) -> dict[str, Any]:
    """Read CAL's ``campaign_config`` row and refuse to proceed if any
    required field is null/empty.

    Used by the three fresh-session draft endpoints (redraft, refine,
    preview-draft) before they invoke the gateway. The point is to fail
    *fast* and *clearly* on the operator's HTTP call instead of letting
    the agent run for 30-60s and open a
    ``campaign_config_missing_required_product_facts`` escalation that
    the operator then has to triage manually.

    On success: returns the campaign_config dict so callers can reuse
    it (e.g., to read ``test_mode_to``).

    On failure: raises ``HTTPException(400)`` with a structured body the
    frontend can route to the right CTA (``PATCH /campaigns/{id}/config``).

    On bridge unreachable: raises ``HTTPException(502)`` with
    ``cal_unreachable``. We do NOT swallow — the alternative is letting
    the draft run start against a phantom config.
    """
    # Fast-fail on caller bugs that interpolated a null/undefined into
    # the campaign id (most often a console route that built the URL
    # via ``encodeURIComponent(null)`` → ``"null"``). Without this guard
    # the operator sees a confusing "campaign_config is missing in CAL"
    # error and is told to use the campaign edit form, which they have
    # no way to reach because the URL itself is broken.
    cid_str = "" if campaign_id is None else str(campaign_id).strip()
    if not cid_str or cid_str.lower() in _NULL_SENTINEL_CAMPAIGN_IDS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            {
                "code": "invalid_campaign_id",
                "message": (
                    f"campaign_id is the sentinel value {campaign_id!r} — "
                    "the console built this request from a missing or "
                    "null campaign context. Reload the page from a "
                    "campaign-scoped view (kanban / candidates) and "
                    "retry."
                ),
            },
        )
    try:
        config = await bridge.get_campaign(campaign_id)
    except BridgeError as exc:
        # Bridge's GET /campaigns/{id} returns 404 when the row is
        # missing — treat that the same as "row exists but every
        # required field is empty" so the operator gets a coherent
        # message instead of a 502 surfaced from the bridge.
        if getattr(exc, "status", None) == 404:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                {
                    "code": "campaign_config_incomplete",
                    "message": (
                        "campaign_config is missing in CAL — the "
                        "campaign may not have launched cleanly. Use "
                        "the campaign edit form to write the required "
                        "fields, then retry."
                    ),
                    "missing_fields": list(required),
                    "fix_endpoint": f"PATCH /campaigns/{campaign_id}/config",
                },
            ) from exc
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            {
                "code": "cal_unreachable",
                "message": (
                    "cannot reach CAL to validate campaign_config; "
                    "retry in a moment"
                ),
                "bridge_error": str(exc),
            },
        ) from exc
    if not isinstance(config, dict):
        # Bridge contract guarantees a dict on 200 — anything else means
        # the proxy/transport got into a weird state. Fail closed.
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            {
                "code": "cal_unexpected_response",
                "message": "bridge returned non-dict campaign_config",
            },
        )
    missing: list[str] = []
    for field in required:
        value = config.get(field)
        if value is None:
            missing.append(field)
            continue
        if isinstance(value, str) and not value.strip():
            missing.append(field)
    if missing:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            {
                "code": "campaign_config_incomplete",
                "message": (
                    "campaign_config is missing required field(s) for "
                    "this draft. Fill them via the campaign edit form, "
                    "then retry — the agent will not invent values."
                ),
                "missing_fields": missing,
                "fix_endpoint": f"PATCH /campaigns/{campaign_id}/config",
            },
        )
    return config
