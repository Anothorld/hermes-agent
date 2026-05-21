# KOL Ops — v2.4 Goal-Driven Architecture

> Operator-facing reference for the KOL outreach pipeline. Source of truth
> for runtime semantics is the code (`plugins/kol-ops-bridge/`); this doc
> is the navigation map.

## 1. Why goal-driven

Earlier iterations modelled the pipeline as a linear stage machine
(`outreach → negotiation → contract → ship → publish`). That broke down
when:

- multiple things happen in parallel (a KOL can negotiate price WHILE
  shipping is in transit when contract was signed early);
- partial states are common (deliverables agreed but compensation not);
- inbound replies don't fit a single "current stage".

v2.4 replaces the linear stage with **10 goals across 4 lanes**, each
goal independently tracked and recomputed from facts. The dispatcher
picks per-turn behavior by reading `active_goals_by_lane`, not a single
"current_stage" pointer.

## 2. Data model

### 2.1 Three-tier memory

| Tier | Tables | Lifetime |
|---|---|---|
| Identity | `kol_identity`, `kol_relationship` | persistent across campaigns |
| Campaign | `campaign_config`, `campaign_candidates`, `kol_goal_state` | per-campaign |
| Thread/event | `kol_facts`, `kol_conversation_events`, `kol_escalations` | per-engagement |
| Reference | `policy_documents` | versioned global |

### 2.2 Fact namespaces

**Exactly four** (CHECK-constrained on `kol_facts.fact_key`):

| Namespace | Purpose | Example keys |
|---|---|---|
| `identity.*` | KOL profile drift signals captured per-campaign | `identity.outreach_path`, `identity.contact_role_observed` |
| `offer.*` | Commerce-lane state | `offer.outreach_sent`, `offer.product_locked`, `offer.compensation_mode`, `offer.contract_signed` |
| `fulfillment.*` | Logistics + content production + publish state (the whole "shipped→delivered→brief→draft→live" arc) | `fulfillment.address_collected`, `fulfillment.tracking_no`, `fulfillment.brief_sent`, `fulfillment.draft_approved`, `fulfillment.golive_done` |
| `approval.*` | Human-decision-gated state and post-engagement outcomes | `approval.compensation_paid_above_ceiling`, `approval.identity_drift_review`, `approval.shipping_anomaly`, `approval.archival_outcome`, `approval.archival_done` |

> **Lane ≠ namespace.** Lanes (`commerce/fulfillment/publish/meta`) are
> goal groupings; namespaces are fact-key prefixes. Notably, the
> publish lane's facts live under `fulfillment.*`, and meta-lane
> facts (archival, escalation outcomes) live under `approval.*`.

### 2.3 Goals (10) and lanes (4)

| Lane | Goal | Satisfied when |
|---|---|---|
| commerce | outreach | `offer.outreach_sent=true` |
| commerce | interest_qualification | `offer.interest_signal ∈ {confirmed,negotiating,clarify}` |
| commerce | product_selection | `offer.product_locked` set + (if applicable) `offer.color_variant_locked` |
| commerce | deliverables_scope | `offer.deliverables_scope` matches campaign requirement |
| commerce | compensation_negotiation | `offer.compensation_agreed=true` (within ceiling, else gated by approval) |
| commerce | contract_signing | `offer.contract_signed=true` (when `campaign_config.contract_required`) |
| fulfillment | logistics | `fulfillment.delivered_confirmed=true` (or skipped if no product) |
| fulfillment | content_production | `fulfillment.brief_sent=true` |
| publish | content_review_and_golive | `fulfillment.golive_done=true` (or `engagement_aborted`) |
| meta | post_collab_archival | `approval.archival_outcome` written + identity/relationship updated |

Goal status: `inactive | unsatisfied | in_progress | satisfied | paused | aborted`.

## 3. Skill catalog

| Skill | Lane | Role |
|---|---|---|
| `instagram-kol-discovery` | (pre) | discover candidate handles |
| `kol-discovery-to-outreach-router` | (pre) | partition cold / reengagement / needs_review |
| `kol-cold-outreach` | commerce | first-touch draft for new KOL |
| `kol-reengagement-outreach` | commerce | re-engage prior collaborators |
| `kol-email-stage-classifier` | (transversal) | side-effect-free; classifies inbound, extracts fact candidates |
| `kol-reply-dispatcher` | (transversal) | reads dispatch-context bundle, delegates to child skill |
| `kol-interest-qualifier` | commerce | one clarifying question; never pre-commits interest |
| `kol-product-selector` | commerce | enforces `sku_whitelist` + `color_variant_policy` |
| `kol-deliverables-clarifier` | commerce | proposes scope from campaign_config; deflects price questions |
| `kol-pricing-strategist` | commerce | pure-function decision; emits JSON pricing recommendation |
| `kol-compensation-negotiator` | commerce | invokes strategist; A_draft / B_escalate branches |
| `kol-contract-coordinator` | commerce | initiate / chase / handle_response; cosmetic vs core clause split |
| `kol-shipping-intake` | fulfillment | one-line confirm of identity default vs full-fields ask |
| `kol-logistics-tracker` | fulfillment | send_tracking / chase_delivery / handle_response (anomaly→escalate) |
| `kol-brief-sender` | fulfillment | renders `brief_template_id` + `audit_standards_md` |
| `kol-content-reviewer` | publish | approve / request_revision (cap=2) / escalate |
| `kol-golive-and-boost` | publish | bundle send / URL capture / boost handoff |
| `kol-archival-writer` | meta | post-engagement; identity + relationship update |
| `kol-campaign-intake` | meta | parses operator brief into strict `campaign_config` |
| `kol-outreach-orchestrator-flow` | (doc) | onboarding map; not a runtime skill |

