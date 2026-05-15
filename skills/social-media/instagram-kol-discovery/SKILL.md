---
name: instagram-kol-discovery
description: Generic North America Instagram KOL discovery framework for any furniture product. First interprets the product brief, persona, and research documents to identify the buyer's real purchase driver, then dynamically routes to the right creator archetypes, seed terms, and scoring weights before crawling.
trigger: When user asks to find Instagram KOLs/influencers for any furniture product (sofa, bed, dining, storage, media console, cabinet, designer pieces, etc.), interpret a product brief / persona / research doc, route to the correct purchase-driver category, generate seeds dynamically, and qualify candidates against the framework.
tags: ["instagram", "kol", "influencer", "furniture", "home", "veedcrawl", "cloud browser"]
---

## Goal
Find qualified **North American Instagram creators** whose audience matches the **actual buyer persona for the specific furniture product**, where creator selection logic is determined dynamically from the product brief, user input, or research documentation. The skill must work across the full furniture catalog (sofa, bed, dining, coffee table, sideboard, TV stand / media console, storage cabinets, designer pieces, office furniture, etc.) without hardcoding rules for any single category.

**Business objective**: Identify potential collaborators who can produce sponsored furniture promotional Reels. Video creators (Reels) only — static-only accounts are out of scope. Personal bloggers preferred; organizations / agencies / brand accounts excluded; direct competitors excluded.

**Operating principle**: Understand *why this product is bought* first, then look for creators whose audience matches that purchase driver. Do **not** default to "home decor / interior design" creators just because the product is furniture.

**Fixed market constraint**: The target market is **North America (US / CA)**. Both the creator's location and the bulk of audience signals must be in NA. This is a hard rule and is not relaxed by any product brief.

## Step 0 — Interpret the Brief
Before any browsing, parse all available inputs (user message, product brief, persona doc, research doc, market analysis) and extract a structured **Campaign Context**. Do this even if the input is short.

Extract these fields. If a field is missing, mark it `inferred` and write a short 1-line assumption rather than leaving it blank.

- Product category (e.g. sofa, bed, dining table, TV stand, sideboard, designer cabinet)
- Core product features (materials, dimensions, mechanisms, tech, etc.)
- Emotional value (cozy, calm, premium, family warmth, design statement, …)
- Functional value (storage, comfort, durability, AV compatibility, hosting capacity, …)
- Room / use context (living room, bedroom, dining area, media room, entryway, small apartment, suburban home, …)
- Buyer age range
- Buyer gender skew
- Family / life stage (single, couple, parents, multi-generational)
- Homeownership status (renter, first home, established homeowner, remodeler)
- Income / price sensitivity (mass, mid, premium, designer)
- Top 3 purchase pain points the product solves
- Competitive alternatives buyers consider
- Why buyers choose this product over those alternatives
- Best content angle for promo (real use scene, before/after, hosting, setup walkthrough, design styling, …)

Then derive the single **Primary Purchase Driver** (see next section) and 1-2 **Secondary Drivers**.

If no brief is provided, run **Brief Fallback**:
1. Infer a provisional persona from product category + visible product claims.
2. Mark every inferred field as `assumption`.
3. Disclose all assumptions in the final output under "Assumptions made due to missing brief".

## Product-Driver Routing
Classify the product into ONE primary driver below. The driver — not the product name — determines KOL strategy. Same product (e.g. a sofa) can route differently depending on the brief.

| Driver | Typical products | Typical claims | KOL archetypes to prioritize |
|---|---|---|---|
| **A. Emotion / Aesthetic** | sofa, bed, accent chair, coffee table, decorative storage | cozy, beautiful, cloud-like comfort, family warmth, aesthetic living | home decor, cozy lifestyle, interior styling, soft-aesthetic family home |
| **B. Family Life / Practical** | dining table, sectional, kid-friendly furniture, family living-room sets | family gathering, hosting, kid-proof, daily-use durability, big household | mom creators, family lifestyle, homeowner creators, practical-home creators |
| **C. Function / Storage** | cabinets, sideboards, entryway furniture, TV stands (storage-led), shoe cabinets | organization, hidden storage, layout, cable management, space efficiency | organization creators, home renovation, practical setup, DIY/homeowner |
| **D. Device / Specialized Use** | media console, AV-friendly TV stand, vinyl cabinet, gaming/office hybrids | depth/ventilation, equipment compatibility, cable flow, signal/audio fit | home theater / setup creators, dad-homeowner, DIY makers, gaming/vinyl/tech-lifestyle |
| **E. Design Authority** | designer collections, statement pieces, premium/high-style furniture | design language, premium materials, elevated taste, visual styling | interior designers, design-forward creators, premium home stylists |

