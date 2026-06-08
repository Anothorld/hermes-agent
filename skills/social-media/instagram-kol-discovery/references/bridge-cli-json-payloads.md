# Bridge CLI JSON payloads (Instagram discovery)

File-backed `@/tmp/*.json` payloads for the bridge CLI. Use **terminal**
(one subcommand per call), not `execute_code` loops.

## CLI invocation

**Gateway runs** (terminal cwd often `$HOME`): use the absolute wrapper from the
brief `# terminal_safety` block, e.g.:

```bash
<HERMES_AGENT_ROOT>/plugins/kol-ops-bridge/scripts/kol-bridge-cli <subcommand> --env LIVE ...
```

**Local shell** from `hermes-agent/` repo root:

```bash
python3 plugins/kol-ops-bridge/scripts/kol_bridge_tool.py <subcommand> --env LIVE ...
```

CLI failures print one JSON line on **stdout** (`error`, `hint`). Empty terminal
output with exit 2 means read stdout — never fall back to `execute_code`.

## ingest-confirmed-candidate — required shape

`ingest-confirmed-candidate` expects **`IngestConfirmedCandidateBody`**: three
top-level objects — `source`, `identity`, `candidate` — plus optional
`identity_facts` and `ingest_id`. `--env` may be passed on the CLI; it can also
appear in the JSON file.

### Wrong (fails CLI validation)

Flat “profile card” JSON is **not** valid. These fields are in the wrong place
or use the wrong names:

```json
{
  "handle": "techbymidas",
  "display_name": "Tomi | Midas",
  "platform": "instagram",
  "profile_url": "https://www.instagram.com/techbymidas/",
  "bio": "Tech, Gaming & Lifestyle"
}
```

Typical errors: `json_missing_field` for `source`, then `identity`, then
`candidate`. Do **not** reuse `upsert-identity` or `/tmp/identity_<handle>.json`
for ingest — that file shape is different (`primary_handle` at top level only,
no `candidate` block).

### Correct minimal ingest

```json
{
  "source": "skill:instagram-kol-discovery",
  "identity": {
    "primary_handle": "techbymidas",
    "platform": "instagram",
    "display_name": "Tomi | Midas"
  },
  "candidate": {
    "source": "discovery:profile_verification",
    "discovery_score": 82,
    "payload": {
      "evidence_url": "https://www.instagram.com/techbymidas/",
      "reason": "..."
    }
  },
  "identity_facts": {
    "identity.instagram_profile_url": "https://www.instagram.com/techbymidas/",
    "identity.instagram_profile_url_source": "ig_bio",
    "identity.instagram_profile_url_discovered_at": "2026-06-04T12:00:00Z",
    "identity.instagram_profile_url_discovered_url": "https://www.instagram.com/techbymidas/"
  }
}
```

```bash
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py ingest-confirmed-candidate \
  --campaign-id <campaign_id> --env LIVE \
  --json @/tmp/ingest_techbymidas.json
```

| Field | Location | Notes |
|-------|----------|--------|
| Workflow origin | top-level `source` | e.g. `skill:instagram-kol-discovery` |
| Handle | `identity.primary_handle` | not `handle` |
| Profile URL | `identity_facts["identity.instagram_profile_url"]` | not top-level `profile_url` |
| Bio / pillars | allowed `identity_facts` keys only | see `confirmed_ingest.ALLOWED_IDENTITY_FACT_KEYS` in bridge plugin |
| Candidate provenance | `candidate.source` | required; separate from top-level `source` |

### Ingest validation rules

- Top-level `source` ≠ `identity.*_source` provenance enums.
  - `identity.*_source` must be one of: `google_search_result`, `linktree`,
    `ig_bio`, `facebook_about`, `fb_creator_profile`, `personal_site`,
    `media_kit`, `agency_page`, `ig_profile_and_reels`, `ig_reel_pick`,
    `llm_summary`.
- Every `identity.*_url` in `identity_facts` must be absolute `http(s)`.
- `identity.linktree_url` hosts: `linktr.ee`, `beacons.ai`, `bio.link`,
  `lnk.bio`, `solo.to`, `linkin.bio` — otherwise use `identity.personal_site_url`
  or omit.
- `identity.primary_email`: only real `x@y.tld`; omit if unknown (email discovery runs later).
- Optional field fails validation → remove that field and retry same handle; do not guess formats.

### Not for cold outreach

`kol-cold-outreach` runs **after** shortlist approval when `identity_id` already
exists. Use `get-dispatch-context` + `persist-initial-outreach-draft` — **not**
`ingest-confirmed-candidate`.

### Buffer fallback

```bash
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py buffer-confirmed-candidate \
  --campaign-id <campaign_id> --env LIVE --json @/tmp/ingest_<handle>.json
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py replay-ingest-buffer --limit 50
```

Same JSON shape as direct ingest.

---

## upsert-identity (legacy / non-ingest)

Use only when **not** calling `ingest-confirmed-candidate`. Must use
`primary_handle`, not `handle`:

```json
{
  "primary_handle": "thecozyfarmhouse",
  "platform": "instagram",
  "display_name": "Michelle Anzaldua | Home Decor",
  "env": "LIVE"
}
```

`primary_email` — only a real address; omit if unknown. Link-in-bio URLs belong
in `identity.linktree_url` / `identity.personal_site_url` via `write-facts-multi`,
not in `primary_email`.

```bash
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py upsert-identity \
  --env LIVE --json @/tmp/identity_<handle>.json
```

---

## write-facts-multi

Requires `--identity-id`. Pattern:

```bash
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py write-facts-multi \
  --env LIVE --identity-id <id> --json @/tmp/facts.json
```

---

## add-candidate (legacy three-step chain)

Deprecated when ingest endpoint is available. JSON must include `identity_id`:

```json
{
  "identity_id": 655,
  "platform": "instagram",
  "source": "rediscovery_profile_verification",
  "discovery_score": 82,
  "candidate_status": "discovered",
  "payload": {
    "evidence_url": "https://www.instagram.com/thecozyfarmhouse/",
    "followers": "220K",
    "reason": "..."
  }
}
```

---

## Recommended discovery flow

1. `browser_navigate` to profile URL and collect evidence.
2. Write `/tmp/ingest_<handle>.json` (nested shape above).
3. `ingest-confirmed-candidate` — one handle per terminal call.
4. Optional `list-candidates` verification.

Legacy order (do not use in new runs): `upsert-identity` → `write-facts-multi`
→ `add-candidate`.

For rediscover floor-only runs, completing ingest per verified handle is enough;
do not emit `shortlist_ready` unless the run contract requires it.

---

## Auto-retry / rediscover pending ingest

When a discover/rediscover run ends with qualified handles **not** yet in CAL,
emit structured diagnostics (see `instagram-kol-discovery` SKILL.md):

```yaml
pending_ingests:
  - "techbymidas — iteration limit before ingest-confirmed-candidate"
```

The KOL Ops Console quantity gate stores this in `diagnostics_history` and, on
auto-retry or operator `/rediscover`, injects `# resume_directives` into the
next brief with **STEP_0: ingest these handles first**.

**Cross-run constraints:**

- `/tmp/ingest_<handle>.json` from the prior run is **not** available — rebuild
  nested `source` / `identity` / `candidate` / `identity_facts` from fresh
  profile evidence (`browser_navigate` if needed).
- Handles already in `list-candidates` are dropped from `resume_directives`
  automatically.
- Prose sections like `### Next round should:` are **not** parsed — use
  `pending_ingests` and `next_round_focus` field names exactly.
