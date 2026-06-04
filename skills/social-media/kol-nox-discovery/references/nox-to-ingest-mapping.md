# Nox search → CAL ingest mapping

After operator selects rows from `creator-search`, persist each row with
`ingest-confirmed-candidate` (same nested body as Instagram discovery — **not**
a flat handle/profile card).

```bash
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py ingest-confirmed-candidate \
  --campaign-id <cid> --env <env> --json @/tmp/ingest_<handle>.json
```

Full payload rules: `instagram-kol-discovery/references/bridge-cli-json-payloads.md`.

## Example `ingest_<handle>.json` (YouTube supplement)

Replace placeholders from the Nox search item (`id`, handle, channel URL, score).

```json
{
  "source": "skill:kol-nox-discovery",
  "identity": {
    "primary_handle": "<nox_handle_or_channel_slug>",
    "platform": "youtube",
    "display_name": "<optional from Nox name>"
  },
  "candidate": {
    "source": "discovery:noxinfluencer_supplement",
    "discovery_score": 78,
    "payload": {
      "nox_creator_id": "<search item id>",
      "discovery_source": "noxinfluencer_api",
      "evidence_url": "https://www.youtube.com/@<handle>",
      "reason": "operator-selected Nox supplement row"
    }
  },
  "identity_facts": {
    "identity.nox_creator_id": "<search item id>",
    "identity.nox_creator_name": "<Nox display name>",
    "identity.nox_channel_url": "<channel URL from Nox item when present>",
    "identity.nox_channel_url_source": "google_search_result",
    "identity.nox_channel_url_discovered_at": "2026-06-04T12:00:00Z",
    "identity.nox_channel_url_discovered_url": "<same channel URL>"
  }
}
```

When the platform profile URL is known separately, add
`identity.youtube_profile_url` or `identity.tiktok_profile_url` with the
matching `*_source` / `*_discovered_at` / `*_discovered_url` triple (see
`kol-social-link-discovery` for allowed `*_source` values).

## Rules

- Do **not** re-run Nox API for ingest — use fields already returned by
  `creator-search`.
- Do **not** auto-ingest without operator selection.
- `identity.email_source=noxinfluencer_api` only when email came from Gate B
  `contacts` (with the usual email provenance triples).
- Top-level `source` is always `skill:kol-nox-discovery`; Nox creator id lives in
  `candidate.payload.nox_creator_id` and/or `identity_facts.identity.nox_creator_id`.
