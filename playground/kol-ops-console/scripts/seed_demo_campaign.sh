#!/usr/bin/env bash
# Seed demo data for kol-ops-console (TEST environment only).
#
# Creates one campaign with a representative cross-section of KOLs so the
# Kanban, Approvals, Candidates, and Escalations views all show non-empty
# state out of the box. Safe to re-run — the bridge upsert helpers are
# idempotent on (campaign_id, primary_handle).
#
# Requirements:
#   - The bridge HTTP service is running and reachable on $BRIDGE_URL
#     (default http://127.0.0.1:8090).
#   - $HERMES_KOL_BRIDGE_KEY (or $BRIDGE_KEY) is exported with the same
#     value the bridge was started with.
#
# Usage:
#   scripts/seed_demo_campaign.sh [campaign_id]
#
# All writes target env=TEST. Refuses to run against LIVE.

set -euo pipefail

CID="${1:-DEMO-2025-Q1}"
BRIDGE_URL="${BRIDGE_URL:-http://127.0.0.1:8090}"
KEY="${HERMES_KOL_BRIDGE_KEY:-${BRIDGE_KEY:-}}"
ENV="TEST"

if [[ -z "$KEY" ]]; then
  echo "error: HERMES_KOL_BRIDGE_KEY (or BRIDGE_KEY) must be set" >&2
  exit 2
fi

H=(-H "Content-Type: application/json" -H "X-Bridge-Key: ${KEY}")

post() {
  local path="$1"; shift
  curl -sS --fail-with-body "${H[@]}" -X POST "${BRIDGE_URL}${path}" "$@"
  echo
}

put() {
  local path="$1"; shift
  curl -sS --fail-with-body "${H[@]}" -X PUT "${BRIDGE_URL}${path}" "$@"
  echo
}

upsert_identity() {
  local handle="$1"
  local primary_email="${2:-}"
  local extra="${3:-{\}}"
  jq -n --arg h "$handle" --arg e "$primary_email" --argjson x "$extra" \
       '{primary_handle:$h, primary_email:($e|select(.!="")), env:"TEST"} * $x'
}

echo ">> seeding TEST campaign ${CID} via ${BRIDGE_URL}"

# ---- 1. Campaign config ----------------------------------------------------
put "/campaigns/${CID}" -d @- <<JSON
{
  "label": "Demo Q1 — seeded fixture",
  "env": "${ENV}",
  "sku_whitelist": ["SKU-LAMP", "SKU-CHAIR", "SKU-TABLE"],
  "deliverable_platforms": ["instagram"],
  "deliverable_count_per_platform": 1,
  "paid_ceiling": 800.0,
  "contract_required": true,
  "followup_intervals": {
    "cold_outreach": 48,
    "interest_qualification": 24,
    "product_selection": 36,
    "compensation_negotiation": 24,
    "contract_signing": 48,
    "shipping_intake": 24,
    "logistics_tracking": 72,
    "content_review": 48,
    "go_live_boost": 24,
    "reengagement": 72
  }
}
JSON

# Helper: create + return identity_id via /identities/by-handle lookup.
_upsert() {
  local handle="$1"; local email="${2:-}"
  post /identities "${H[@]}" -d "$(upsert_identity "$handle" "$email")" \
    | jq -r '.identity_id'
}

# Helper: write facts under a namespace.
_facts() {
  local iid="$1"; local ns="$2"; local body="$3"
  post "/facts/${iid}" -d @- <<JSON
{"campaign_id":"${CID}","env":"${ENV}","namespace":"${ns}","facts":${body},"source":"seed"}
JSON
}

# Helper: add candidate row (discovery pipeline).
_cand() {
  local iid="$1"; local source="${2:-discovery}"
  post "/campaigns/${CID}/candidates" -d @- <<JSON
{"identity_id": ${iid}, "source": "${source}", "env": "${ENV}"}
JSON
}

# ---- 2. KOLs --------------------------------------------------------------

echo ">> A. fresh prospect (cold)"
A=$(_upsert demo_alice alice@example.com)
_cand "$A"

echo ">> B. interest confirmed, awaiting product pick"
B=$(_upsert demo_bob bob@example.com)
_cand "$B"
_facts "$B" offer '{"offer.outreach_sent":true,"offer.interest_signal":"confirmed"}'

