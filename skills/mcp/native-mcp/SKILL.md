---
name: native-mcp
description: "MCP client: connect servers, register tools (stdio/HTTP)."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [MCP, Tools, Integrations]
    related_skills: [mcporter]
---

# Native MCP Client

Hermes Agent has a built-in MCP client that connects to MCP servers at startup, discovers their tools, and makes them available as first-class tools the agent can call directly. No bridge CLI needed -- tools from MCP servers appear alongside built-in tools like `terminal`, `read_file`, etc.

## When to Use

Use this whenever you want to:
- Connect to MCP servers and use their tools from within Hermes Agent
- Add external capabilities (filesystem access, GitHub, databases, APIs) via MCP
- Run local stdio-based MCP servers (npx, uvx, or any command)
- Connect to remote HTTP/StreamableHTTP MCP servers
- Have MCP tools auto-discovered and available in every conversation

For ad-hoc, one-off MCP tool calls from the terminal without configuring anything, see the `mcporter` skill instead.

## Prerequisites

- **mcp Python package** -- optional dependency; install with `pip install mcp`. If not installed, MCP support is silently disabled.
- **Node.js** -- required for `npx`-based MCP servers (most community servers)
- **uv** -- required for `uvx`-based MCP servers (Python-based servers)

Install the MCP SDK:

```bash
pip install mcp
# or, if using uv:
uv pip install mcp
```

## Quick Start

Add MCP servers to `~/.hermes/config.yaml` under the `mcp_servers` key:

```yaml
mcp_servers:
  time:
    command: "uvx"
    args: ["mcp-server-time"]
```

Restart Hermes Agent. On startup it will:
1. Connect to the server
2. Discover available tools
3. Register them with the prefix `mcp_time_*`
4. Inject them into all platform toolsets

You can then use the tools naturally -- just ask the agent to get the current time.

## Configuration Reference

Each entry under `mcp_servers` is a server name mapped to its config. There are two transport types: **stdio** (command-based) and **HTTP** (url-based).

### Stdio Transport (command + args)

```yaml
mcp_servers:
  server_name:
    command: "npx"             # (required) executable to run
    args: ["-y", "pkg-name"]   # (optional) command arguments, default: []
    env:                       # (optional) environment variables for the subprocess
      SOME_API_KEY: "value"
    timeout: 120               # (optional) per-tool-call timeout in seconds, default: 120
    connect_timeout: 60        # (optional) initial connection timeout in seconds, default: 60
```

### HTTP Transport (url)

```yaml
mcp_servers:
  server_name:
    url: "https://my-server.example.com/mcp"   # (required) server URL
    headers:                                     # (optional) HTTP headers
      Authorization: "Bearer sk-..."
    timeout: 180               # (optional) per-tool-call timeout in seconds, default: 120
    connect_timeout: 60        # (optional) initial connection timeout in seconds, default: 60
```

### All Config Options

| Option            | Type   | Default | Description                                       |
|-------------------|--------|---------|---------------------------------------------------|
| `command`         | string | --      | Executable to run (stdio transport, required)     |
| `args`            | list   | `[]`    | Arguments passed to the command                   |
| `env`             | dict   | `{}`    | Extra environment variables for the subprocess    |
| `url`             | string | --      | Server URL (HTTP transport, required)             |
| `headers`         | dict   | `{}`    | HTTP headers sent with every request              |
| `timeout`         | int    | `120`   | Per-tool-call timeout in seconds                  |
| `connect_timeout` | int    | `60`    | Timeout for initial connection and discovery      |

Note: A server config must have either `command` (stdio) or `url` (HTTP), not both.

## How It Works

### Startup Discovery

When Hermes Agent starts, `discover_mcp_tools()` is called during tool initialization:

1. Reads `mcp_servers` from `~/.hermes/config.yaml`
2. For each server, spawns a connection in a dedicated background event loop
3. Initializes the MCP session and calls `list_tools()` to discover available tools
4. Registers each tool in the Hermes tool registry

### Tool Naming Convention

MCP tools are registered with the naming pattern:

```
mcp_{server_name}_{tool_name}
```

Hyphens and dots in names are replaced with underscores for LLM API compatibility.

Examples:
- Server `filesystem`, tool `read_file` → `mcp_filesystem_read_file`
- Server `github`, tool `list-issues` → `mcp_github_list_issues`
- Server `my-api`, tool `fetch.data` → `mcp_my_api_fetch_data`

