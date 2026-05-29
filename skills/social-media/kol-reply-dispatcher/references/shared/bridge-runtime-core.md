## Shared bridge runtime core

Applies to router/dispatcher/documented orchestration skills:

- Profile: `outreach-operator` unless skill explicitly overrides.
- Bridge is the only CAL read/write surface.
- Do not use `cal.py`, direct `~/.hermes/kol-ops-bridge/cal.db`, ad-hoc SQL,
  or `execute_code` against CAL.
- Always pass `--env <TEST|LIVE>` on bridge calls.
- Treat bridge validation as authoritative; do not "repair and retry" payloads
  by mutating keys or semantics.
