---
name: kol-email-discovery
description: Finds a KOL's outreach email when CAL has no `identity.primary_email`, prioritizing the creator's personal collab/business inbox over talent-agency or MCN mailboxes. Tries Google Search + WebFetch first, then BrowserUse for JS surfaces. Writes `primary_email` + provenance on hit; returns `{"found": false, "tried": [...]}` on miss — never guesses.
trigger: Invoked by the post-approval orchestrator (web `approve-shortlist` agent run, or chat-side "approve KOLs ..." flow) for each approved identity whose `identity.primary_email` is empty. Also invocable on demand when the operator says "find an email for @<handle>".
tags: ["kol", "outreach", "enrichment", "email", "contact-discovery", "pre-draft"]
---

## Goal
Resolve a single KOL identity's **personal collaboration email** from
public sources so the cold/reengagement outreach draft skills have a
direct `to:` address. Agency / talent-management inboxes are a **last
resort** only when no verified creator-owned address exists within the
page-load budget. Never fabricate. A miss is a legitimate outcome —
escalation, not invention.

## Runtime Contract
- Profile: `outreach-operator`.
- Bridge is the only CAL writer. Forbidden: `cal.py` import, direct
  `~/.hermes/kol-ops-bridge/cal.db` access, ad-hoc SQL, `execute_code`
  against the DB. Use `plugins/kol-ops-bridge/scripts/kol_bridge_tool.py`.
  `--env <TEST|LIVE>` mandatory.
- **No sending, no drafting.** This skill resolves contact data only.
  The orchestrator decides what to do with the result.
- **No guessing.** Heuristic addresses (e.g. `firstname@brand-domain`,
  `hello@<personal-site>`) are explicitly forbidden — even with an
  `unverified` flag. The operator has signed off on this policy: a
  bad-address send burns the prospect; a miss costs nothing but a
  human-review step.
- **Single-shot per (identity, env):** if `identity.primary_email` is
  already non-empty, abort and return `{"skipped": "already_has_email",
  "email": "<existing>"}`. Do NOT overwrite.
- **Web tools only.** No paid enrichment APIs, no scraping LinkedIn at
  scale, no databroker lookups. Public surfaces only: the creator's own
  bio, link-in-bio aggregators, their personal/portfolio site, their
  press / media kit page, their newsletter sign-up, their podcast show
  notes, public Facebook profile/Page About sections, and read-only
  Facebook creator discovery metadata when the configured tool is
  already available. Public **agency rosters** are allowed only as a
  fallback surface after creator-owned paths are exhausted.
- **Personal email first (hard preference).** Within the page-load
  budget, exhaust creator-owned contact surfaces before accepting an
  agency/management inbox. Do not stop at the first verified address
  when that address is agency-classified and cheaper creator-owned
  paths (IG bio, link-in-bio, personal `/contact`) remain untried.

## Inputs
1. `identity_id` (mandatory).
2. `env` (`TEST` or `LIVE`, mandatory).
3. (Optional) `campaign_id` — attached to provenance facts so audit
   trails carry which campaign drove the lookup.

## Procedure

### Step 1 — Load identity
```
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py get-identity \
  --identity-id <identity_id> --env <TEST|LIVE>
```

Use:
- `primary_handle`, `platform` — handle to search for.
- `primary_email` — if non-empty, abort with
  `{"skipped": "already_has_email", "email": "<value>"}`.
- `display_name`, `region`, `language` — disambiguation for noisy
  search results.

### Step 2 — Tier 1: Google Search + WebSearch + WebFetch
Cheap, fast, and covers ~70-80% of public creators. Run discovery in
**surface-priority order** (creator-owned first, agency last). Use Google
Search directly when a browser/search tool is available; otherwise run
the same query strings through `WebSearch`. Record each query in `tried`
as `GoogleSearch:"..."` or `WebSearch:"..."`.

Maintain two in-memory candidate slots while crawling (never written
until Step 4a):
- `personal_candidate` — verified creator-owned collab/business email.
- `agency_candidate` — verified agency/management inbox (fallback only).

