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

When `BROWSER_CDP_URL` is set (including `http://127.0.0.1:9222` from
`start-debug-chrome.sh`), the plugin wraps `browser_tool._get_session_info`
so each task still receives a **page-level** CDP URL instead of sharing the
browser-level socket's active tab.

Login cookies and IG session stay shared because all tabs live in the same profile
(`~/.hermes/local-chrome-debug-profile` by default).

## Scope / limitations

| Scenario | Tab pool |
|----------|----------|
| `local-chrome` (CDP, no cloud provider) — KOL default | Yes |
| Concurrent agent runs, same gateway | Yes — one tab per bare `task_id` |
| `BROWSER_CDP_URL=http://127.0.0.1:9222` (HTTP discovery from `start-debug-chrome.sh`) | Yes — pool opens page tabs on that Chrome |
| `BROWSER_CDP_URL=ws://…/devtools/browser/…` (browser-level WebSocket) | Yes — pool opens page tabs via HTTP `PUT /json/new`; set `LOCAL_CHROME_FORCE_SHARED_CDP=1` only for legacy shared active-tab mode |
| Cloud provider + hybrid `::local` sidecar (private URLs) | No — sidecar stays headless |
| `LOCAL_CHROME_TAB_POOL=0` | Disabled — legacy shared CDP connection |

If a browser session already exists without `tab_pool` metadata (typically a stale
browser-level `BROWSER_CDP_URL` session), the plugin **evicts** it and replaces it
with a dedicated page tab instead of silently reusing the shared active tab.

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

## Cloud fallback

When `browser.cloud_provider` is configured but `create_session()` fails or times
out, `browser_tool` falls back to local debug Chrome **per task** (CDP URL stored
in `_active_sessions` only). It does **not** set global `BROWSER_CDP_URL`, so
tab-pool per-page isolation stays intact for concurrent runs.

Autostart uses `DEBUG_CHROME_SKIP_ENV=1` (same as the pool launcher) so
`start-debug-chrome.sh` does not write a browser-level ws into the shell env.

## Agent behaviour

No skill changes required. Agents call `browser_navigate` as usual; the plugin
runs before the tool and wires the isolated tab transparently.

## `_get_session_info` wrapper

When `BROWSER_CDP_URL` is present, `browser_tool._get_session_info` would
otherwise resolve it to a **browser-level** WebSocket and attach every task
to the same active tab. The plugin wraps that function at load time so each
`task_id` still receives its own page-level CDP URL before any browser command
runs — even if `pre_tool_call` hooks are skipped on a code path.

## `_create_cdp_session` wrapper (v1.0.4)

Some code paths still call `browser_tool._create_cdp_session` with the
**browser-level** WebSocket resolved from `BROWSER_CDP_URL`, overwriting the
pooled page session in `_active_sessions`. Runtime evidence (2026-06-23):
concurrent discovery runs logged `Resolved CDP …/devtools/browser/…` for every
campaign while tab-pool acquire succeeded — POVISON/PR0037 then read SSF8033's
Instagram page (`@cozyvibesdarling`). The plugin now wraps
`_create_cdp_session` to redirect browser-level creates to the task's pooled
page tab, and wraps `_ensure_cdp_supervisor` so the supervisor attaches to the
same page CDP.

## `_run_browser_command` wrapper (v1.0.5–1.0.6)

`browser_tool._run_browser_command` copies the full process environment into each
`agent-browser` subprocess (`browser_env = {**os.environ}`). When
`BROWSER_CDP_URL=http://127.0.0.1:9222` is loaded from dotenv (as
`start-debug-chrome.sh` writes) **and** the tab pool passes a page-level
`--cdp ws://…/devtools/page/…`, concurrent `open` commands attached to the
shared browser socket and navigated whichever tab was foreground.

**v1.0.5** strips `BROWSER_CDP_URL` from the subprocess env. Runtime proof showed
that was necessary but not sufficient — concurrent `open` still raced (SSF8033's
tab showed PR0037's `angelarosehome` URL while navigating to `homecinema`).

**v1.0.6** routes tab-pool `open` through direct `Page.navigate` on the page
websocket (`internal/cdp_page.py`), bypassing agent-browser entirely. Agent-browser
`open` remains a serialized fallback when direct CDP fails. Post-navigate URL checks
are logged for verification.

**v1.0.7** attempted a serialized agent-browser `open` sync after direct CDP
navigate. Runtime logs showed `sync_success=true` but `snapshot_len=0`, and the
sync step reintroduced cross-talk risk — **removed in v1.0.8**.

**v1.0.8** routes tab-pool `open`, `snapshot`, `eval`, `click`, `scroll`, and
`back` through direct page CDP only (`internal/cdp_page.py`). Each concurrent
run uses its own page websocket end-to-end — no agent-browser on the hot path.
Agent-browser remains fallback only when direct CDP navigate fails.

## `browser_cdp` ownership guard

The raw `browser_cdp` tool connects to the **browser-level** CDP socket and
can address any tab via `target_id` — bypassing the pool. Concurrent runs were
hijacking each other's tabs this way (run A ran `Runtime.evaluate` /
`Page.navigate` inside run B's tab, discovered via `Target.getTargets`).

The `pre_tool_call` hook therefore enforces per-task tab ownership on
`browser_cdp`:

| Call shape | Behaviour |
|------------|-----------|
| Page-scoped method (`Page.*`, `Runtime.*`, `DOM.*`, ...) without `target_id` | `target_id` auto-injected with this task's pooled tab |
| `target_id` / `params.targetId` = this task's pooled tab | allowed |
| `target_id` / `params.targetId` = any other tab | **blocked** with a message naming the task's own target_id |
| Browser-level methods (`Target.getTargets`, `Storage.*`, `Browser.*`, ...) without target | allowed (read-only discovery is harmless) |
| `Target.attachToTarget` / `activateTarget` / `closeTarget` on a foreign tab | **blocked** |

The guard only protects processes that loaded this plugin version — restart
long-lived gateways/CLIs after upgrading.

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
| `plugins/local-chrome-tab-pool/internal/cdp_page.py` | Direct `Page.navigate` on pooled page websockets |
| `plugins/local-chrome-tab-pool/internal/tab_pool.py` | Canonical tab pool logic |
| `plugins/local-chrome-tab-pool/hooks.py` | Hermes `pre_tool_call`, session/create/run_browser/supervisor wrappers, cleanup wrapper |
| `playground/local-chrome-debug/start-debug-chrome.sh` | Chrome launcher |
| `playground/local-chrome-debug/tab_pool.py` | Backward-compatible re-export |

## See also

- [docs/local-chrome-concurrent-tabs.md](../../../docs/local-chrome-concurrent-tabs.md)