### Auto-Injection

After discovery, MCP tools are automatically injected into all `hermes-*` platform toolsets (CLI, Discord, Telegram, etc.). This means MCP tools are available in every conversation without any additional configuration.

### Connection Lifecycle

- Each server runs as a long-lived asyncio Task in a background daemon thread
- Connections persist for the lifetime of the agent process
- If a connection drops, automatic reconnection with exponential backoff kicks in (up to 5 retries, max 60s backoff)
- On agent shutdown, all connections are gracefully closed

### Idempotency

`discover_mcp_tools()` is idempotent -- calling it multiple times only connects to servers that aren't already connected. Failed servers are retried on subsequent calls.

## Transport Types

### Stdio Transport

The most common transport. Hermes launches the MCP server as a subprocess and communicates over stdin/stdout.

```yaml
mcp_servers:
  filesystem:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/home/user/projects"]
```

The subprocess inherits a **filtered** environment (see Security section below) plus any variables you specify in `env`.

### HTTP / StreamableHTTP Transport

For remote or shared MCP servers. Requires the `mcp` package to include HTTP client support (`mcp.client.streamable_http`).

```yaml
mcp_servers:
  remote_api:
    url: "https://mcp.example.com/mcp"
    headers:
      Authorization: "Bearer sk-..."
```

If HTTP support is not available in your installed `mcp` version, the server will fail with an ImportError and other servers will continue normally.

## Security

### Environment Variable Filtering

For stdio servers, Hermes does NOT pass your full shell environment to MCP subprocesses. Only safe baseline variables are inherited:

- `PATH`, `HOME`, `USER`, `LANG`, `LC_ALL`, `TERM`, `SHELL`, `TMPDIR`
- Any `XDG_*` variables

All other environment variables (API keys, tokens, secrets) are excluded unless you explicitly add them via the `env` config key. This prevents accidental credential leakage to untrusted MCP servers.

```yaml
mcp_servers:
  github:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-github"]
    env:
      # Only this token is passed to the subprocess
      GITHUB_PERSONAL_ACCESS_TOKEN: "ghp_..."
```

### Credential Stripping in Error Messages

If an MCP tool call fails, any credential-like patterns in the error message are automatically redacted before being shown to the LLM. This covers:

- GitHub PATs (`ghp_...`)
- OpenAI-style keys (`sk-...`)
- Bearer tokens
- Generic `token=`, `key=`, `API_KEY=`, `password=`, `secret=` patterns

## Troubleshooting

### "MCP SDK not available -- skipping MCP tool discovery"

The `mcp` Python package is not installed. Install it:

```bash
pip install mcp
```

**Pitfall — multiple Hermes venvs after migration / repo move.** Hermes can end up with more than one venv on disk (e.g. `~/.hermes/hermes-agent/venv` from a fresh install plus `<your-checkout>/venv` from a git checkout you re-pointed the CLI/systemd at). `pip install mcp` only affects the venv whose `pip` you ran. Verify the venv that the gateway actually uses:

```bash
# What does the systemd unit launch?
grep ExecStart ~/.config/systemd/user/hermes-gateway*.service

# What does the shell wrapper resolve to?
readlink -f "$(which hermes)"
```

Install `mcp` into THAT venv specifically:

```bash
/path/to/active/venv/bin/pip install mcp
/path/to/active/venv/bin/python -c "import mcp; print('ok')"
```

If you see "tools not appearing" after configuring `mcp_servers`, the most common cause is that `mcp` was installed into the wrong venv and discovery silently no-ops in the live process.

### "No MCP servers configured"

No `mcp_servers` key in `~/.hermes/config.yaml`, or it's empty. Add at least one server.

### "Failed to connect to MCP server 'X'"

Common causes:
- **Command not found**: The `command` binary isn't on PATH. Ensure `npx`, `uvx`, or the relevant command is installed.
- **Package not found**: For npx servers, the npm package may not exist or may need `-y` in args to auto-install.
- **Timeout**: The server took too long to start. Increase `connect_timeout`.
- **Port conflict**: For HTTP servers, the URL may be unreachable.

### "MCP server 'X' requires HTTP transport but mcp.client.streamable_http is not available"

