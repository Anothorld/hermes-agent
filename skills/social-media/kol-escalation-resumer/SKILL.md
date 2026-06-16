---
name: kol-escalation-resumer
description: Resume operator-answered escalations via bridge CLI only.
trigger: Console PATCH resolve or dispatcher after escalation becomes resolved.
tags: ["kol", "escalation", "resume", "meta-lane", "policy-aware"]
---

# kol-escalation-resumer

Translates an operator-resolved escalation into CAL writes and a routing
decision so the parent goal can continue (or escalate again).

## Runtime contract

- **`--env` `TEST` or `LIVE`** on every bridge call.
- **Bridge-only writes** — use `plugins/kol-ops-bridge/scripts/kol_bridge_tool.py`.
  Gateway runs: absolute **`kol-bridge-cli`** per brief `# terminal_safety`.
- **Tool choice:** use the native **`terminal`** tool (one subcommand per call).
  Do **not** wrap the CLI in `execute_code` + `subprocess`.
- **CLI errors:** failures print JSON on **stdout**. Empty output + exit 2 means
  read stdout for `error`/`hint` — never switch to `execute_code` (guard blocks it).
- **Forbidden:** `execute_code`, `curl`, hand-rolled HTTP, hardcoded `BRIDGE_KEY`,
  reading `plugin_api.py` / `reply_draft.py` / `serve.py` / `cal.py`, direct `cal.db`,
  `search_files` under `plugins/kol-ops-bridge/`, PATCH `/escalations/{id}` after Console resolve.
- Console resume briefs include `# bridge_cli_checklist` — follow that order.
- **Idempotent:** if `resume_context.resumed_at` is set, return
  `{"skipped":"already_resumed"}`.

## Procedure

### Step 1 — Load escalation

```bash
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py get-escalation \
  --escalation-id <id> --env <TEST|LIVE>
```

Also:

```bash
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py get-dispatch-context \
  --identity-id <id> --campaign-id <cid> --env <TEST|LIVE> --view agent
```

Optional Gmail context (resume drafts — **not curl**):

```bash
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py get-email-conversation \
  --identity-id <id> --campaign-id <cid> --env <TEST|LIVE> \
  --operator-user-id <console_user_id>
```

### Step 2 — Branch

| Condition | Decision |
|-----------|----------|
| `state != resolved` | abort |
| `decision == terminate` | `terminate_goal` |
| `attempts_count >= max_escalation_depth` and facts incomplete | `escalate_again` + `force_human_takeover_hint` |
| `operator_facts` covers `missing_facts` | `inject_and_continue` |
| `override_config_patch:` in answer | `override_and_continue` |
| partial / vague answer | `escalate_again` |

### Step 3 — Execute

**inject_and_continue:**

```bash
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py write-facts-multi \
  --identity-id <id> --env <env> \
  --json '{"campaign_id":"<cid>","source":"skill:kol-escalation-resumer","namespaces":{...}}'
```

**override_and_continue:** `upsert-campaign` with canonical column names only.

**escalate_again:** `open-escalation` with `parent_escalation_id`.

**Reply draft (when console brief requires it):** `persist-reply-draft` with
`linked_escalation_id`, `conversation_summary.bullets` (Chinese), and
`summary_only` `kol-reply-synthesizer` when the drafting child is not the
synthesizer — never raw `POST /reply-drafts/persist` via curl.

### Step 4 — Envelope

Return JSON with `"skill": "kol-escalation-resumer"`, `"body": null` unless
console explicitly required a draft in the brief.

## Pitfalls

- Using `execute_code` + `subprocess` + `curl` — use CLI subcommands only.
- Hardcoding bridge keys — keys belong in env/secrets, not source code.
- Reading plugin Python files to learn persist schema — use `persist-reply-draft --help`.