**Stopping rule.** Persist immediately when `personal_candidate` is set
and all higher-priority creator-owned surfaces in the checklist below
are either tried or skipped (no URL known). If only `agency_candidate`
is set, keep searching creator-owned surfaces until the page-load budget
is exhausted, then persist the agency address as last resort.

#### Surface-priority checklist (try in this order)

1. **Creator link-in-bio** — Linktree / Beacons / bio.link / lnk.bio /
   solo.to / linkin.bio for the handle.
2. **Creator personal site** — `/contact`, `/about`, `/work-with-me`,
   `/press`, `/media-kit` on a domain visibly owned by the creator
   (handle/name in domain or page branding).
3. **Creator platform profiles** — public IG bio (Tier 2 if JS-gated),
   Facebook About/Contact on the creator's own Page/profile.
4. **Creator media kit** — PDF/page on the creator's domain or link-in-bio
   (not a third-party press aggregator).
5. **Targeted Google on creator signals** — Path A queries below,
   prioritizing results whose URLs belong to surfaces 1–4.
6. **Agency / management roster (fallback only)** — official agency
   page listing this creator by name + handle, or a Google hit clearly
   titled "represented by …" / "talent management". Record any hit
   in `agency_candidate` only; do not return yet if surfaces 1–4 remain
   untried within budget.

#### Path A — Google search queries

Run these after (or in parallel with) direct fetches of known
link-in-bio / personal-site URLs from CAL or earlier search hits.
Prefer opening creator-owned result URLs before agency domains.