Always record the chosen driver and a 1-line justification. Do not pick more than one primary driver. If two drivers genuinely tie, pick the one closest to the buyer's *purchase intent*, not the one closest to the product's *appearance*.

## Persona Inference Framework
Build the buyer persona from the extracted Campaign Context — never from a built-in default. The persona has 5 dimensions; each is used later as a scoring axis.

1. **Demographic fit** — age, gender skew, family status, homeowner vs renter
2. **Need-state fit** — comfort, storage, design, device compatibility, kid/pet friendliness, hosting/entertaining, sleep quality, etc.
3. **Space-context fit** — living room, bedroom, dining area, media room, small apartment, suburban home, etc.
4. **Purchase-stage fit** — first apartment, first home, home upgrade, remodel/renovation, specialty room setup
5. **Content-native fit** — does the creator naturally post in scenes where this furniture would actually be used?

Persona is a *target* used to evaluate creators; it is never used to pre-filter creators by surface niche alone.

## Audience Match Scoring Framework
Score every qualified creator on the 5 persona dimensions plus performance and risk. Weights are **dynamically set** based on the Primary Purchase Driver — they are not fixed.

Default weights per driver (sum = 100):

| Dimension | A. Emotion | B. Family | C. Function | D. Device | E. Design |
|---|---|---|---|---|---|
| Demographic fit | 15 | 20 | 15 | 15 | 10 |
| Need-state fit | 20 | 25 | 30 | 30 | 15 |
| Space-context fit | 20 | 15 | 15 | 20 | 15 |
| Purchase-stage fit | 10 | 15 | 15 | 10 | 10 |
| Content-native fit | 25 | 15 | 15 | 15 | 30 |
| Performance (views + ER) | 5 | 5 | 5 | 5 | 5 |
| Authority / professionalism | 5 | 5 | 5 | 5 | 15 |

If the brief explicitly emphasizes a different priority, adjust weights and disclose the change in the output.

The final **Match Score** (0–100) drives ranking. Audience Match Tier:
- **High**: ≥ 70
- **Medium**: 50–69
- **Low**: < 50 (auto-discard)

## Creator Role Mapping
For each campaign, name 1–3 **Creator Roles** to recruit. Do not chase only one archetype.

- **Conversion role** — closest to the real buyer, highest sales-conversion likelihood
- **Authority role** — credibility / taste / technical endorsement
- **Lifestyle role** — embeds the product in believable daily scenes
- **Niche use-case role** — pets / kids / gaming / home theater / hosting / small-space, etc.

Role mix examples (adjust per brief):
- Sofa (Driver A) → Conversion + Lifestyle, Authority secondary
- Dining table (Driver B) → Lifestyle + Conversion (hosting/family meals)
- TV stand / media console (Driver D) → Conversion + Niche use-case + Authority
- Designer cabinet (Driver E) → Authority + Lifestyle
- Storage cabinet (Driver C) → Conversion + Niche use-case (organization)

The final shortlist must cover the chosen roles, not duplicate the same archetype.

## Anti-Bias Rule (must obey)
Do **not** over-prioritize visual niche similarity when the product's purchase trigger is functional, technical, family-practical, or specialized. When the product is bought primarily for utility, compatibility, organization, or a specific household use case, **rank creator-audience purchase intent above purely aesthetic alignment**. A clean "looks like a home account" is not evidence of buyer match.

