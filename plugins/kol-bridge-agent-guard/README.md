# kol-bridge-agent-guard

Hermes backend plugin that registers a **`pre_tool_call`** hook to stop agents from
bypassing the deterministic KOL bridge CLI.

## What it blocks

| Tool | When |
|------|------|
| `execute_code` | Snippet matches bridge contract lint (curl, `BRIDGE_KEY`, urllib, subprocess+`kol_bridge_tool`, batch `/tmp/ingest_*.json`, etc.) |
| `terminal` | Same lint on the shell command |
| `read_file` / `search_files` | On `kol-*` gateway sessions, paths under `kol-ops-bridge` implementation files |

Allowed: `terminal` running `python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py ...`
(one subcommand per call).

## Disable

```bash
export KOL_BRIDGE_AGENT_GUARD=0
```

## Contract source

Lint rules and gateway brief snippets live in
`../kol-ops-bridge/bridge_agent_contract.py` (also used by Console and
`kol_bridge_tool.py lint-agent-code`).

## Related

- `docs/kol-bridge-agent-tooling.md`
- `plugins/kol-ops-bridge/README.md` (agent bridge contract)
