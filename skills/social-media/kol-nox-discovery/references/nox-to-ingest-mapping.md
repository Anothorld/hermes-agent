# Nox search → CAL ingest mapping

After operator selects rows from `creator-search`:

```bash
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py ingest-confirmed-candidate \
  --campaign-id <cid> --env <env> --json @/tmp/ingest.json
```

Suggested `ingest.json` fields:

- `source`: `skill:kol-nox-discovery`
- `payload_json.nox_creator_id`: search item `id` (Nox creator id)
- `payload_json.discovery_source`: `noxinfluencer_api`
- Platform URL/handle from search item when present

Do **not** re-run Nox for ingest. Use `identity.email_source=noxinfluencer_api` only when email came from Gate B `contacts`.