1. `"<handle>" (email OR contact OR "business inquiries" OR collab OR partnership)`
2. `"<display_name>" "<region>" (email OR contact OR "business inquiries")` (only if `display_name` is present and the handle didn't disambiguate)
3. `site:instagram.com "<handle>" (email OR contact OR "business inquiries")`
4. `(site:linktr.ee OR site:beacons.ai OR site:bio.link) "<handle>"`
5. `"<handle>" (site:<personal_domain> OR "<personal_domain>") contact` (only when CAL or a prior hit exposes a creator personal domain)
6. `site:facebook.com "<handle>" (email OR contact OR "business inquiries" OR "about")`
7. `"<display_name>" "<handle>" (talent OR management OR agency OR "represented by")` — **fallback only**, after 1–6 yield no `personal_candidate`

Add local-language contact words from `language` / `region` when they
are obvious (for example `合作`, `商务`, `pr`, `contacto`) but do not
spray broad translations that bury the identity signal.

#### Path B — Fetch promising result URLs

For each promising result URL, call `WebFetch(url,
"Extract any email addresses associated with this creator and the
surrounding context that shows the address belongs to them.")`

Do not accept an email from a Google result snippet alone. Open the
source page or public cached text and verify that the page visibly
belongs to the creator.

#### Path C — Facebook discovery path

Use this path when `platform` is Facebook, CAL already has a Facebook
profile/Page URL, Google/Search returns an official Facebook result, or
the creator was originally sourced through `fb_creator`.

1. Fetch the public Facebook profile/Page URL and its public About /
   Contact surface if available. Accept only emails visible on that
   public page and associated with the creator/Page.
2. If `fb_creator` tooling is available and already configured, use it
   only to resolve or confirm public identity metadata such as
   `profile_url`, alias, bio, and creator id. Then fetch the returned
   public `profile_url` or linked website before accepting any email.
3. If Facebook requires login, blocks the page, or exposes only a DM /
   Messenger route, record the attempted URL and continue. Do not log
   in, send a message, or treat a Messenger button as an email hit.

Verification rules — an email candidate must clear ALL of these to
count as verified:
- Appears on a page that visibly belongs to the creator (their domain,
  their named profile) **or**, as fallback only, their official agency
  page listing them by name + handle. Random third-party aggregators
  don't count.
- Local part is not obviously a role inbox of a different brand
  (e.g. `support@unrelatedbrand.com` found in a sidebar).
- Not a `noreply@`, `donotreply@`, `notifications@` address.
- Not a Mailchimp / Substack tracking address
  (`*@email.mailchimpapp.com`, `*@substack.com` notification reflectors).

#### Classify each verified address: personal vs agency

**Personal (preferred)** — treat as `personal_candidate`:
- Domain matches the creator's personal site, link-in-bio target domain,
  or handle/name (e.g. `hello@janedoe.com`, `collab@byjanedoe.co`).
- Found on the creator's IG bio, link-in-bio page, personal
  `/contact` / `/work-with-me`, or creator-branded media kit.
- Labeled "business inquiries", "collabs", "partnerships", "PR",
  "work with me", or equivalent **on a creator-owned page**.
- `mailto:` link next to the creator's own name (not "contact our
  team at … agency").

**Agency (fallback only)** — treat as `agency_candidate`; do not
persist until creator-owned surfaces are exhausted:
- Domain is a talent agency, MCN, management firm, or casting company
  (page header/footer names the agency; roster lists multiple creators).
- Copy says "represented by", "managed by", "for bookings contact",
  "talent inquiries", or lists the creator under an agent's name.
- Local parts like `talent@`, `bookings@`, `creators@`, `management@`,
  `partnerships@` on a **non-creator** domain.
- Google result/snippet is clearly an agency roster, not the creator's
  own site.

When uncertain, prefer classifying as personal only if the page is
single-creator branded; if multiple creators are listed, classify as
agency.

If multiple emails appear on the same page, prefer in this order:
1. Personal address labeled "business inquiries" / "collabs" /
   "partnerships" / "PR" / "work with me".
2. Personal `mailto:` link associated with the creator's name.
3. Other personal address on a creator-owned contact page.
4. Agency inbox explicitly scoped to this creator (named agent line).
5. Generic agency talent/bookings inbox — only if nothing above exists
   anywhere within budget.

Common public-surface URLs to try when Google/Search returns a
link-in-bio, personal-site, or Facebook target:
- `linktr.ee/<handle>`, `beacons.ai/<handle>`, `bio.link/<handle>`,
  `lnk.bio/<handle>`, `solo.to/<handle>`, `linkin.bio/<handle>`
- `<handle>.com`, `<handle>.co`, `<displayname>.com` — only if
  search results already point at them.
- `facebook.com/<handle>`, `facebook.com/<page_slug>/about`, or the
  canonical Facebook `profile_url` from `fb_creator` / search results
  — only public pages; never behind-login content.

Record every URL you actually fetched in a running `tried` list so the
final envelope (and any escalation) can show what was checked.

**Immediate persistence rule (hard).** This skill must not keep
"found-but-not-written" facts in long-running memory.

- When `personal_candidate` is verified and all higher-priority
  creator-owned surfaces in the checklist are tried or skipped: run
  Step 4a **immediately** with that address.
- When only `agency_candidate` exists: finish the surface-priority
  checklist and Tier 2 within the page-load budget first; persist the
  agency address only if no `personal_candidate` appears.
- Forbidden pattern: persist an agency inbox while IG bio, link-in-bio,
  or known personal-site `/contact` URLs remain untried; or continue
  crawling/summarizing after a personal hit while delaying writes.

Durable state in CAL is the source of truth; in-memory notes are not.

### Step 3 — Tier 2: BrowserUse fallback
Only invoke when Step 2 returns no verified hit. Tier 2 is reserved
for surfaces that WebFetch cannot render (Instagram bio behind JS,
Beacons/Linktree pages that gate email behind a click, personal sites
that lazy-load contact blocks).

Use the built-in BrowserUse tools — `browser_navigate`,
`browser_snapshot`, `browser_get_images`, `browser_click`,
`vision_analyze`. Do NOT use the `mcp_chrome_devtools_*` family.

Browse sequence (prefer personal; hold agency as fallback):
1. `https://www.instagram.com/<handle>/` — snapshot bio text + the
   link-in-bio URL if present. Bio emails are almost always personal.
2. The link-in-bio target itself (Linktree / Beacons / personal site).
3. The "Contact" / "About" / "Press" / "Work with me" subpage of the
   personal site if the homepage didn't surface an address.
4. The official public Facebook profile/Page/About URL when Step 2
   found one or the identity's platform is Facebook.
5. If the bio shows an email embedded in an image (common on IG to
   defeat scrapers), use `browser_get_images` + `vision_analyze` with
   prompt: "Extract any email addresses visible in this image. Reply
   with addresses only, one per line, or 'NONE'."
6. Agency roster / management page — **only when** steps 1–5 found no
   `personal_candidate` and Step 2 already surfaced an agency URL.

Budget cap: at most 8 fetched/rendered page loads total across Tier 1
and Tier 2 per identity. Search queries and read-only `fb_creator`
metadata lookups do not count as page loads, but every opened result,
Facebook About page, and link-in-bio page does. If still no hit after
the cap, treat as miss.

Apply the same verification rules from Step 2 to any Tier 2 candidate.

### Step 4a — On hit: persist + return
Choose the winning address: `personal_candidate` if set, else
`agency_candidate`. Two calls, in this order:

```
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py upsert-identity \
  --primary-handle <handle> --primary-email <email> --env <TEST|LIVE>
```

```
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py write-facts-multi \
  --identity-id <identity_id> --env <TEST|LIVE> \
  --json '{"campaign_id":"<campaign_id_or_null>",
            "source":"skill:kol-email-discovery",
            "namespaces":{
              "identity": {
                "identity.email_source":         "<google_search_result|linktree|ig_bio|facebook_about|fb_creator_profile|personal_site|media_kit|agency_page|...>",
                "identity.email_discovered_at":  "<iso8601>",
                "identity.email_discovered_url": "<verbatim URL the address came from>",
                "identity.email_discovery_tier": "<1|2>"
              }
            }}'
```

If `upsert-identity` succeeds but `write-facts-multi` returns
`FactNamespaceError`, do NOT roll back the email. Fix the payload
immediately (usually missing/invalid provenance fields), retry
`write-facts-multi` for the same identity, and only then continue.
Return success only after provenance write succeeds.

#### Step 4a-bis — Social-platform URL side effect (no extra budget)
While browsing the pages above in Step 2/3, you already see most of
the creator's cross-platform profile links (link-in-bio aggregators
literally list them all on one page; personal sites usually have a
"Follow me on …" row). **Extract them as a free side effect** — do
NOT open additional pages for this, do NOT extend the page-load
budget.

