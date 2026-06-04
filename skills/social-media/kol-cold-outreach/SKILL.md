---
name: kol-cold-outreach
description: First-touch outreach email for new KOL prospects (outreach path=cold).
tags: ["kol", "outreach", "cold", "initial"]
---

# kol-cold-outreach

Used after Console shortlist approval when `relationship.total_collabs == 0`
or `outreach_path=cold`. Parent run reads this SKILL and executes the Procedure
itself — there is no CLI `run-skill`.

## Runtime contract

- **CAL I/O:** native **`terminal`** with
  `plugins/kol-ops-bridge/scripts/kol_bridge_tool.py` (one subcommand per call).
- **Forbidden:** `execute_code` + subprocess, `curl` / `urllib` to the bridge,
  reading bridge `.py` source, reading `~/.hermes/**/.env` for keys,
  `write-facts` / `write-facts-multi` on `approval.reply_draft`.
- **Forbidden:** `ingest-confirmed-candidate` — identity already exists after
  shortlist approval; use `get-dispatch-context` (you receive `identity_id`) and
  `persist-initial-outreach-draft`. Ingest JSON shape is discovery-only; see
  `instagram-kol-discovery/references/bridge-cli-json-payloads.md`.

## Procedure

### 1 — Context

```bash
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py get-dispatch-context \
  --identity-id <id> --campaign-id <cid> --env <TEST|LIVE>

python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py get-identity \
  --identity-id <id> --env <TEST|LIVE>
```

Read `campaign_config.product_pitch`, identity creator-brief facts, and
`learning_hints` when present.

### 2 — Draft content

Return a **content-only** envelope (parent run persists it):

| Field | Rule |
|-------|------|
| `subject` | Clear collab subject (not `Re:` unless true thread exists) |
| `body` | HTML (`<p>`…) personalized with creator-brief facts |
| `to` | Verified `primary_email` only — never invent |

Optional metadata: `kind: initial_outreach`, `low_personalization` + `reason` when
brief enrichment failed.

### 3 — Persist (parent run or this skill's terminal step)

**Do not** hand-write `approval.reply_draft` facts. Use the toolized CLI:

```bash
cat > /tmp/outreach_persist_<identity_id>.json <<'JSON'
{
  "identity_id": <id>,
  "campaign_id": "<cid>",
  "child_skill": "kol-cold-outreach",
  "primary_lane": "commerce",
  "primary_goal": "outreach",
  "child_envelope": {
    "subject": "POVISON x @handle — …",
    "body": "<p>…</p>",
    "to": "verified@email.com"
  }
}
JSON

python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py persist-initial-outreach-draft \
  --env <TEST|LIVE> --json @/tmp/outreach_persist_<identity_id>.json
```

**Thread anchors (auto — do not randomize):**

| Field | Value |
|-------|--------|
| `source_message_id` | `draft:outreach_<campaign_id>_<identity_id>` |
| `thread_id` | `outreach_<campaign_id>_<identity_id>` |

The CLI sets these and calls `POST /reply-drafts/persist` — same path as
`persist-reply-draft` but without guessing HTTP URLs.

### 4 — Ack event (optional, parent run)

```bash
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py write-event \
  --identity-id <id> --campaign-id <cid> --env <env> \
  --event-type kol_initial_outreach_draft_ready \
  --actor skill:kol-cold-outreach \
  --json @/tmp/event_<id>.json
```

Prefer `--json @file` — not inline shell JSON.

## Pitfalls

- **Success:** Using `write-facts-multi` for `approval.reply_draft` — Bridge rejects it.
- **Success:** `urllib` + hardcoded `BRIDGE_KEY` after CLI auth errors — stop and report missing env.
- **Failure:** `cd hermes-agent` with no subcommand — triggers doc injection, empty terminal output.
- **Failure:** Random `uuid` in `source_message_id` — breaks idempotency; use stable `draft:outreach_*` only.

## Examples

**Success:** `persist-initial-outreach-draft` returns `{"ok":true,"draft_event_id":...}`.

**Failure:** `write-facts-multi` with `approval.reply_draft` → `must be written via persist-reply-draft`.
