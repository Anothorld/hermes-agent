# Local Chrome Tab Pool

Concurrent agent runs against **local Chrome** without cross-talk.

## Problem

`local-chrome` mode attaches Hermes to one debug Chrome via `BROWSER_CDP_URL`.
Every task shared the same browser connection and fought over the **active tab**.

Chrome also **cannot** run two processes on the same `--user-data-dir` at once,
so "multiple Chromes, one profile" is not possible concurrently.

## Solution

**One Chrome + one shared profile + one tab per `task_id`.**

The `local-chrome-tab-pool` plugin (enabled by default):

1. Auto-starts debug Chrome via `start-debug-chrome.sh` when port `9222` is idle.
2. On the first `browser_*` tool call for a task, opens a dedicated tab
   (`PUT /json/new`) and seeds `browser_tool._active_sessions[task_id]` with that
   tab's page-level CDP URL.
3. On `cleanup_browser` or inactivity reaper, closes only that tab.

Login cookies and IG session stay shared because all tabs live in the same profile
(`~/.hermes/local-chrome-debug-profile` by default).

## Scope / limitations

| Scenario | Tab pool |
|----------|----------|
| `local-chrome` (CDP, no cloud provider) — KOL default | Yes |
| Concurrent agent runs, same gateway | Yes — one tab per bare `task_id` |
| `BROWSER_CDP_URL=http://127.0.0.1:9222` (HTTP discovery from `start-debug-chrome.sh`) | Yes — pool opens page tabs on that Chrome |
| `BROWSER_CDP_URL=ws://…/devtools/browser/…` (browser-level WebSocket) | No — legacy shared active-tab mode |
| Cloud provider + hybrid `::local` sidecar (private URLs) | No — sidecar stays headless |
| `LOCAL_CHROME_TAB_POOL=0` | Disabled — legacy shared CDP connection |

If a browser session already exists without `tab_pool` metadata, the plugin
**blocks** the tool call with an actionable error instead of silently sharing
the active tab.

## Enable / disable

```bash
# default: on
export LOCAL_CHROME_TAB_POOL=1

# revert to legacy single-connection behaviour
export LOCAL_CHROME_TAB_POOL=0
```

Optional overrides (same as `start-debug-chrome.sh`):

```bash
export DEBUG_CHROME_PORT=9222
export DEBUG_CHROME_PROFILE_DIR=$HOME/.hermes/local-chrome-debug-profile
export HERMES_LOCAL_CHROME_LAUNCHER=/path/to/start-debug-chrome.sh
```

## Agent behaviour

No skill changes required. Agents call `browser_navigate` as usual; the plugin
runs before the tool and wires the isolated tab transparently.

First run on a fresh machine still needs IG login once inside the debug profile
(manual, in the Chrome window launched by the script).

## Manual Chrome control

```bash
./hermes-agent/playground/local-chrome-debug/start-debug-chrome.sh start
./hermes-agent/playground/local-chrome-debug/start-debug-chrome.sh status
./hermes-agent/playground/local-chrome-debug/start-debug-chrome.sh stop
```

The plugin calls `start` automatically when browser tools run and nothing is
listening on the debug port.

## Implementation layout

| Path | Role |
|------|------|
| `plugins/local-chrome-tab-pool/internal/tab_pool.py` | Canonical tab pool logic |
| `plugins/local-chrome-tab-pool/hooks.py` | Hermes `pre_tool_call` + cleanup wrapper |
| `playground/local-chrome-debug/start-debug-chrome.sh` | Chrome launcher |
| `playground/local-chrome-debug/tab_pool.py` | Backward-compatible re-export |

## See also

- [docs/local-chrome-concurrent-tabs.md](../../../docs/local-chrome-concurrent-tabs.md)