## Hard Qualification Criteria (ALL must be met)
| # | Criterion | Threshold | How to verify |
|---|-----------|-----------|---------------|
| 1 | Region / market | **North America (US / CA)** — creator location and primary audience both in NA | Bio must contain a US/CA city, state, country name, or 🇺🇸/🇨🇦 flag emoji. Cross-check with caption language (English), tagged post locations, comment language, and brand/shipping references. If the bio gives no usable geographic signal, treat region as unknown and discard. |
| 2 | Followers | ≥ 100,000 | Read from profile header. |
| 3 | Video activity | ≥ 5 Reels in the last 3 months | Check the Reels tab; static-image-only accounts are excluded regardless of follower count. |
| 4 | Product-context relevance | Recent Reels show scenes where this product can appear naturally for its driver | Visual scan of last 10–15 Reels against the Primary Purchase Driver, not generic "home content". Reject only if there is no believable insertion point for *this* product. |
| 5 | Avg. Reel views | ≥ 30,000 | Average views of the most recent 10–15 Reels, excluding the last 72 hours. |
| 6 | Reel engagement rate | ≥ 3% | `(likes + comments) / views`, averaged across the same 10–15 sample. |
| 7 | Account type | Individual personal blogger | Real person's name in bio; no agency/studio/media/brand language; profile picture shows a person. |
| 8 | Competitor relationship | No active exclusive deal with a directly competing furniture brand | Discard only on evidence of an ongoing exclusive partnership. Past one-off competitor collaborations are a **positive signal** — flag with "⭐ prior competitor collab". |
| 9 | Audience match score | ≥ 70 (High tier) per the dynamic Scoring Framework | See Audience Match Scoring Framework. Medium tier (50–69) only kept with an explicit note; Low tier auto-discarded. |

When mining commenters as candidates, only enqueue accounts already showing **≥ 100k followers** on the hover/profile preview.

## Discovery Channels

### Channel A — Hashtag search (seed discovery)
Generate **8–12 seed hashtags dynamically** from: product category, room/use context, buyer persona, and the Primary Purchase Driver. Mix product terms, room/scene terms, persona terms, and audience-intent terms. Avoid popular-but-irrelevant tags. Where helpful, include NA-locality cues (e.g. `#usinteriors`, `#canadianhome`).

Illustrative seed mappings (examples only, regenerate per brief):
- Driver A — sofa: `#cozyhome` `#livingroomdecor` `#familyroom` `#homeliving` `#sofainspo`
- Driver B — dining table: `#diningroomdecor` `#hostingathome` `#familydinner` `#kitchenanddining`
- Driver C — storage cabinet: `#homeorganization` `#entrywayideas` `#storagehacks` `#smallspacehome`
- Driver D — TV stand / media console: `#mediaconsole` `#tvstand` `#mediaroom` `#hometheater` `#livingroomsetup` `#cablemanagement` `#gameroom` `#vinylsetup`
- Driver E — designer cabinet: `#interiordesigner` `#designforward` `#highdesignhome` `#statementfurniture`

Navigate directly to the hashtag results page (avoids the search-box redirect bug):
```
https://www.instagram.com/explore/search/keyword/?q=%23<tag-name>
```

### Channel B — Comment mining (lateral discovery)
On a qualified KOL's top-performing recent reel:
1. Open the reel and scroll the comments panel.
2. Hover or open creator-looking commenters' profiles in a new context.
3. Enqueue only if profile header shows **≥ 100k followers**.

### Channel C — Following / Suggested graph (lateral discovery)
On a qualified KOL's profile:
1. Open the **Following** list (or "Suggested for you" sidebar).
2. Scan for product-relevant creators matching the inferred persona and driver.
3. Apply ≥ 100k filter and NA region check before enqueuing.

### Channel D — Public web search fallback (proxy-unavailable)
When direct Instagram access is blocked/timing out:
1. Search public curated lists with NA-scoped queries built from the brief. Examples: "top US Instagram creators for media console 2026", "North American family home creators sectional sofa", "Canadian organization influencers entryway storage".
2. Extract usernames from reliable sources (agency roundups, industry blogs, directories).
3. Cross-verify against the same qualification criteria before enqueuing.

### Channel E — Similar accounts fallback (when hashtag search fails)
1. Start with product-relevant creator profiles already discovered (no fixed built-in list).
2. Use Instagram's "Similar accounts" recommendations.
3. Apply ≥ 100k followers and NA region check.
4. Allow up to 5 hops from the initial seed profile.

