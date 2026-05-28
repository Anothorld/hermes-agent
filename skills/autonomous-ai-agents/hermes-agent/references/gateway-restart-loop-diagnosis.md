# Gateway Restart-Loop Diagnosis

The Hermes gateway runs under `systemd --user` with `Restart=always`. When
it appears to "always be restarting," 90% of the time the restarts are
*clean* (exit code 0, `gateway.exit_clean`) — which means systemd is
reviving it on purpose because something else is killing it. Don't assume
a crash; verify which one of these is happening.

## Triage path

Pull these three sources and cross-reference timestamps. They live under
`$HERMES_HOME/logs/` (profile-aware):

```
gateway-exit-diag.log     # one JSON line per gateway start/exit, with PID, python path, argv
errors.log                # WARNING+ from gateway runtime, includes Shutdown context blocks
gateway-shutdown-diag.log # ps + pstree snapshot taken on SIGTERM
```

Also check what systemd thinks is going on:

```bash
systemctl --user list-units --all --type=service | grep -i hermes
systemctl --user status hermes-gateway-<profile>.service --no-pager
journalctl --user -u hermes-gateway-<profile>.service -n 200 --no-pager
```

## Pattern A — two services racing (most common after profile setup)

Symptoms:

- `systemctl --user list-units` shows BOTH `hermes-gateway.service` and
  `hermes-gateway-<profile>.service` loaded.
- `gateway-exit-diag.log` shows `gateway.start` events alternating between
  two different argv paths and sometimes two different Python versions
  (e.g. one entry has `/home/pc/.hermes/hermes-agent/...` Python 3.11,
  the next has `/home/pc/agent_prj/hermes-agent/...` Python 3.14). They
  appear roughly every 60 seconds (matching `RestartSec=60`).
- `errors.log` is full of `Received SIGTERM as a planned --replace takeover`
  with `parent_pid=<systemd-pid>` and `under_systemd=yes`.
- A platform adapter complains that a shared resource is in use:
  `[QQBot] QQBot app ID already in use (PID …). Stop the other gateway first.`
  (also possible with DingTalk, Telegram bot tokens, Slack socket-mode
  apps — anything with a single-holder long connection).

Mechanism:

1. Service A starts → its `gateway run --replace` SIGTERMs whatever else
   holds the gateway lock — Service B's running process.
2. Service B exits cleanly (it received a *planned* takeover SIGTERM).
3. systemd sees Service B's main PID gone with `Restart=always` →
   schedules a restart.
4. Service B starts → SIGTERMs Service A's process. Loop.

Fix (in order of preference):

1. Disable the service for the profile you don't actively use:
   ```bash
   systemctl --user disable --now hermes-gateway.service
   ```
   Keep the per-profile one (or vice versa — only one).
2. If both profiles must run, give each its own platform credentials.
   You cannot share one QQBot AppID / one Telegram bot token / one
   DingTalk app between two gateway processes; the platform itself only
   allows a single concurrent holder. Disable the redundant platform on
   the secondary profile:
   ```bash
   hermes config set qqbot.enabled false                           # legacy key
   hermes config set gateway.platforms.qqbot.enabled false         # current key
   ```
   Make sure both keys are in agreement (older profiles have both).
3. Before opening a bug report, verify the crash isn't really a clean
   takeover by reading the exit tag in `gateway-exit-diag.log`:
   `gateway.exit_clean` = systemd-induced, `gateway.exit_nonzero` =
   actual failure.

## Pattern B — `--replace` from a foreground command

If you see `Shutdown context: ... parent_pid=<bash-pid> parent_cmdline="...
hermes gateway restart ..."`, the kill came from a user-issued `hermes
gateway restart` — not a service race. Common when an agent (including
yourself) issued `hermes config set ... && hermes gateway restart` from a
tool call and didn't realize the previous restart was still settling.
Harmless if isolated; problematic only if the loop persists after the
last manual restart.

## Pattern C — actual crash

Indicator: `gateway-exit-diag.log` shows `asyncio.run.SystemExit` with a
non-zero `code` field and a real traceback. systemd then revives it under
`Restart=always`. Diagnose from the traceback like any normal Python
crash. `code=75` specifically is the planned-restart sentinel
(`RestartForceExitStatus=75`); not a crash.

## Verification after fix

```bash
# wait a few minutes, then:
systemctl --user show hermes-gateway-<profile>.service \
  -p ActiveState -p SubState -p NRestarts
grep gateway.start ~/.hermes/profiles/<profile>/logs/gateway-exit-diag.log | tail
```

Healthy: `ActiveState=active`, `SubState=running`, no new `gateway.start`
entries beyond the last legitimate restart, PID stays stable for many
minutes. Looping: PID changes every ~60s, new `gateway.start` lines keep
appearing.

## Notes

- The `--replace` semantics are intentional: it's how `hermes gateway
  restart` hands the gateway lock between processes cleanly. The bug is
  always *who* is calling `--replace`, never `--replace` itself.
- `RestartForceExitStatus=75` exists so systemd treats a deliberate
  restart-handoff as success, not a crash. If you see exit code 75 in
  the diag log, that's a feature.
- Don't capture "the gateway is crashing" as a generalization — it
  almost never is. Capture which of A/B/C you saw and act on that.