Your `mcp` package version doesn't include HTTP client support. Upgrade:

```bash
pip install --upgrade mcp
```

### Tools not appearing

- Check that the server is listed under `mcp_servers` (not `mcp` or `servers`)
- Ensure the YAML indentation is correct
- Look at Hermes Agent startup logs for connection messages
- Tool names are prefixed with `mcp_{server}_{tool}` -- look for that pattern

### Connection keeps dropping

The client retries up to 5 times with exponential backoff (1s, 2s, 4s, 8s, 16s, capped at 60s). If the server is fundamentally unreachable, it gives up after 5 attempts. Check the server process and network connectivity.

## Examples

### Time Server (uvx)

```yaml
mcp_servers:
  time:
    command: "uvx"
    args: ["mcp-server-time"]
```

Registers tools like `mcp_time_get_current_time`.

### Filesystem Server (npx)

```yaml
mcp_servers:
  filesystem:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/home/user/documents"]
    timeout: 30
```

Registers tools like `mcp_filesystem_read_file`, `mcp_filesystem_write_file`, `mcp_filesystem_list_directory`.

### GitHub Server with Authentication

```yaml
mcp_servers:
  github:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-github"]
    env:
      GITHUB_PERSONAL_ACCESS_TOKEN: "ghp_xxxxxxxxxxxxxxxxxxxx"
    timeout: 60
```

Registers tools like `mcp_github_list_issues`, `mcp_github_create_pull_request`, etc.

### Remote HTTP Server

```yaml
mcp_servers:
  company_api:
    url: "https://mcp.mycompany.com/v1/mcp"
    headers:
      Authorization: "Bearer sk-xxxxxxxxxxxxxxxxxxxx"
      X-Team-Id: "engineering"
    timeout: 180
    connect_timeout: 30
```

### Multiple Servers

```yaml
mcp_servers:
  time:
    command: "uvx"
    args: ["mcp-server-time"]

  filesystem:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]

  github:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-github"]
    env:
      GITHUB_PERSONAL_ACCESS_TOKEN: "ghp_xxxxxxxxxxxxxxxxxxxx"

  company_api:
    url: "https://mcp.internal.company.com/mcp"
    headers:
      Authorization: "Bearer sk-xxxxxxxxxxxxxxxxxxxx"
    timeout: 300
```

All tools from all servers are registered and available simultaneously. Each server's tools are prefixed with its name to avoid collisions.

### WSL ↔ Windows Chrome DevTools MCP pattern

When Hermes runs inside **WSL** but Chrome is running on the **Windows host**, `--browserUrl http://127.0.0.1:9222` may work in Windows itself while still being unreachable from WSL. In that setup:

1. Start Windows Chrome with remote debugging enabled, and bind the debug port broadly enough for WSL to reach it:
   ```bat
   "C:\Program Files\Google\Chrome\Application\chrome.exe" ^
     --remote-debugging-port=9222 ^
     --remote-debugging-address=0.0.0.0 ^
     --user-data-dir="%LOCALAPPDATA%\ChromeDebugProfile-9222" ^
     --no-first-run --no-default-browser-check
   ```
2. Verify on Windows first by opening `http://127.0.0.1:9222/json/version` in a normal browser. If it does not return JSON, Chrome was not launched with the expected flags.
3. If Hermes in WSL still cannot connect to `127.0.0.1:9222`, use the Windows host IPv4 from `ipconfig` (for example `10.30.80.118`) in the MCP config instead:
   ```yaml
   mcp_servers:
     chrome_devtools:
       command: "npx"
       args:
         - "-y"
         - "chrome-devtools-mcp@latest"
         - "--browserUrl"
         - "http://10.30.80.118:9222"
         - "--no-usage-statistics"
       timeout: 180
       connect_timeout: 60
   ```
4. Install the Python MCP SDK in the same Hermes venv that actually runs the agent / gateway (`pip install mcp`), or MCP discovery will be silently unavailable in that environment.

Pitfall: editing the right config but testing from the wrong runtime is common. Confirm which Hermes binary / venv is active before concluding the MCP setup is broken.

## Sampling (Server-Initiated LLM Requests)

Hermes supports MCP's `sampling/createMessage` capability — MCP servers can request LLM completions through the agent during tool execution. This enables agent-in-the-loop workflows (data analysis, content generation, decision-making).

