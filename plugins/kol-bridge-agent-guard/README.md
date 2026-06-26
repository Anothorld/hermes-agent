# kol-bridge-agent-guard

Hermes backend plugin that registers a **`pre_tool_call`** hook to stop agents from
bypassing the deterministic KOL bridge CLI.

## What it blocks

| Tool | When |
|------|------|
| `execute_code` | Snippet matches bridge contract lint (curl, `BRIDGE_KEY`, urllib, subprocess+`kol_bridge_tool`, bare `python plugins/…`, relative `plugins/kol-ops-bridge/…`, batch `/tmp/ingest_*.json`, chained `ingest-confirmed-candidate`, stdout `> file`, pipe through `head`/`grep`/`jq`, hallucinated subcommands like `read-identity`, etc.) |
| `terminal` | Same lint on the shell command |
| `read_file` / `search_files` | On `kol-*` gateway sessions, paths under `kol-ops-bridge` implementation files |
| `mcp_chrome_devtools_*` | **All** `kol-*` sessions (discovery, email-discover, outreach, reply, …) — remote CDP is disabled; use `browser_*` or Nox/bridge CLI |
| `web_search` / `web_extract` | `kol-email-discover:*` and `kol-campaign:*` discovery runs — use `browser_navigate` → `https://www.google.com/search?q=...` |
| `terminal` / `execute_code` HTTP scrape | `kol-email-discover:*` and `kol-campaign:*` when command matches curl/requests/Serper/google.com/search/etc. |
| `browser_*` | Post-approval only: `kol-campaign-outreach:`, `kol-campaign-draft:`, `kol-nox-contacts-batch:`, `kol-reply:` — use Nox + bridge CLI, not browser crawl |
| `browser_*` / `veedcrawl_*` | **`kol-campaign:` discovery** — blocked until bootstrap completes: `list-candidates`, `list-discovery-skip-handles`, `list-outreach-cooldown-handles` for the session's campaign |
| `terminal` (bridge CLI) | **`kol-campaign:` discovery** — `--campaign-id` / `--env` must match session; mismatches are blocked |

Block messages include the canonical Nox path:
`python3 plugins/nox-kol-bridge/scripts/nox_kol_tool.py` (not under `kol-ops-bridge/`).

Block JSON includes `"source": "kol_bridge_agent_guard"` and a `note` — this is **not**
bridge HTTP/JSON validation; fix the command per `hint`.

Allowed:

- `terminal` running absolute path to **`kol-bridge-cli`** (one subcommand per call).
- `ingest-confirmed-candidate --json @/tmp/ingest_<handle>.json` (single handle; not batch execute_code).
- `| tee /tmp/…` on bridge CLI (stdout still visible to the agent).
- `browser_*` on `kol-campaign:` (instagram discovery) and `kol-email-discover:` (Console「全网搜索邮箱」Tier 2) and `kol-creator-brief-refresh:` (Console 创作者简介刷新).

## Session matching

Gateway passes the run namespace as **`task_id`** (e.g. `kol-email-discover:LIVE:701`), not
`session_id`. This hook matches on `session_id or task_id` so blocks actually fire.

Observer hooks may also pass `turn_id`, `api_request_id`, and `middleware_trace`; this
plugin ignores them via `**kwargs` forward compatibility (required since Hermes 0.14).

## Disable

```bash
export KOL_BRIDGE_AGENT_GUARD=0
```

Restart Hermes gateway after changing plugin code or env.

## Related

- `plugins/kol-ops-bridge/bridge_agent_contract.py` — lint rules + brief snippets
- `plugins/kol-ops-bridge/scripts/kol-bridge-cli` — macOS-safe wrapper (python3 + absolute tool path)
- Console `campaigns.py` `_APPROVAL_INSTRUCTIONS` — outreach pipeline contract
