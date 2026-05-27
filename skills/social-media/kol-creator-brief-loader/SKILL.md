---
name: kol-creator-brief-loader
description: Helper skill that returns a "creator brief" markdown block (content pillars, signature hooks, voice descriptors, hero post, recommendation reason) used by every KOL outbound-email skill to personalize the opening. Reads identity-level facts via the Bridge first (passive path). When facts are missing or older than 90 days, actively fetches the KOL's IG profile + 2-3 hero Reels (caption + cover overlay text + first-viewport comments), distills them with a cheap LLM call, and persists the result as `identity.*` facts so future drafts hit the passive path. Never blocks the drafter — on total failure returns an "unavailable" brief block and lets the caller flag `low_personalization`.
trigger: Invoked by every outbound first-contact KOL email skill (`kol-cold-outreach`, `kol-reengagement-outreach`) at prompt-build time, AFTER `kol-email-style-loader` and BEFORE the goal-specific Step 1. Also invocable on demand when the operator says "refresh creator brief for @handle" — in which case run the active-fetch path unconditionally.
tags: ["kol", "outreach", "personalization", "creator-brief", "content-style", "helper"]
---

## Goal
Hand the downstream drafter a short, structured creator brief so the opening
"why-them" line can cite a real detail (content pillar, signature hook, hero
post theme, or campaign-fit reason) instead of falling back to generic
brand-introduction boilerplate. The brief MUST be cheap when facts already
exist (passive path) and MUST gracefully degrade when active fetch fails.

## Runtime Contract
- Profile: `outreach-operator`.
- Bridge is the only CAL writer. Forbidden: `cal.py` import, direct
  `~/.hermes/kol-ops-bridge/cal.db` access, ad-hoc SQL, `execute_code`. Use
  `plugins/kol-ops-bridge/scripts/kol_bridge_tool.py`. `--env <TEST|LIVE>`
  mandatory.
- **Read-mostly.** This skill only writes back to CAL when the active-fetch
  path produced a usable brief. It never opens escalations, never sends
  email, never alters offers/relationships.
- **Never blocks the caller.** Any bridge / browser / LLM failure degrades
  to `brief_status: "unavailable"` and returns an empty brief block. The
  drafter is expected to surface a `low_personalization` flag in that case
  but continue drafting.
- **No paid services.** Do NOT call `veedcrawl_extract` (paid summarization
  is not yet enabled). `veedcrawl_metadata` (free) may be used to read
  view/ER when ranking hero Reels.

## Inputs
1. `identity_id` (mandatory).
2. `env` (`TEST` or `LIVE`, mandatory).
3. (Optional) `campaign_id` — attached to provenance facts so the audit
   trail shows which campaign triggered the refresh.
4. (Optional) `force_refresh: true` — operator-on-demand path that skips
   the passive freshness check and re-runs the active fetch.

## Procedure

### Step 1 — Try the passive path (zero-cost)

Read identity-level facts:

```
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py get-facts \
  --identity-id <identity_id> --env <TEST|LIVE>
```

Inspect the returned `facts` dict for these 6 brief keys plus their
provenance `_discovered_at` triples:

- `identity.content_pillars`
- `identity.signature_hooks`
- `identity.voice_descriptors`
- `identity.hero_post_url`
- `identity.hero_post_note`
- `identity.recommendation_reason`

**Freshness check.** Look at
`identity.content_pillars_discovered_at` (the canonical anchor — all 6
keys are written together, so checking one is sufficient).

- All 6 core keys present AND anchor timestamp **within 90 days** AND
  `force_refresh` is not set → go to **Step 4 (assemble & return)** with
  `brief_status: "fresh"`.
- Otherwise → **Step 2** (active fetch).

### Step 2 — Active fetch (multi-signal)

Use the browser tools — `browser_navigate`, `browser_snapshot`,
`browser_console`, `browser_get_images`, `vision_analyze`. Do NOT use the
`mcp_chrome_devtools_*` family. The same conservative-pacing rules
documented in [instagram-kol-discovery] apply (random 2-4s between
profiles, 1-2s between Reels, no follow/like/comment, stop on
checkpoint).

Page-load budget: at most **5 page loads** per invocation (profile + up
to 3 Reels + 1 retry). If a load fails, do not retry the same URL more
than twice.

#### Signal A — Profile bio + Reel list
1. `get-identity` already gave you `primary_handle` and (usually)
   `identity.instagram_profile_url`. Pull the URL from `get-facts`
   results; if missing, construct
   `https://www.instagram.com/<primary_handle>/` as a fallback.
2. `browser_navigate(profile_url, timeout=150)`.
3. `browser_console(expression=
   "document.querySelector('header section') ? document.querySelector('header section').innerText : ''")`
   to extract the bio block. Trim to a reasonable length.
4. `browser_console(...)` to enumerate the **first 9 Reel links** on the
   page (`a[href*='/reel/']` or `a[href*='/p/']`). Capture their URLs.

