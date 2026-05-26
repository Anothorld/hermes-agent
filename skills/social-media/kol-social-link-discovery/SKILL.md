---
name: kol-social-link-discovery
description: Searches the public web for a KOL's social-platform profile URLs (TikTok, YouTube, Facebook, X/Twitter, Threads, Linktree/Beacons, personal site) and persists each newly resolved URL as a reusable `identity.<platform>_profile_url` fact. Mirrors `kol-email-discovery`'s tiered browsing (Google + WebFetch first, BrowserUse fallback) and verification rules. Does NOT overwrite existing non-empty URL facts. On total miss, returns `{found: false, tried: [...]}` — does NOT open an escalation (missing social URLs are non-blocking for outreach).
trigger: Invoked by the web detail page's "🔍 搜索社交主页" button via `POST /kols/{id}/discover-social-links`. Not auto-chained from the orchestrator — `kol-email-discovery` already captures these URLs as a side effect when it browses link-in-bio / personal-site pages, so this skill exists to cover KOLs whose email is already known (and so won't trigger email-discovery).
tags: ["kol", "enrichment", "social-links", "profile-url", "quick-link", "post-discovery"]
---

## Goal
Resolve a single KOL identity's cross-platform profile URLs from public
sources so the web detail page's "快速跳转" bar can offer one-click jumps
to the creator's TikTok / YouTube / Facebook / X / Threads / link-in-bio /
personal site. Never fabricate. A partial result (some URLs resolved,
others not) is the normal outcome — write what you verified, return the
miss list verbatim.

## Runtime Contract
- Profile: `outreach-operator`.
- Bridge is the only CAL writer. Forbidden: `cal.py` import, direct
  `~/.hermes/kol-ops-bridge/cal.db` access, ad-hoc SQL, `execute_code`
  against the DB. Use `plugins/kol-ops-bridge/scripts/kol_bridge_tool.py`.
  `--env <TEST|LIVE>` mandatory.
- **No sending, no drafting, no email lookup.** This skill resolves
  social-platform URLs only. If you stumble across an email on a page,
  ignore it — `kol-email-discovery` owns that surface.
- **No guessing.** Constructing `instagram.com/<handle>` without
  verifying the page belongs to the creator is forbidden. The whole
  point of resolving each URL is that the bare handle on one platform
  doesn't necessarily match the handle on another. A miss costs
  nothing (no quick-link button shown); a wrong URL would send the
  operator to a stranger's profile.
- **Single-shot per (identity, env):** if every target fact key is
  already non-empty, abort and return
  `{"skipped": "already_has_all_social_links"}`. Do NOT re-verify.
- **Web tools only.** Public surfaces only: link-in-bio aggregators,
  the creator's personal/portfolio site, their media kit / press page,
  public Facebook profile/Page About sections, public agency rosters.
  No paid enrichment APIs, no LinkedIn scraping, no databroker
  lookups, no behind-login data.

## Target fact keys
Each one is a string URL. Write the URL value plus its provenance
triple in the same `write-facts-multi` call:

| URL key | Source key | Discovered-at key |
|---|---|---|
| `identity.instagram_profile_url` | `identity.instagram_profile_url_source` | `identity.instagram_profile_url_discovered_at` |
| `identity.tiktok_profile_url` | `identity.tiktok_profile_url_source` | `identity.tiktok_profile_url_discovered_at` |
| `identity.youtube_profile_url` | `identity.youtube_profile_url_source` | `identity.youtube_profile_url_discovered_at` |
| `identity.facebook_profile_url` | `identity.facebook_profile_url_source` | `identity.facebook_profile_url_discovered_at` |
| `identity.twitter_profile_url` | `identity.twitter_profile_url_source` | `identity.twitter_profile_url_discovered_at` |
| `identity.threads_profile_url` | `identity.threads_profile_url_source` | `identity.threads_profile_url_discovered_at` |
| `identity.linktree_url` | `identity.linktree_url_source` | `identity.linktree_url_discovered_at` |
| `identity.personal_site_url` | `identity.personal_site_url_source` | `identity.personal_site_url_discovered_at` |

Source values are one of: `google_search_result`, `linktree`, `ig_bio`,
`facebook_about`, `personal_site`, `media_kit`, `agency_page`.

## Inputs
1. `identity_id` (mandatory).
2. `env` (`TEST` or `LIVE`, mandatory).
3. (Optional) `campaign_id` — attached to provenance facts so audit
   trails carry which campaign drove the lookup. The URL facts
   themselves are always written with `campaign_id=null` so they're
   reusable across future campaigns.

## Procedure

### Step 1 — Load identity + current URL facts
```
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py get-identity \
  --identity-id <identity_id> --env <TEST|LIVE>
```
```
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py read-facts \
  --identity-id <identity_id> --env <TEST|LIVE>
```