### Expansion depth
Lateral expansion (B + C) is allowed up to **3 hops** from any seed hashtag result. Track hop count per candidate; stop expanding from a node at hop 3.

## Search Persistence And Stop Conditions
- **Do not stop at the first acceptable candidate.** Continue searching, qualifying, and comparing creators until a defensible **Best Overall KOL** can be named (or all surfaces are exhausted).
- Treat discovery as a prioritized queue, not a one-shot pass. If one channel fails, switch to the next viable channel and keep the queue moving.
- **Minimum evidence**: review at least **3 candidates with Audience Match = High** when the surface allows. If fewer than 3 are reachable, continue until surfaces are exhausted, then explain why.
- **Minimum surface coverage**: sample at least **2 distinct discovery surfaces** before naming a winner, unless blocked.
- **Role coverage**: the final shortlist must cover the chosen Creator Roles (Conversion / Authority / Lifestyle / Niche use-case as applicable), not duplicate one archetype.
- Best Overall KOL requires: passes all hard criteria, Audience Match = High, real Reel-performance sample measured, ranks first under the dynamic Scoring Framework, and beats the runner-up on more than one dimension. If the lead is marginal, keep searching.
- Single-step failures (one hashtag / one browser session / one extraction call) never end the run — switch channel or seed and continue.
- Stop only when (a) a defensible Best Overall KOL has been identified after enough comparisons, or (b) all relevant channels are exhausted/blocked and the blocker is documented.
- If no clear winner exists, return **"No best-fit KOL identified yet"** with the blocking reason.

## Workflow

Track three counters across the run: total reviewed candidates, reviewed High-match candidates, distinct discovery surfaces sampled. Maintain a prioritized queue from Channels A–E.

1. **Interpret the brief (Step 0)** — Extract Campaign Context, choose Primary Purchase Driver and Secondary Drivers, set scoring weights, choose Creator Roles. If no brief, run Brief Fallback and disclose assumptions.
2. **Seed phase** — Generate 8–12 dynamic seed hashtags + public-search queries from the Campaign Context. Run Channel A (or Channel D if proxy unavailable). Collect post/reel URLs of high-performing content.
3. **Extract direct URLs** — For each target post/reel:
   a. Click the post/reel to open the modal.
   b. Run `browser_console(expression="window.location.href")` to capture the canonical URL.
   c. Close the modal using the in-page × button. Use `browser_back` only if no × is present — it often lands on `about:blank` or the IG home feed.
4. **Profile qualification** — For each unique creator:
   a. Open their profile.
   b. Read followers and bio.
   c. **Region gate**: discard immediately if no US/CA signal in bio.
   d. **Reels activity gate**: discard if < 5 Reels in last 3 months.
   e. Take a screenshot of the Reels tab to assess content style and product-context relevance against the Primary Purchase Driver.
   f. Score on the 5 persona dimensions + performance + authority using the dynamic weights → Match Score.
   g. If followers ≥ 100k AND region = NA AND product-context relevance fits AND Match Score ≥ 50 → proceed to step 5. Discard if Match Score < 50.
5. **Performance qualification** — On the qualified profile's Reels tab:
   a. List the 10–15 most recent Reels.
   b. Drop any posted within the last 72 hours.
   c. Call `veedcrawl_metadata(url=<reel_url>)` for each remaining Reel (zero cost). Fall back to page screenshot only when metadata is empty.
   d. Compute `avg_views` and `avg_engagement = mean((likes+comments)/views)`.
   e. Prefer `avg_views ≥ 30,000` AND `avg_engagement ≥ 3%`. Borderline cases are flagged, not discarded.
6. **Lateral expansion** — For each KOL passing step 5, run Channels B and C (3-hop cap, ≥ 100k filter, NA gate). Loop back to step 4 for new candidates. Do not end the run while high-priority queued candidates remain unreviewed.
7. **Content extraction** — Three tiers:
   - **Tier 1 — Screenshot (always, zero cost)**: profile grid + sample Reel pages.
   - **Tier 2 — Metadata (always for qualified KOLs, zero cost)**: `veedcrawl_metadata` for recent Reels.
   - **Tier 3 — Full extraction (only on explicit user request, has cost)**: `veedcrawl_extract(url=..., prompt=...)` — both args required. Only when the user asks for spoken/visual scene mining or product-placement analysis. Never speculative.