#### Signal B — Hero-Reel captions
1. Rank candidate Reels by available signal: when
   `veedcrawl_metadata(url=...)` is in your toolset, call it for each
   collected Reel URL and pick the **top 3 by view count**. When
   `veedcrawl_metadata` is NOT available, pick the **first 3 Reels** the
   profile surfaces (they are the most recent and usually load first).
2. For each of the 3 hero Reels: `browser_navigate(reel_url)` and
   `browser_console(...)` to extract the caption text + any visible
   hashtags.

#### Signal C — Cover overlay text (caption is sparse)
For each Reel where the caption is < 30 characters or the theme is not
self-evident from the caption alone:
1. `browser_get_images(...)` to grab the Reel cover thumbnail.
2. `vision_analyze(image, prompt="Extract any text overlay visible on
   this Instagram Reel cover. Reply with the raw text lines, or 'NONE'.")`

Creators very often print the video's theme or selling point directly
on the cover (e.g. "POV: hosting in a 600 sq ft apartment"), so this
signal often disambiguates a thin caption.

#### Signal D — Comments (theme still unclear)
For each hero Reel whose theme is still ambiguous after A+B+C:
1. Already on the Reel page from Step B. Use
   `browser_console(expression=
   "Array.from(document.querySelectorAll('ul ul span')).slice(0, 20).map(n => n.innerText).join('\\n')")`
   (or the closest equivalent your snapshot reveals) to extract up to
   **20 comments visible in the first viewport**.
2. **Do NOT scroll, do NOT click "View replies", do NOT load more.**
   First-viewport top comments are enough — they're the highest-signal
   audience reactions and cost zero extra page loads.

This is the most important signal for `voice_descriptors` and
`signature_hooks` — what viewers repeatedly say about the creator beats
the creator's self-description.

### Step 3 — Distill + persist

Concatenate the captured signals into a structured prompt block:

```
PROFILE BIO:
<bio text or "(empty)">

HERO REELS (up to 3):
[1] URL: <reel_url>
    Caption: <caption text>
    Cover overlay (if extracted): <vision text>
    Top comments (up to 20): <comments joined with newline>
[2] ...
[3] ...
```

Pass it to a cheap LLM (e.g. Haiku) with this output contract:

> From the material above, produce a strict JSON object with these keys:
> - `content_pillars`: array of 2-4 short noun phrases (e.g. "cozy hosting",
>   "slow-morning routine"). Drawn from recurring themes in captions + bio.
> - `signature_hooks`: array of 2-3 phrases describing the structural pattern
>   of the Reels (e.g. "before/after walk-through", "POV diary", "honest
>   unboxing"). Inferred from the Reel themes, not from the bio.
> - `voice_descriptors`: array of 2-3 single-word tone adjectives. **Prefer
>   adjectives that appear repeatedly in the comments** ("cozy", "honest",
>   "dry", "warm") over the creator's self-description.
> - `hero_post_url`: the single Reel URL with the strongest theme match
>   for a home / lifestyle / family-warmth campaign (or the highest-engagement
>   one if none clearly leads). Must be one of the URLs in the input.
> - `hero_post_note`: 1 sentence describing why this Reel is representative
>   (e.g. "moving-into-new-house comfort tour, viewers repeatedly called it
>   'so cozy'").
> - `recommendation_reason`: 1 sentence campaign-fit explanation — why this
>   creator's content angle matches our product. Be concrete; do NOT use
>   generic praise like "great content" or "high engagement".
>
> Output the JSON object only. No prose, no markdown fences.

If the LLM call succeeds and returns valid JSON, persist the 6 keys via
**one** `write-facts-multi` call at identity scope (so the brief is
reusable across campaigns):

```
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py write-facts-multi \
  --identity-id <identity_id> --env <TEST|LIVE> \
  --json '{"campaign_id": <campaign_id_or_null>,
            "source": "skill:kol-creator-brief-loader",
            "namespaces": {
              "identity": {
                "identity.content_pillars":          [...],
                "identity.content_pillars_source":   "ig_profile_reels_comments",
                "identity.content_pillars_discovered_at":  "<iso8601 now>",
                "identity.content_pillars_discovered_url": "<profile_url>",
                "identity.signature_hooks":          [...],
                "identity.signature_hooks_source":   "ig_profile_reels_comments",
                "identity.signature_hooks_discovered_at":  "<iso8601 now>",
                "identity.signature_hooks_discovered_url": "<profile_url>",
                "identity.voice_descriptors":        [...],
                "identity.voice_descriptors_source": "ig_profile_reels_comments",
                "identity.voice_descriptors_discovered_at":  "<iso8601 now>",
                "identity.voice_descriptors_discovered_url": "<profile_url>",
                "identity.hero_post_url":            "<reel_url>",
                "identity.hero_post_url_source":     "ig_reel_pick",
                "identity.hero_post_url_discovered_at":  "<iso8601 now>",
                "identity.hero_post_url_discovered_url": "<profile_url>",
                "identity.hero_post_note":           "<note>",
                "identity.hero_post_note_source":    "llm_summary",
                "identity.hero_post_note_discovered_at":  "<iso8601 now>",
                "identity.hero_post_note_discovered_url": "<hero_reel_url>",
                "identity.recommendation_reason":          "<reason>",
                "identity.recommendation_reason_source":   "llm_summary",
                "identity.recommendation_reason_discovered_at":  "<iso8601 now>",
                "identity.recommendation_reason_discovered_url": "<profile_url>"
              }
            }}'
```

`campaign_id: null` keeps these as reusable identity facts. If
`campaign_id` was passed into the skill, you MAY pass it through so the
provenance event carries the trigger context, but the keys themselves
remain identity-scoped.

`brief_status: "refreshed"` on the returned envelope.

### Step 4 — Assemble and return

Return a single string (markdown block) that the caller pastes into the
prompt as the **P0.1** section, immediately after the
`kol-email-style-loader` block.

```
### [P0.1] Creator brief (use to personalize — MUST cite at least one detail)
- Handle: @<primary_handle>
- Content pillars: <pillar1> · <pillar2> · <pillar3>
- Signature hooks: <hook1> · <hook2>
- Voice: <descriptor1>, <descriptor2>
- Hero post: <url> — <hero_post_note>
- Why we picked them: <recommendation_reason>
- Brief status: <fresh|refreshed|unavailable>
```

Format rules:
- `Content pillars` / `Signature hooks` are list values from the facts;
  join with " · ".
- `Voice` joins with ", ".
- `Hero post` shows the URL + the note (the URL is the canonical
  reference; the note tells the LLM what the post is about so it can
  paraphrase the theme).
- `Brief status` reflects how the brief was sourced:
  - `fresh` — passive path hit (facts within 90 days).
  - `refreshed` — active fetch succeeded and wrote new facts.
  - `unavailable` — Step 2 failed entirely.

### Failure mode — `brief_status: "unavailable"`

When the active fetch fails completely (browser blocked / checkpoint /
LLM returned non-JSON / all signal sources empty), return the brief
block with placeholder fields and `Brief status: unavailable`:

```
### [P0.1] Creator brief (use to personalize — MUST cite at least one detail)
- Handle: @<primary_handle>
- Content pillars: (unavailable)
- Signature hooks: (unavailable)
- Voice: (unavailable)
- Hero post: (unavailable)
- Why we picked them: (unavailable)
- Brief status: unavailable
```

The downstream drafter (cold-outreach / reengagement) is expected to
detect this state and emit `low_personalization: true` +
`low_personalization_reason: "creator_brief_unavailable"` in its draft
envelope so the operator review surface flags it. **Never** raise an
exception or block the drafter.

## Examples

### Fresh — passive path
`@cozyhome_emma`, identity already has 6 brief facts written 12 days ago
by `instagram-kol-discovery`. `get-facts` returns them. Skip Steps 2-3.
Assemble the markdown block with `Brief status: fresh`. Zero browser
calls, zero LLM calls.

### Refreshed — active fetch
`@beckiowens`, no brief facts on file (legacy identity from before this
skill existed). Step 2 navigates the profile, picks 3 hero Reels, pulls
captions + cover overlay text + first-viewport comments. Step 3 LLM
distills:
- pillars: ["interior styling", "family hosting", "honest reviews"]
- hooks: ["before/after walk-through", "house-tour vlog"]
- voice: ["warm", "candid"]
- hero_post: a 412k-view comfort-tour Reel
- recommendation_reason: "her hosting tours match the family-warmth angle
  we want for the sofa"
`write-facts-multi` writes all 24 fact entries (6 keys × 4 provenance
fields each). Return block with `Brief status: refreshed`.

### Unavailable — IG checkpoint
`@silentkol`, profile page returns IG's "suspicious login attempt"
interstitial. Skill stops the active fetch, returns the placeholder
block with `Brief status: unavailable`. Cold-outreach sees this, drafts
a generic opening, and sets `low_personalization: true` in the envelope.
Operator sees the flag in their review queue.

### Stale refresh
`@oldfriend`, brief facts written 137 days ago. Passive freshness check
fails (> 90 days). Active fetch runs, new LLM-distilled facts overwrite
the old ones (write_facts append-only history is preserved — the new
values just become the latest). `Brief status: refreshed`.

## Pitfalls
- Do NOT call `veedcrawl_extract` (paid). Only `veedcrawl_metadata`
  (free, for view/ER) is allowed.
- Do NOT scroll the comments section or expand "View replies" — first
  viewport only. Each scroll costs a page interaction and IG penalizes
  rapid scrolling.
- Do NOT overwrite a fresh brief just because `force_refresh` is unset.
  The 90-day threshold is a guard against unnecessary browser cost.
- Do NOT raise on bridge / LLM / browser errors. The contract is "best
  effort"; a missing brief is an envelope flag, not a hard failure.
- Do NOT write campaign-scoped facts. The brief is reusable across
  campaigns (`campaign_id: null` in the write payload).
- Do NOT call `cal.py` / direct SQL / `execute_code`. Use the bridge
  CLI for all reads and writes.
- Do NOT mix the brief content into the email body verbatim — that's
  the drafter's job. This skill ONLY returns the markdown block; the
  drafter paraphrases the relevant details into the opening line.
