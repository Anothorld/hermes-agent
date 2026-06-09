# MySQL Tools Plugin

Hermes plugin that exposes **`mysql_execute_sql`** for the data-analyst profile.
Every query runs through **Hermes `clarify` human approval** before hitting the
profile-local `sql_executor.py` implementation. Clarify UI is resolved inside
the plugin (`internal/clarify_resolver.py`) — no Hermes core changes required.

## Why this exists

Direct `terminal` calls to `sql_executor.py` bypass operator review. This plugin:

1. Registers `mysql_execute_sql` (read-only SQL with existing safety rules).
2. Prompts via `clarify` with three choices:
   - **批准本次执行** — run this query once.
   - **拒绝** — cancel.
   - **批准本会话内免审** — skip further prompts for this Hermes session (same conversation); cleared on `on_session_end`.
   - **Timeout** — if clarify times out or receives no response, SQL execution is **automatically rejected** (fail-closed).
3. Blocks direct SQL via `terminal`, `execute_code`, `mysql`/`mariadb` CLI, and `sql_executor.py` (except `--test` / `--validate`).

Agent-visible SQL execution is **only** `mysql_execute_sql`. There is no ungated query tool in the tool list.

## Prerequisites

- Active Hermes profile with scripts at `$HERMES_HOME/scripts/mysql_tools/sql_executor.py`
- `clarify` toolset enabled (interactive CLI / TUI / gateway)
- MySQL executor env vars configured in profile `.env`

## Enable

Add `mysql_tools` to `platform_toolsets.cli` (or your platform list) in profile `config.yaml`:

```yaml
platform_toolsets:
  cli:
    - mysql_tools
    - clarify
    # ...
```

## Usage (agent)

```json
{
  "name": "mysql_execute_sql",
  "arguments": {
    "sql": "SELECT COUNT(*) FROM orders LIMIT 10",
    "database": "ads"
  }
}
```

## Manual CLI (operator)

Operators may still run `sql_executor.py` directly from a shell for debugging;
the clarify gate applies only to **agent** tool paths (`mysql_execute_sql`).

## Related

- Profile skill: `data-science/mysql-nl2sql`
- Integration doc: `agent_prj/docs/mysql-tools-clarify-gate.md`
