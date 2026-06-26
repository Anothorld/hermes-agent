# CS Bridge Agent Guard

Blocks direct QuickCEP CLI usage and `send-email` in `povison-cs:*` gateway runs so automation only uses `cs_bridge_tool`.

## Enable

Auto-enabled when the plugin is loaded on the `povison-cs` gateway profile. One-time setup:

```bash
python playground/povison-cs-console/scripts/ensure_cs_send_guard.py
```

This adds `cs-bridge-agent-guard` to `plugins.enabled` and patches profile `quickcep_cli.py` so `send-email` exits before calling QuickCEP API.

Disable guard with:

```bash
export CS_BRIDGE_AGENT_GUARD=0
```

Manual operator CLI overrides (rare):

```bash
CS_OPS_ALLOW_QUICKCEP_CLI=1 python quickcep_cli.py messages ...
CS_OPS_ALLOW_QUICKCEP_SEND=1 python quickcep_cli.py send-email ...
```

## What is blocked

| Layer | Scope |
|-------|--------|
| Hermes `pre_tool_call` | Any `quickcep_cli` / `quickcep_cli.py` in `terminal` **or** `execute_code` on `povison-cs:*` runs |
| Hermes `pre_tool_call` | `send-email` / `/im/message/operator/sendEmail` in `terminal` or `execute_code` on `povison-cs:*` runs |
| Hermes `pre_tool_call` | **`cs_bridge_tool` inside `execute_code`** — use **terminal** once per bridge step instead |
| QuickCEP CLI hook | `quickcep_cli.py send-email` when `CS_OPS_PROFILE` is a povison profile (unless override env set) |

## What is allowed

- **`terminal` + `cs_bridge_tool`** — one subcommand per call (`get-messages`, `draft-save`, `apply-handoff`, …)
- All tools on non-`povison-cs:` sessions
