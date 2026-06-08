# kol-bridge-agent-guard

Hermes backend plugin that registers a **`pre_tool_call`** hook to stop agents from
bypassing the deterministic KOL bridge CLI.

## What it blocks

| Tool | When |
|------|------|
| `execute_code` | Snippet matches bridge contract lint (curl, `BRIDGE_KEY`, urllib, subprocess+`kol_bridge_tool`, bare `python plugins/…`, relative `plugins/kol-ops-bridge/…`, batch `/tmp/ingest_*.json`, etc.) |
| `terminal` | Same lint on the shell command |
| `read_file` / `search_files` | On `kol-*` gateway sessions, paths under `kol-ops-bridge` implementation files |
| `mcp_chrome_devtools_*` | **All** `kol-*` sessions (discovery, email-discover, outreach, reply, …) — remote CDP is disabled; use `browser_*` or Nox/bridge CLI |
| `browser_*` | Post-approval only: `kol-campaign-outreach:`, `kol-campaign-draft:`, `kol-nox-contacts-batch:`, `kol-reply:` — use Nox + bridge CLI, not browser crawl |

Allowed:

- `terminal` running absolute path to **`kol-bridge-cli`** (one subcommand per call).
- `browser_*` on `kol-campaign:` (instagram discovery) and `kol-email-discover:` (Console「全网搜索邮箱」Tier 2).

## Session matching

Gateway passes the run namespace as **`task_id`** (e.g. `kol-email-discover:LIVE:701`), not
`session_id`. This hook matches on `session_id or task_id` so blocks actually fire.

## Disable

```bash
export KOL_BRIDGE_AGENT_GUARD=0
```

Restart Hermes gateway after changing plugin code or env.

## Related

- `plugins/kol-ops-bridge/bridge_agent_contract.py` — lint rules + brief snippets
- `plugins/kol-ops-bridge/scripts/kol-bridge-cli` — macOS-safe wrapper (python3 + absolute tool path)
- Console `campaigns.py` `_APPROVAL_INSTRUCTIONS` — outreach pipeline contract