Sampling is **enabled by default**. Configure per server:

```yaml
mcp_servers:
  my_server:
    command: "npx"
    args: ["-y", "my-mcp-server"]
    sampling:
      enabled: true           # default: true
      model: "gemini-3-flash" # model override (optional)
      max_tokens_cap: 4096    # max tokens per request
      timeout: 30             # LLM call timeout (seconds)
      max_rpm: 10             # max requests per minute
      allowed_models: []      # model whitelist (empty = all)
      max_tool_rounds: 5      # tool loop limit (0 = disable)
      log_level: "info"       # audit verbosity
```

Servers can also include `tools` in sampling requests for multi-turn tool-augmented workflows. The `max_tool_rounds` config prevents infinite tool loops. Per-server audit metrics (requests, errors, tokens, tool use count) are tracked via `get_mcp_status()`.

Disable sampling for untrusted servers with `sampling: { enabled: false }`.

## Notes

- MCP tools are called synchronously from the agent's perspective but run asynchronously on a dedicated background event loop
- Tool results are returned as JSON with either `{"result": "..."}` or `{"error": "..."}`
- The native MCP client is independent of `mcporter` -- you can use both simultaneously
- Server connections are persistent and shared across all conversations in the same agent process
- Adding or removing servers requires restarting the agent (no hot-reload currently)

## WSL + browser-controlling MCP servers (chrome-devtools-mcp, playwright, etc.)

When Hermes runs inside WSL but the browser you actually want it to drive lives on the Windows host, do NOT have the MCP server launch its own browser inside WSL. WSL has no graphical Chrome by default, and even with a headless install you lose the user's existing logged-in profile.

The working topology is:

1. On the **Windows** side, start Chrome with `--remote-debugging-port=9222 --user-data-dir=<separate-profile>` (use a dedicated profile so it doesn't fight your everyday Chrome).
2. In `mcp_servers`, point the MCP server at that endpoint over loopback. WSL2 can usually reach the Windows host's `127.0.0.1:9222` directly because of the WSL/host loopback bridge:

```yaml
mcp_servers:
  chrome_devtools:
    command: "npx"
    args:
      - "-y"
      - "chrome-devtools-mcp@latest"
      - "--browserUrl"
      - "http://127.0.0.1:9222"
      - "--no-usage-statistics"
    timeout: 180
```

3. If `127.0.0.1:9222` is not reachable from WSL (some firewall configurations, older WSL2, or non-default network modes), launch Chrome with `--remote-debugging-address=0.0.0.0` and use the Windows host's IP from inside WSL:

```bash
# Inside WSL — discover the Windows host IP
ip route show | awk '/default/ {print $3}'
# or, on recent WSL:
cat /etc/resolv.conf | awk '/nameserver/ {print $2}'
```

Then put `http://<that-ip>:9222` in `--browserUrl`. Note that binding `0.0.0.0` exposes the debugging port on all interfaces — only do this on a trusted network.

4. Order matters at first startup: chrome-devtools-mcp fails its initial connection if Chrome is not already listening. Start the Windows-side debug Chrome BEFORE you `hermes gateway restart`, otherwise the MCP server retries with backoff and the first user-visible call will time out. Subsequent runs reconnect automatically.

5. Don't share that debugging port with another MCP / automation client at the same time. Chrome's CDP allows multiple clients but state is shared, and `chrome-devtools-mcp`'s page-tracking gets confused if a second tool is also navigating.

This same pattern works for any "control a real browser via CDP" MCP server. The WSL-side gotchas (loopback reachability, `0.0.0.0` bind, host IP discovery) are the part that's easy to forget.

### Migrating skills/runbooks from `browser_*` to `mcp_chrome_devtools_*`

The two toolsets cover the same capability surface but the call shapes differ — `chrome-devtools-mcp` is uid-based (snapshot-driven, ephemeral identifiers) while Hermes's built-in `browser_*` is ref-based (selectors / stable refs). Any skill that previously called `browser_console`, `browser_type`, `browser_back`, `browser_vision`, etc. needs per-call-site rewriting, not a bulk rename. Full migration table, pitfalls (uid invalidation rules, IG `back` unreliability, no `vision` equivalent, stateful login profile), and a porting checklist live in `references/chrome-devtools-mcp-tool-semantics.md`.
