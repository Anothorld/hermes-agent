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
  shortlist approval; use `get-dispatch-context --view agent` (you receive `identity_id`) and
  `persist-initial-outreach-draft`. Ingest JSON shape is discovery-only; see
  `instagram-kol-discovery/references/bridge-cli-json-payloads.md`.

## Procedure

### 1 — Context

```bash
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py get-dispatch-context \
  --identity-id <id> --campaign-id <cid> --env <TEST|LIVE> --view agent

python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py get-identity \
  --identity-id <id> --env <TEST|LIVE>
```

Read `campaign_config.product_pitch`, identity creator-brief facts, and
`learning_hints` when present.

### 1.5 — Style + brief preambles (mandatory)

Build the LLM prompt header before drafting (see
`references/shared/style-and-brief-preambles.md`):

1. Invoke `kol-email-style-loader` (company_style + user_style). Pass the
   operator id as `--owner-user-id <current_user_id>` when the run carries one
   (brief `requested_by_user_id`); if no user id is in scope, load
   `company_style` only — `user_style` without an owner returns an empty block,
   never an error. Prepend the returned block as `[P0]`.
2. Invoke `kol-creator-brief-loader`; prepend as `[P0.1]`.
3. Loader failures must NOT block drafting — fall back to a generic draft and
   set `low_personalization` + `reason`.

### 2 — Draft content

Return a **content-only** envelope (parent run persists it):

| Field | Rule |
|-------|------|
| `subject` | Clear collab subject (not `Re:` unless true thread exists) |
| `body` | **HTML only.** Wrap every paragraph in `<p>…</p>`; the product must appear as a real `<a href="<product_url>">…</a>` link (use `campaign_config.product_url` / the pitch URL). Never plain text, never a bare URL. |
| `to` | Verified `primary_email` only — never invent |
| `html` | `true` |
| `kind` | `initial_outreach` |

`low_personalization` + `reason` when brief enrichment failed.

**Format contract (hard):** the body is sent verbatim as the operator's Gmail
draft. A plain-text marketing paragraph or a missing product link is a defect.
Structure: warm opening line → one specific creator-content observation →
product intro with the `<a href>` link → soft collaboration ask → sign-off.

### 2.5 — Humanizer (mandatory final pass)

Before returning the envelope, apply `humanizer` in email mode to subject + body.
Preserve all P0 facts, the product `<a href>` link, and HTML structure; only
polish wording. Keep the warm, considerate voice from the style block.

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
    "body": "<p>Hi …,</p><p>… <a href=\"https://www.povison.com/…\">product</a> …</p><p>Best,<br>POVISON Team</p>",
    "to": "verified@email.com",
    "html": true,
    "kind": "initial_outreach"
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
- **Failure:** Plain-text body (`Hi …\n\nWe just launched …`) instead of `<p>` HTML, or the product mentioned as bare text/URL instead of `<a href>`. The draft is sent as-is; this is the POVISON 683 defect. The bridge will auto-wrap raw paragraphs as a last resort, but it cannot invent the product link — you must include it.
- **Failure:** Skipping `kol-email-style-loader` / `humanizer` — produces an off-voice, template-y draft.

## Examples

**Success:** `persist-initial-outreach-draft` returns `{"ok":true,"draft_event_id":...}`.

**Failure:** `write-facts-multi` with `approval.reply_draft` → `must be written via persist-reply-draft`.
