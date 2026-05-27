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

4. Recommended persistence order for each verified candidate
- browser_navigate to profile URL and collect profile evidence
- upsert-identity with primary_handle (NO primary_email)
- write-facts-multi with --identity-id (identity.instagram_profile_url, creator brief, and link-in-bio / personal-site URL if observed)
- add-candidate with identity_id in JSON
- optional list-candidates verification

5. Use file-backed JSON by default.
This avoids shell quoting issues and keeps bridge writes reproducible:
- /tmp/identity_<handle>.json
- /tmp/facts_<handle>.json
- /tmp/candidate_<handle>.json
Then call each CLI subcommand with --json @/tmp/<file>.json

6. Discovery-floor resume use case
For rediscover runs whose only requirement is "persist N additional verified candidates", completing verified identity/facts/candidate writes is sufficient. Do not emit shortlist_ready unless the run contract explicitly asks for operator approval readiness.