Build a `missing_keys` set: every target URL key (see table above) that
is **not** already present and non-empty in the read-facts response.
If `missing_keys` is empty, abort with
`{"skipped": "already_has_all_social_links"}`.

Use `primary_handle`, `display_name`, `region`, `language`, `platform`
from the identity row for search disambiguation.

### Step 2 — Tier 1: Google Search + WebSearch + WebFetch
Same browsing toolkit as `kol-email-discovery`. Run the discovery paths
below in order; do NOT stop on first hit — keep crawling until either
all `missing_keys` are resolved or the budget cap (8 page loads) is
exhausted. Record each query in `tried` as `GoogleSearch:"..."` or
`WebSearch:"..."`.

#### Path A — Google search queries

1. `"<handle>" (tiktok OR youtube OR facebook OR linktree OR linktr.ee OR beacons.ai)`
2. `"<display_name>" "<region>" (site:tiktok.com OR site:youtube.com OR site:facebook.com)` (only if `display_name` is present and the handle didn't disambiguate)
3. `(site:linktr.ee OR site:beacons.ai OR site:bio.link OR site:lnk.bio OR site:solo.to) "<handle>"`
4. `"<handle>" site:tiktok.com`
5. `"<handle>" site:youtube.com`
6. `"<handle>" site:facebook.com`
7. `"<handle>" "official site" OR "personal site"`

#### Path B — Fetch promising result URLs

For each promising result URL, call `WebFetch(url,
"List every social-media profile link and personal-site link visible on
this page that visibly belongs to the creator. Reply as URL: <url>,
PLATFORM: <name>, EVIDENCE: <one-line reason it belongs to them>.")`.

Aggregate URLs across pages. Common surfaces:
- `linktr.ee/<handle>`, `beacons.ai/<handle>`, `bio.link/<handle>`,
  `lnk.bio/<handle>`, `solo.to/<handle>`, `linkin.bio/<handle>` —
  these are gold (a single page typically lists every platform).
- `<handle>.com`, `<handle>.co`, `<displayname>.com` — personal sites.
- IG profile page (read bio for cross-platform links).
- Facebook About page.

Map extracted URLs to target keys by domain:
- `instagram.com/...` → `identity.instagram_profile_url`
- `tiktok.com/@...` → `identity.tiktok_profile_url`
- `youtube.com/...`, `youtu.be/...` → `identity.youtube_profile_url`
- `facebook.com/...`, `fb.com/...` → `identity.facebook_profile_url`
- `twitter.com/...`, `x.com/...` → `identity.twitter_profile_url`
- `threads.net/@...`, `threads.com/@...` → `identity.threads_profile_url`
- `linktr.ee/...`, `beacons.ai/...`, `bio.link/...`, `lnk.bio/...`,
  `solo.to/...`, `linkin.bio/...` → `identity.linktree_url`
- Anything else with creator-name evidence → `identity.personal_site_url`

### Step 3 — Tier 2: BrowserUse fallback
Only invoke when Step 2 left ≥1 key in `missing_keys`. Reserved for
surfaces WebFetch cannot render (IG bio behind JS, Beacons/Linktree
pages that gate links behind a click, personal sites that lazy-load).

Use built-in BrowserUse tools — `browser_navigate`, `browser_snapshot`,
`browser_get_images`, `browser_click`, `vision_analyze`. Do NOT use the
`mcp_chrome_devtools_*` family.

Browse sequence (continue across all targets, stop on budget):
1. `https://www.instagram.com/<handle>/` — snapshot bio + link-in-bio
   target if present.
2. The link-in-bio target itself (Linktree / Beacons / personal site).
3. Personal-site `/about`, `/contact`, `/links`, `/press` subpages.

Budget cap: at most 8 fetched/rendered page loads total across Tier 1
and Tier 2. Search queries don't count as page loads, but every opened
result, About page, and link-in-bio page does.

### Step 4 — Verification rules
A candidate URL must clear ALL of these to count as a hit:
- Found on a page that visibly belongs to the creator (their domain,
  their named profile, their official agency page listing them by
  name + handle). Random third-party aggregators don't count.
- For each target key, only ONE URL counts. If multiple candidates
  appear, prefer in this order:
  1. URL listed on the creator's own link-in-bio page.
  2. URL listed on the creator's personal site.
  3. URL found via direct platform search where the page handle / bio
     visibly matches `display_name`.
- For `identity.personal_site_url`: must be a creator-owned domain
  (e.g., the same domain the email lives on, or a domain whose home
  page is clearly about the creator). Skip parked domains, expired
  sites, and generic blog farms.
- Not a Mailchimp / Substack tracking URL.

### Step 5 — Persist + return
For every newly resolved key (i.e., in `missing_keys` AND verified
above), write a single `write-facts-multi` call:

```
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py write-facts-multi \
  --identity-id <identity_id> --env <TEST|LIVE> \
  --json '{"campaign_id": null,
            "source": "skill:kol-social-link-discovery",
            "namespaces": {
              "identity": {
                "identity.tiktok_profile_url":                 "https://www.tiktok.com/@kol_handle",
                "identity.tiktok_profile_url_source":          "linktree",
                "identity.tiktok_profile_url_discovered_at":   "<iso8601>",
                "identity.tiktok_profile_url_discovered_url":  "https://linktr.ee/kol_handle",
                "identity.youtube_profile_url":                "https://www.youtube.com/@kolchannel",
                "identity.youtube_profile_url_source":         "linktree",
                "identity.youtube_profile_url_discovered_at":  "<iso8601>",
                "identity.youtube_profile_url_discovered_url": "https://linktr.ee/kol_handle"
              }
            }}'
```

Note `campaign_id: null` — these are reusable identity facts. The
`campaign_id` passed as a skill input is only carried in the audit
trail, not in the fact row's scope column.

**Do NOT overwrite a non-empty existing value.** Step 1 already filtered
to `missing_keys`; if a parallel writer landed a value between Step 1
and Step 5, the bridge's `write-facts-multi` will see the existing
value via its own read-before-write — but be defensive: include only
the newly resolved keys in your call.

### Step 6 — Return envelope
Single JSON object, no prose, no markdown:

```json
{
  "skill": "kol-social-link-discovery",
  "identity_id": 42,
  "env": "TEST",
  "found": true,
  "resolved": [
    {"key": "identity.tiktok_profile_url", "url": "https://www.tiktok.com/@kol_handle", "source": "linktree", "discovered_url": "https://linktr.ee/kol_handle"},
    {"key": "identity.youtube_profile_url", "url": "https://www.youtube.com/@kolchannel", "source": "linktree", "discovered_url": "https://linktr.ee/kol_handle"}
  ],
  "still_missing": ["identity.facebook_profile_url", "identity.threads_profile_url"],
  "tried": ["GoogleSearch:\"@handle\" tiktok", "https://linktr.ee/kol_handle", "https://www.instagram.com/kol_handle/"]
}
```

On total miss (no key resolved):

```json
{
  "skill": "kol-social-link-discovery",
  "identity_id": 42,
  "env": "TEST",
  "found": false,
  "resolved": [],
  "still_missing": ["identity.instagram_profile_url", "identity.tiktok_profile_url", "..."],
  "tried": ["..."],
  "reason_hint": "no verified profile URLs on bio, link-in-bio, or personal site"
}
```

**Do NOT open an escalation on a miss.** Unlike email, missing social
URLs don't block any outreach goal — the quick-link bar simply hides
the buttons for those platforms.

## Examples

### Success — link-in-bio gold mine
`@cozyhome_emma`. Google Search surfaces `linktr.ee/cozyhome_emma`.
WebFetch on that page lists TikTok (`@cozyhome_emma_tt`), YouTube
(`@cozyhomeemma`), Pinterest (skipped — not in target set), and a
personal site `cozyhome.studio`. Five keys resolve in one Tier 1
fetch. Returns `{"found": true, "resolved": [5 entries], "still_missing": ["identity.facebook_profile_url", "identity.threads_profile_url"]}`.

### Success — Tier 2 IG bio with JS-rendered link
`@nanaeats_atl`. Step 2 finds nothing useful (Google snippet only).
Step 3 navigates to IG bio, snapshot shows a Linktree button. Click
through, find TikTok + YouTube URLs. Two keys resolve.

### Partial miss
`@silent_kol_99`. Linktree returns a Spotify playlist (no profile
URLs in target set). IG bio has no cross-platform links. Personal
site doesn't exist. Returns `{"found": false, "resolved": [],
"still_missing": [all 8 keys]}` after 6 page loads. Operator's quick-link
bar shows nothing for this KOL; that's expected.

### Already complete
Every target key is already filled. Step 1 short-circuits with
`{"skipped": "already_has_all_social_links"}`. Do NOT re-verify the
existing URLs.

## Pitfalls
- Never write a URL you didn't verify on a creator-owned surface.
  `instagram.com/<handle>` constructed from the bare handle is a guess,
  not a verified URL.
- Never overwrite a non-empty existing fact. If `identity.tiktok_profile_url`
  is already set, skip it even if you found a different URL on a fresh
  page (operator may have curated it).
- Never open an escalation. Missing social URLs are non-blocking.
- Never call `kol-email-discovery` from inside this skill. If the email
  is missing, the operator runs that separately via its own button.
- Never call `cal.py` / direct SQL / `execute_code`. The single write
  surface is `write-facts-multi` (and `read-facts` for Step 1).
- Do not write the URL fact without its `_source` + `_discovered_at` +
  `_discovered_url` provenance triple. The detail page's audit panel
  relies on the triple to explain where each URL came from.
- Budget cap is a hard ceiling. Eight page loads per identity is enough
  to clear public surfaces; further crawling is the operator's job
  (they can manually fill via the "✏️ 手动添加" form on the detail page).
