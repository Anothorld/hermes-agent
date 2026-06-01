When persisting Instagram rediscovery candidates through kol_bridge_tool.py, prefer file-backed JSON payloads and the bridge CLI shape exactly.

Observed durable patterns

1. upsert-identity JSON must use primary_handle, not handle.
Minimal correct shape:
{
  "primary_handle": "thecozyfarmhouse",
  "platform": "instagram",
  "display_name": "Michelle Anzaldua | Home Decor",
  "env": "LIVE"
}
If you pass handle instead, the CLI returns json_missing_field / primary_handle.

primary_email — only a real address.
- IF the IG profile actually exposes a contact email matching `x@y.tld` (bio text, contact button reveal, pinned post, OCR of a bio image), include it: `"primary_email": "hello@kolsite.com"`. The bridge normalizes (strip + lowercase) and stores it; record provenance facts (`identity.email_source = "ig_bio"`, `identity.email_discovered_at`, `identity.email_discovered_url`, `identity.email_discovery_tier = "0"`) in the same write-facts-multi call.
- ELSE omit primary_email entirely. kol-email-discovery runs post-approval and will resolve it. The bridge rejects any non-email-shaped value (link-in-bio URLs, personal website domains, brand names) with a 422 / ValueError — do not waste a turn trying.

If you observed a link-in-bio URL (`linktr.ee/<handle>`, `beacons.ai/<handle>`, etc.) or a creator-owned personal website domain on the IG profile while qualifying, persist it via write-facts-multi under `identity.linktree_url` or `identity.personal_site_url` (same keys kol-email-discovery uses) — never via primary_email.

2. write-facts-multi requires --identity-id.
Do not assume --handle works for this subcommand. Safe pattern:
python .../kol_bridge_tool.py write-facts-multi --env LIVE --identity-id <id> --json @/tmp/facts.json

3. add-candidate JSON must include identity_id.
Do not rely on handle-only candidate payloads. Safe shape:
{
  "identity_id": 655,
  "platform": "instagram",
  "source": "rediscovery_profile_verification",
  "discovery_score": 82,
  "relationship_status": "new_prospect",
  "candidate_status": "discovered",
  "payload": {
    "evidence_url": "https://www.instagram.com/thecozyfarmhouse/",
    "followers": "220K",
    "reason": "..."
  }
}

4. Recommended persistence order for each verified candidate (preferred)
- browser_navigate to profile URL and collect profile evidence
- build one file-backed ingest payload: /tmp/ingest_<handle>.json
- ingest-confirmed-candidate (single deterministic endpoint: identity → facts → candidate)
- optional list-candidates verification

ingest-confirmed-candidate minimal shape:
{
  "env": "LIVE",
  "source": "skill:instagram-kol-discovery",
  "ingest_id": "<uuid>",
  "identity": {
    "primary_handle": "thecozyfarmhouse",
    "platform": "instagram"
  },
  "candidate": {
    "source": "rediscovery_profile_verification",
    "discovery_score": 82,
    "payload": {
      "evidence_url": "https://www.instagram.com/thecozyfarmhouse/",
      "followers": "220K",
      "reason": "..."
    }
  },
  "identity_facts": {
    "identity.instagram_profile_url": "https://www.instagram.com/thecozyfarmhouse/",
    "identity.instagram_profile_url_source": "ig_bio",
    "identity.instagram_profile_url_discovered_at": "<iso8601>",
    "identity.instagram_profile_url_discovered_url": "https://www.instagram.com/thecozyfarmhouse/"
  }
}

ingest payload hard validation rules (apply before CLI call):

- Treat top-level `source` and `identity.*_source` as different fields:
  - top-level `source` = workflow origin string (for example `skill:instagram-kol-discovery`);
  - `identity.*_source` must be one of:
    `google_search_result`, `linktree`, `ig_bio`, `facebook_about`,
    `fb_creator_profile`, `personal_site`, `media_kit`, `agency_page`,
    `ig_profile_and_reels`, `ig_reel_pick`, `llm_summary`.
- Every `*_url` in `identity_facts` must be an absolute `http(s)` URL.
- `identity.linktree_url` accepts only these hosts:
  `linktr.ee`, `beacons.ai`, `bio.link`, `lnk.bio`, `solo.to`, `linkin.bio`.
- If a link-in-bio host is not on the allowlist (for example `msha.ke`), do not force it into `identity.linktree_url`; either:
  - omit that field, or
  - store the full URL under `identity.personal_site_url` when it is creator-owned.
- Optional-field policy: when a field fails validation and is not required for identity/candidate creation, remove the field and retry the same ingest (do not keep guessing alternate string formats).

CLI:
python .../kol_bridge_tool.py ingest-confirmed-candidate \
  --campaign-id <campaign_id> --env LIVE --json @/tmp/ingest_<handle>.json

Fallback when ingest endpoint unavailable:
python .../kol_bridge_tool.py buffer-confirmed-candidate \
  --campaign-id <campaign_id> --env LIVE --json @/tmp/ingest_<handle>.json
python .../kol_bridge_tool.py replay-ingest-buffer --limit 50

Legacy three-step chain (deprecated for new runs):
- upsert-identity with primary_handle (NO primary_email)
- write-facts-multi with --identity-id
- add-candidate with identity_id in JSON

5. Use file-backed JSON by default.
This avoids shell quoting issues and keeps bridge writes reproducible:
- /tmp/identity_<handle>.json
- /tmp/facts_<handle>.json
- /tmp/candidate_<handle>.json
Then call each CLI subcommand with --json @/tmp/<file>.json

6. Discovery-floor resume use case
For rediscover runs whose only requirement is "persist N additional verified candidates", completing verified identity/facts/candidate writes is sufficient. Do not emit shortlist_ready unless the run contract explicitly asks for operator approval readiness.
