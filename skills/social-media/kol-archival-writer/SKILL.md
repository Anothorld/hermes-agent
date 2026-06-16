---
name: kol-archival-writer
description: Post-collaboration archival — sync relationship memory and close archival goal.
tags: ["kol", "archival", "relationship", "meta"]
---

# kol-archival-writer

Runs when `post_collab_archival` is the active meta goal. Persists outcome into
`kol_relationship` and writes required `approval.*` archival facts.

## Bridge tool (required)

```bash
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py archive-identity \
  --identity-id ID --env LIVE --json '{
  "campaign_id": "C1",
  "outcome": "success",
  "preferred_mode": "gifted",
  "negotiation_style": "soft_anchor",
  "preferred_skus": ["SKU-1"],
  "delivery_quality": 0.9,
  "decided_by": "skill:kol-archival-writer"
}'
```

HTTP: `POST /identities/{id}/archive` with the same body fields.

### `negotiation_style` (learning field)

Set when the thread showed clear negotiation behavior:

| Value | When |
|-------|------|
| `hard_anchor` | Firm rate floor, refused gifted-only |
| `soft_anchor` | Flexible once scope clear |
| `unknown` | Not enough signal |

This feeds `reusable_facts.facts.personalization_hint` on the next campaign via
dispatch context. Load with `get-dispatch-context --view agent` before archival writes.

## Pitfalls

- Do not mark `success` without delivery evidence in facts/events.
- Always pass explicit `env=LIVE` for production archival.