For each page you actually fetched while resolving the email, scan its
body text / link table for these domains and capture the URL value:

| Domain match | Fact key |
|---|---|
| `instagram.com/...` | `identity.instagram_profile_url` |
| `tiktok.com/@...` | `identity.tiktok_profile_url` |
| `youtube.com/...`, `youtu.be/...` | `identity.youtube_profile_url` |
| `facebook.com/...`, `fb.com/...` | `identity.facebook_profile_url` |
| `twitter.com/...`, `x.com/...` | `identity.twitter_profile_url` |
| `threads.net/@...`, `threads.com/@...` | `identity.threads_profile_url` |
| `linktr.ee/...`, `beacons.ai/...`, `bio.link/...`, `lnk.bio/...`, `solo.to/...`, `linkin.bio/...` | `identity.linktree_url` |
| Creator-owned personal domain (same as email domain or visibly the creator's site) | `identity.personal_site_url` |

Apply the SAME verification rules as for the email (the URL must
appear on a page that visibly belongs to the creator). Do NOT
fabricate `instagram.com/<handle>` from a bare handle.

**Do NOT overwrite a non-empty existing value** — read the identity
facts first (or rely on the bridge's read-before-write) and only
include keys that are currently unset.

Add the resolved keys to the same `write-facts-multi` payload in Step
4a, in the same `identity` namespace, each with the provenance triple
`<key>_source`, `<key>_discovered_at`, `<key>_discovered_url`. Source
value mirrors where the URL was found (e.g., `linktree`,
`personal_site`, `ig_bio`, `facebook_about`).

On miss for a given platform, simply omit the key — no `still_missing`
list needed in the email-discovery envelope. The dedicated
`kol-social-link-discovery` skill exists for when the operator wants
to retry just the URL discovery.

Return envelope (single JSON object, no prose, no markdown):

```json
{
  "skill": "kol-email-discovery",
  "identity_id": 42,
  "env": "TEST",
  "found": true,
  "email": "collab@janedoe.com",
  "source": "ig_bio",
  "email_class": "personal",
  "tier": 1,
  "discovered_url": "https://www.instagram.com/janedoe/",
  "tried": ["https://linktr.ee/janedoe", "https://www.instagram.com/janedoe/"]
}
```

Agency fallback example (only after creator-owned surfaces exhausted):

```json
{
  "skill": "kol-email-discovery",
  "identity_id": 42,
  "env": "TEST",
  "found": true,
  "email": "talent@example-agency.com",
  "source": "agency_page",
  "email_class": "agency",
  "tier": 1,
  "discovered_url": "https://example-agency.com/talent/janedoe",
  "tried": ["https://linktr.ee/janedoe", "https://www.instagram.com/janedoe/", "https://janedoe.com/contact", "GoogleSearch:\"@janedoe\" talent management", "https://example-agency.com/talent/janedoe"]
}
```

### Step 4b — On miss: return, do NOT escalate from here
The orchestrator owns escalation policy (it may want to batch-open
escalations after processing the whole approved set, or attach
campaign-level operator notes). This skill returns the miss verbatim:

```json
{
  "skill": "kol-email-discovery",
  "identity_id": 42,
  "env": "TEST",
  "found": false,
  "tried": [
    "GoogleSearch:\"@handle\" email contact",
    "WebSearch:\"@handle\" email contact",
    "https://www.facebook.com/handle/about",
    "https://linktr.ee/handle",
    "https://www.instagram.com/handle/",
    "https://handle.com/contact"
  ],
  "reason_hint": "no verified address on bio, Facebook, link-in-bio, or personal site"
}
```

The orchestrator's expected next action for a miss is
`open-escalation` with `reason="contact_email_not_found"` and the
`tried` list pasted into `question_to_operator`.

## Minimal canonical output (format anchor)

Use this as a shape check; keep keys stable and return JSON only:

```json
{
  "skill": "kol-email-discovery",
  "identity_id": 42,
  "env": "TEST",
  "found": true,
  "email": "collab@janedoe.com",
  "source": "ig_bio",
  "email_class": "personal",
  "tier": 1,
  "discovered_url": "https://www.instagram.com/janedoe/",
  "tried": ["https://linktr.ee/janedoe", "https://www.instagram.com/janedoe/"]
}
```

## Pitfalls
- Never construct an email from a name + a guessed domain
  (`firstname@brand-domain`). Operator policy: miss > guess.
- Never persist an **agency** inbox while creator-owned surfaces (IG bio,
  link-in-bio, personal `/contact`) remain untried within budget.
- Never treat the first Google hit as done when it is an agency roster
  and the creator's Linktree or personal site is still unchecked.
- Never write a Mailchimp / Substack / newsletter reflector address as
  the primary email. Those are one-way; outreach to them silently
  drops.
- Never accept a Google snippet as the evidence URL. The discovered URL
  must be the page where the email was visible and verified.
- Never log into Facebook, send a DM, click Messenger, or use private /
  behind-login Facebook data. Public About/profile metadata is allowed;
  private contact routes are not.
- Do not call `cal.py` / direct SQL / `execute_code`. The two writes
  (`upsert-identity` + `write-facts-multi`) are the entire write
  surface.
- Do not invoke `kol-cold-outreach` or `kol-reengagement-outreach`
  from inside this skill. Returning the envelope is the entire
  hand-off — the orchestrator chains the next step.
- Do not retry `upsert-identity` when only `write-facts-multi` failed.
  The email column write already landed; fix and retry the facts payload
  instead.
- Budget cap is a hard ceiling, not a soft target. Eight page loads
  per identity is enough to clear public surfaces; further crawling
  is the operator's job, not the skill's.
