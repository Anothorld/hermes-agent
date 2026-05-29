## Shared runtime + draft guardrails

Applies to KOL draft-generator skills unless a skill explicitly tightens it.

- Profile: `outreach-operator`.
- Bridge is the only CAL write surface. Do not use `cal.py`, direct
  `~/.hermes/kol-ops-bridge/cal.db`, ad-hoc SQL, or `execute_code`.
- Always pass `--env <TEST|LIVE>` to bridge CLI calls.
- Skill output is draft-only. Do not send mail directly.
- If using HTML body, keep a minimal safe tag set and use
  `http://`/`https://` links only.
- If a `write-facts-multi` call fails with `FactNamespaceError`, abort with
  structured error. Do not retry with rewritten keys.