## 4. Bridge endpoints

Plugin: `plugins/kol-ops-bridge/`. Mounted at
`/api/plugins/kol-ops-bridge/`. SQLite WAL DB at
`~/.hermes/kol-ops-bridge/cal.db`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/identities/{id}/dispatch-context` | bundled read: goals + lanes + relationship + reusable_facts + campaign_config |
| POST | `/facts/{id}/multi` | atomic multi-namespace fact write |
| POST | `/campaigns/{id}/candidates/route-discovery` | discovery routing in one call |
| POST | `/escalations` | open escalation |
| PATCH | `/escalations/{id}` | resolve escalation (re-activates the blocked goal) |
| GET | `/escalations` | list escalations |
| POST | `/campaigns/{id}/config` | upsert campaign_config |

CLI wrapper: `plugins/kol-ops-bridge/scripts/kol_bridge_tool.py`. All
mutating subcommands require `--env {TEST,LIVE}`.

## 5. Operator approval surfaces

Three queues, kept separate so they don't collide:

1. **Chat shortlist** — one-time per campaign, after discovery routing.
2. **ApprovalsPage** — async queue of `approval.*` rows where
   `decision == "pending"` (cosmetic contract edits, identity drift,
   shipping anomalies, archival outcome, compensation cap reviews).
3. **EscalationConsolePage** — explicit human-in-the-loop questions
   from `kol_escalations` (off-policy SKU asks, contested clauses,
   off-cap pricing, KOL refusing to share address). An escalation
   never auto-resolves; the operator must explicitly resolve it via
   `PATCH /escalations/{id}`.

## 6. Notifications

`plugins/kol-ops-bridge/notifier.py` posts DingTalk markdown messages
with deep-links. Webhook + secret + console base URL are read from
environment variables (`HERMES_DINGTALK_WEBHOOK`,
`HERMES_DINGTALK_SECRET`, `HERMES_KOL_CONSOLE_BASE_URL`). Transport
failure is logged but never blocks a fact write.

## 7. Deterministic-ops policy

Every CRUD operation against persisted state goes through:
1. a CAL helper in `cal.py`, OR
2. a plugin endpoint (`plugin_api.py`), OR
3. the CLI wrapper (`kol_bridge_tool.py`).

Skills are **draft generators only** — they read via
`get-dispatch-context`, write via `write-facts-multi` or
`open_escalation`, and return JSON envelopes. They never run inline
SQL, shell, or `execute_code` against the DB.

## 8. Testing

`plugins/kol-ops-bridge/tests/`:

- `test_goal_machine.py` — goal recompute by namespace.
- `test_dispatcher_bundle.py` — `write_facts_multi` atomicity + dispatch-context shape.
- `test_discovery_router.py` — discovery partitioning.
- `test_archival_and_escalation.py` — archive/relationship + escalation lifecycle.
- `test_notifier.py` — DingTalk notifier (kind validation, signing, transport-fail tolerance).
- `test_e2e_flow.py` — happy-path lifecycle from cold outreach to golive.

Run: `python -m pytest plugins/kol-ops-bridge/tests/`.

## 9. Repo layout (allowed edit zones)

| Zone | What lives here | Editable |
|---|---|---|
| `hermes-agent/plugins/kol-ops-bridge/` | bridge plugin, schemas, CAL, scripts, tests | ✅ |
| `hermes-agent/skills/social-media/kol-*` | skills | ✅ |
| `docs/` | this kind of architecture doc | ✅ |
| `playground/` | scratch | ✅ |
| everywhere else (web, agent, gateway, tools…) | core | ❌ unless explicit operator approval |

## 10. Out of scope (explicitly NOT in the plugin/skill layer)

- The web operator console (`web/`) — frontend; a separate workstream.
- Gmail send (only drafts are written; sending is a manual operator action).
- KOL-side automation outside email (DM, paid ads attribution, etc.).