echo ">> C. repeat KOL (1 prior successful collab)"
C=$(_upsert demo_carol carol@example.com)
post "/identities/${C}/archive" -d @- <<JSON
{"campaign_id": "DEMO-PRIOR", "env": "${ENV}",
 "outcome": "success", "preferred_skus": ["SKU-LAMP"],
 "preferred_mode": "gifted", "delivery_quality": 0.95}
JSON
_cand "$C"
post "/campaigns/${CID}/candidates/resolve-relationships" -d '{"env":"TEST"}'

echo ">> D. compensation over ceiling → pending approval"
D=$(_upsert demo_dan dan@example.com)
_cand "$D"
_facts "$D" offer '{"offer.outreach_sent":true,"offer.interest_signal":"confirmed","offer.sku_locked":"SKU-LAMP","offer.color_or_variant_locked":true,"offer.fit_confirmed":true,"offer.deliverable_platforms":["instagram"],"offer.deliverable_count_per_platform":1,"offer.usage_rights_discussed":true,"offer.compensation_mode":"paid","offer.kol_quote":1500.0}'
_facts "$D" approval '{"approval.over_budget_request":{"amount":1500,"sku":"SKU-LAMP"}}'

echo ">> E. open escalation (off-whitelist SKU request)"
E=$(_upsert demo_eve eve@example.com)
_cand "$E"
_facts "$E" offer '{"offer.outreach_sent":true,"offer.interest_signal":"confirmed"}'
post /escalations -d @- <<JSON
{"identity_id": ${E}, "campaign_id": "${CID}", "env": "${ENV}",
 "goal": "product_selection",
 "reason": "kol_demands_off_whitelist",
 "severity": "high",
 "question_to_operator": "KOL wants a SKU not in the whitelist — allow?"}
JSON

echo ">> F. contract pending"
F=$(_upsert demo_frank frank@example.com)
_cand "$F"
_facts "$F" offer '{"offer.outreach_sent":true,"offer.interest_signal":"confirmed","offer.sku_locked":"SKU-CHAIR","offer.color_or_variant_locked":true,"offer.fit_confirmed":true,"offer.deliverable_platforms":["instagram"],"offer.deliverable_count_per_platform":1,"offer.usage_rights_discussed":true,"offer.compensation_mode":"gifted","offer.agreed_terms":{"mode":"gifted"}}'

echo ">> G. shipping intake (three-lane parallel)"
G=$(_upsert demo_gwen gwen@example.com)
_cand "$G"
_facts "$G" offer '{"offer.outreach_sent":true,"offer.interest_signal":"confirmed","offer.sku_locked":"SKU-TABLE","offer.color_or_variant_locked":true,"offer.fit_confirmed":true,"offer.deliverable_platforms":["instagram"],"offer.deliverable_count_per_platform":1,"offer.usage_rights_discussed":true,"offer.compensation_mode":"gifted","offer.agreed_terms":{"mode":"gifted"}}'
_facts "$G" fulfillment '{"fulfillment.contract_state":"signed"}'

echo ">> H. reengagement (lapsed)"
H_ID=$(_upsert demo_hank hank@example.com)
post "/identities/${H_ID}/archive" -d @- <<JSON
{"campaign_id": "DEMO-PRIOR", "env": "${ENV}",
 "outcome": "success", "preferred_skus": ["SKU-TABLE"],
 "preferred_mode": "paid", "delivery_quality": 0.9}
JSON
_cand "$H_ID" reengagement

echo ">> I. archived (rejected) — leave as 'rejected' candidate status"
I_ID=$(_upsert demo_iris iris@example.com)
_cand "$I_ID"
# Bridge does not currently expose a 'set candidate status' route; the
# console will display this row under 'pending' until a downstream skill
# rejects it. Left here so the demo includes a low-fit signal.
_facts "$I_ID" identity '{"identity.tier":"low_fit"}'

echo ">> seed complete."
echo "   - 9 KOLs in campaign ${CID}"
echo "   - bridge URL: ${BRIDGE_URL}"
echo "   - launch console: cd playground/kol-ops-console && ./start.sh"