## Deliver Results
Return results only after the persistence criteria are satisfied. Start with a one-line verdict naming the **Best Overall KOL** (or an explicit blocker statement). Then return:

| Username | Profile URL | Followers | Avg Views | Engagement Rate | Region | Match Score | Best Role | Buyer-Intent Fit | Prior Competitor Collab | Why this creator fits this product |
|---|---|---|---|---|---|---|---|---|---|---|
| @example | https://instagram.com/example | 245k | 58,400 | 4.2% | Brooklyn, NY 🇺🇸 | 82 | Conversion | High — comments repeatedly ask about TV-mounting, cable management, room setup | ⭐ Yes (@CompetitorBrand, ~6 mo ago) | Real media-room rebuilds with AV gear; audience already shopping AV-friendly furniture |

**Sort priority**: 1) Match Score descending 2) Role coverage (ensure each chosen Creator Role is represented near the top) 3) Prior competitor collab promoted within same tier 4) Engagement rate descending. Include a separate short list of "discarded" candidates with the failing criterion.

Also include:
- **Campaign Context summary**: extracted product category, core claims, persona, Primary + Secondary Purchase Drivers, chosen Creator Roles, applied scoring weights.
- **Why each creator fits this product (not just looks like a home account)**: 1–2 sentences per top creator linking their content/audience to the actual purchase trigger — not generic aesthetic remarks.
- **Assumptions made due to missing brief** (only if Brief Fallback was used): list every `inferred`/`assumption` field.
- **Search coverage summary**: reviewed candidates, reviewed High-match candidates, surfaces actually used, surfaces blocked or exhausted.

## Cloud Browser Operation Principles
- **Session setup (Browser Use)**: Set `BROWSER_USE_API_KEY`. Optionally set `BROWSER_USE_PROFILE_ID` to keep Instagram login state across sessions. The Browser Use `/browsers` endpoint does **not** support `keepAlive`; reuse a session by not stopping it between actions. Default proxy `proxyCountryCode: "us"` provides basic bot-detection evasion — do not disable for Instagram crawls.
- **Act decisively**: issue actions as soon as the target element is visible.
- **Minimal delays**: 1–2 seconds is usually enough; never idle for more than 3–5 seconds without a concrete reason.
- **No redundant checks**: do not re-screenshot or re-snapshot the same state.
- **Fail fast, but do not quit early**: if an element is not found within one retry, skip and continue. Repeated failure on one surface means switch channels or seeds, not stop the overall search.

## Pitfalls
- ❌ Do NOT use `browser_back` as the primary way to leave a post — prefer the in-modal × button.
- ❌ Do NOT include Reels posted within the last 72 hours in the average.
- ❌ Do NOT keep commenters with < 100k followers as candidates.
- ❌ Never call `veedcrawl_extract` without both `url` and `prompt`. It has a cost — only on explicit request.
- ❌ Do NOT expand laterally beyond 3 hops from a seed hashtag.
- ❌ Do NOT default to "home decor / interior design" creators just because the product is furniture — choose archetypes from the Primary Purchase Driver.
- ❌ Do NOT let visual aesthetic similarity outrank buyer-intent match for functional, technical, or use-case-driven products.
- ⚠️ Browser Use ships a US residential proxy by default — do not disable for Instagram. For stricter bot detection, pass `customProxy` to a dedicated residential proxy.
  - When hashtag pages keep timing out: switch to seed creators discovered via Channel D, then use "Similar accounts" (Channel E).
  - When Instagram fails ≥ 3 times consecutively: pause crawling and either request residential proxies or fall back to Channel D. Direct profile URLs usually load even when search pages are blocked.
- ⚠️ Instagram UI selectors for Reel likes/comments/views change frequently; if JS extraction returns 0 values, fall back to page snapshots.
- ⚠️ Bio region check accepts city / state / flag emoji as valid NA signals. If the bio has no geographic signal, treat region as unknown and discard.
- ⚠️ Engagement rate uses `(likes + comments) / views`, NOT `/ followers`. Keep the formula consistent across all candidates.