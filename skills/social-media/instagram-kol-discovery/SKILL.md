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

**Showcase principle**: Audience-buyer fit alone is not enough. The creator must also be *able to credibly showcase this specific product on camera*, judged from their past video work — production quality, on-camera presentation skill, and whether their content format and scenes can naturally hold a real product placement for this category. A high-buyer-fit creator who has never shown a piece of furniture in a usable on-camera way is a weak choice.

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
- Past successful collaborator references, if the user provides them (creator URLs, campaign links, whitelist examples)
- What likely drove conversion in those references (audience overlap, tone, product-integration pattern, CTA style, room scene, demo pattern)

Then derive the single **Primary Purchase Driver** (see next section) and 1-2 **Secondary Drivers**.

If no brief is provided, run **Brief Fallback**: infer a provisional persona from product category + visible claims, mark all inferred fields as `assumption`, and disclose them in the final output.

**Default historical rule**: Use **Embedded Historical Search Experience** below by default. User-supplied newer winners only override it when they are clearly closer to the current product family and show repeat commercial strength, not just aesthetic similarity.

## Product-Driver Routing
Classify the product into ONE primary driver below. The driver — not the product name — determines KOL strategy. Same product (e.g. a sofa) can route differently depending on the brief.

| Driver | Typical products | Typical claims | KOL archetypes to prioritize |
|---|---|---|---|
| **A. Emotion / Aesthetic** | sofa, bed, accent chair, coffee table, decorative storage | cozy, beautiful, cloud-like comfort, family warmth, aesthetic living | home decor, cozy lifestyle, interior styling, soft-aesthetic family home, day-in-the-life / personality-led lifestyle |
| **B. Family Life / Practical** | dining table, sectional, kid-friendly furniture, family living-room sets | family gathering, hosting, kid-proof, daily-use durability, big household | mom creators, family lifestyle, homeowner creators, practical-home creators, couple/family humor creators |
| **C. Function / Storage** | cabinets, sideboards, entryway furniture, TV stands (storage-led), shoe cabinets | organization, hidden storage, layout, cable management, space efficiency | organization creators, home renovation, practical setup, DIY/homeowner, productivity / hacks creators |
| **D. Device / Specialized Use** | media console, AV-friendly TV stand, vinyl cabinet, gaming/office hybrids | depth/ventilation, equipment compatibility, cable flow, signal/audio fit | home theater / setup creators, dad-homeowner, DIY makers, gaming/vinyl/tech-lifestyle, explainer/demo creators |
| **E. Design Authority** | designer collections, statement pieces, premium/high-style furniture | design language, premium materials, elevated taste, visual styling | interior designers, design-forward creators, premium home stylists, fashion/luxury taste-makers |

Always record the chosen driver and a 1-line justification. Do not pick more than one primary driver. If two drivers genuinely tie, pick the one closest to the buyer's *purchase intent*, not the one closest to the product's *appearance*.

Important: the **same product can route to different drivers based on the story being sold**. A sofa can route to Driver A when sold as cozy aesthetics, to Driver B when sold as family hosting, or to Driver D when sold as a home-theater / gaming / room-setup upgrade.

## Creator-Type Scope
There is **no built-in restriction to home/decor creators**. Creator vertical is a clue, not a gate. Eligible creators may come from home/family, tech/setup, gaming, DIY/maker, productivity, lifestyle, fashion/luxury, comedy/entertainment, or mixed creator worlds if they meet all three tests:

1. Audience purchase intent matches.
2. The product has a believable place in their content world.
3. They can showcase the product credibly on camera.

Typical cross-vertical cases: media console -> tech/setup/gaming; dining table or sectional -> family or couple-lifestyle; statement piece -> fashion/luxury taste-maker. Reject only if buyer-intent fit, product-context fit, or Showcase Capability fails.

## Conversion-Mechanism Patterns
Use these proven mechanisms as default heuristics:

- **Milestone lifestyle** — first apartment, new home, moving in together, newlywed, "finally feels like home"
- **Daily-use comfort** — movie nights, lounging, hosting, everyday convenience
- **Feature demo** — modularity, remote, sofa-bed, built-in lights, storage, cable flow, assembly/material proof
- **Specialized setup** — gaming, home theater, media wall, vinyl, creator desk, AV setup
- **Setup completion** — the final piece that completes a room or system; strong for setup/gaming/minimalist-tech
- **Relatable personality** — humor, couple/family dynamics, creator POV

Search adjacent creators by mechanism, not niche label.

## Embedded Historical Search Experience
These priors are distilled from roughly **66 deduped campaigns / 205 raw rows** and should be treated as built-in search experience.

| Product family | Proven creator types / benchmarks | What repeatedly won | Default search priority |
|---|---|---|---|
| **SEB8008** sofa / sofa bed / electric sofa | `kathypicos`, `kennellymichelle`, `sofyaplotnikova`, `bebekolog_`, `haikettua_atl`, `deanwethers`, `starabelar` | comfort + movie-night, moving/newlywed, lifestyle-upgrade demo, honest reassurance, home-theater/setup, culture hook | milestone lifestyle -> comfort/family -> honest-review home -> setup/home-theater -> entertainment/culture |
| **TS8279** media console / TV console | `make.one.studio` | setup/home-theater authority, device compatibility, room-upgrade logic | setup, gaming, home theater, desk/setup, maker before decor |
| **DT8168** dining table | `kubrayasun` | dining-space completion, hosting/family meals, statement-piece + fully assembled convenience | dining setup, hosting, family meals, polished everyday home-life |
| **SF8220** family-friendly sofa / practical seating | `evalunalife` | mom-approved comfort, kid/pet practicality, movies/reading, real-use proof | moms, family-home, kid/pet practical, real-use comfort before pure design |
| **SSF8030** recliner / accent chair | `lifelybyrosa` | comfort-first, modern look, value reassurance, material/price questions | comfort-first, modern-look, material/color reassurance, realistic value |
| **SSF0005** electric chair / recliner | `ugc.aylinkenan` | ergonomic comfort, leather quality, one-touch recline, purchase reassurance | close-up material detail, ergonomic demo, reassurance-led creators |
| **TS8136** room-upgrade / detail-led living-room furniture | `amerikada_hayattt`, `sydneywinbush` | home details, walnut/minimalist styling, fully assembled ease, moving-in series | room-upgrade diaries, moving-in creators, home-details lifestyle |

**Hook priors vs commerce priors**: creators such as `dilamiraco`, `theozspace`, `daisy.diarys`, and `miausalife` are useful top-of-funnel or expansion anchors, but repeated commerce winners outrank one-week hook spikes for default benchmarking.

## Persona Inference Framework
Build the buyer persona from the extracted Campaign Context — never from a built-in default. The persona has 5 dimensions; each is used later as a scoring axis.

1. **Demographic fit** — age, gender skew, family status, homeowner vs renter
2. **Need-state fit** — comfort, storage, design, device compatibility, kid/pet friendliness, hosting/entertaining, sleep quality, etc.
3. **Space-context fit** — living room, bedroom, dining area, media room, small apartment, suburban home, etc.
4. **Purchase-stage fit** — first apartment, first home, home upgrade, remodel/renovation, specialty room setup
5. **Content-native fit** — does the creator naturally post in scenes where this furniture would actually be used?

Persona is a *target* used to evaluate creators; it is never used to pre-filter creators by surface niche alone.

## Showcase Capability Framework
Independent of audience match, evaluate whether the creator can actually present *this product* well on camera. Score each dimension 0–10 based on the most recent 10–15 Reels.

1. **Visual production quality** — lighting, framing, stability, color, resolution.
2. **On-camera demo skill** — voiceover, explanation, ability to show mechanisms or use cases.
3. **Scene fit** — does the creator actually film in the kind of room where this product belongs?
4. **Large-object track record** — prior furniture / room-makeover / AV / organization placements.
5. **Format fit** — room tour, before/after, day-in-the-life, demo, styling, unboxing, etc.
6. **Branded-content execution** — natural integration, product focus, clear CTA.

Derive a **Showcase Score** (0–100) = weighted average × 10. Tiers:
- **Strong showcase**: ≥ 70
- **Workable showcase**: 50–69 (acceptable, may need brand-side creative direction)
- **Weak showcase**: < 50 (auto-discard regardless of audience match)

A creator must clear the Showcase tier even if the Audience Match Score is excellent. High audience match + weak showcase = wrong choice for a furniture promo Reel.

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

### Final Ranking — combine Audience Match × Showcase Capability
Ranking is **NOT** Audience Match alone. Combine the two scores:

```
Final Fit = 0.6 × Match Score + 0.4 × Showcase Score
```

Default 60/40 weighting. For Driver D (Device / Specialized Use) and Driver E (Design Authority) where production quality and demo capability matter more, shift to **50/50**. Disclose the chosen weighting in the output.

A creator is only eligible for the shortlist if **Match Score ≥ 70 AND Showcase Score ≥ 50**. No score-trading: a brilliant showcase cannot rescue weak audience match, and a perfect audience match cannot rescue weak showcase.

## Creator Role Mapping
For each campaign, name 1–3 **Creator Roles** to recruit. Do not chase only one archetype.

- **Conversion role** — closest to the real buyer, highest sales-conversion likelihood
- **Authority role** — credibility / taste / technical endorsement
- **Lifestyle role** — embeds the product in believable daily scenes
- **Niche use-case role** — pets / kids / gaming / home theater / hosting / small-space, etc.
- **Showcase role** — proven on-camera demonstrator: room tours, before/after, furniture/large-object placements done well; primary value is making the product look credible on screen
- **Narrative / entertainment role** — humor, storytelling, or personality-led format that can place the product inside a memorable scene without losing product clarity

Role mix examples (adjust per brief):
- Sofa (Driver A) → Conversion + Lifestyle, Authority secondary
- Dining table (Driver B) → Lifestyle + Conversion (hosting/family meals); Narrative / Entertainment if family or couple humor mirrors real meal/hosting moments
- Electric sofa framed as a home-theater / gaming-room upgrade (Driver D) → Authority + Showcase + Niche use-case
- TV stand / media console (Driver D) → Conversion + Niche use-case + Authority
- Designer cabinet (Driver E) → Authority + Lifestyle
- Storage cabinet (Driver C) → Conversion + Niche use-case (organization)

The final shortlist must cover the chosen roles, not duplicate the same archetype.
A creator from tech, entertainment, or another non-home vertical is fully acceptable if they fill one of the chosen roles better than a home-niche creator.

If reference creators are provided, map each chosen role back to a proven conversion mechanism. Example: a couple-lifestyle creator may fill Lifestyle + Narrative roles for a sofa because they naturally sell "nest-building" and hosting; a gaming/setup creator may fill Authority + Showcase for a media console because they naturally sell compatibility and room integration.

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
| 10 | Showcase capability | ≥ 50 (Workable or Strong tier) per the Showcase Capability Framework | Visual review of last 10–15 Reels: production quality, on-camera presence, scene fit for *this* product, prior furniture/large-object placement track record, format fit, branded-content execution if any. Weak (< 50) auto-discard. |

When mining commenters as candidates, only enqueue accounts already showing **≥ 100k followers** on the hover/profile preview.

## Discovery Channels

### Channel A — Hashtag search (seed discovery)
Generate **8–12 seed hashtags dynamically** from: product category, room/use context, buyer persona, the Primary Purchase Driver, and the nearest matching product family in **Embedded Historical Search Experience**. Mix product terms, room/scene terms, persona terms, audience-intent terms, and creator-format / subculture terms. Avoid popular-but-irrelevant tags. Where helpful, include NA-locality cues (e.g. `#usinteriors`, `#canadianhome`). Do **not** limit seeds to home hashtags when the historical commerce winner for that family lives in another creator world such as tech/setup, gaming, family humor, or personality-led lifestyle.

Illustrative mapping only: sofa -> `#cozyhome` `#livingroomdecor` `#familyroom`; dining -> `#diningroomdecor` `#hostingathome` `#familydinner`; storage -> `#homeorganization` `#entrywayideas` `#storagehacks`; media console -> `#mediaconsole` `#tvstand` `#hometheater` `#livingroomsetup`; designer -> `#interiordesigner` `#designforward` `#statementfurniture`.

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

### Channel F — Reference-creator expansion (user-supplied winners / benchmarks)
When the user supplies past successful collaborators or benchmark creators:
1. Review 5–10 recent Reels and summarize what likely made them convert: tone, format, scene type, product integration, CTA style, and audience cues.
2. Use their Following / Similar accounts / high-signal commenters as discovery surfaces, even if those creators are outside the home vertical.
3. Search for adjacent creators who share the **conversion mechanism**, not just the same niche label.
4. Treat reference creators as calibration anchors, not fixed templates. Copy the causal pattern, not the superficial aesthetic.

### Expansion depth
Lateral expansion (B + C) is allowed up to **3 hops** from any seed hashtag result. Track hop count per candidate; stop expanding from a node at hop 3.

## Search Persistence And Stop Conditions
- **Do not stop at the first acceptable candidate.** Continue searching, qualifying, and comparing creators until a defensible **Best Overall KOL** can be named (or all surfaces are exhausted).
- Treat discovery as a prioritized queue, not a one-shot pass. If one channel fails, switch to the next viable channel and keep the queue moving.
- **Minimum evidence**: review at least **3 candidates with Audience Match = High** when the surface allows. If fewer than 3 are reachable, continue until surfaces are exhausted, then explain why.
- **Minimum surface coverage**: sample at least **2 distinct discovery surfaces** before naming a winner, unless blocked.
- **Role coverage**: the final shortlist must cover the chosen Creator Roles, including Showcase and Narrative / Entertainment when they are part of the campaign mix, not duplicate one archetype.
- Best Overall KOL requires: passes all hard criteria, Audience Match = High, Showcase Capability clears threshold, real Reel-performance sample measured, ranks first under the combined Match + Showcase ranking, and beats the runner-up on more than one dimension. If the lead is marginal, keep searching.
- Single-step failures (one hashtag / one browser session / one extraction call) never end the run — switch channel or seed and continue.
- Stop only when (a) a defensible Best Overall KOL has been identified after enough comparisons, or (b) all relevant channels are exhausted/blocked and the blocker is documented.
- If no clear winner exists, return **"No best-fit KOL identified yet"** with the blocking reason.

## Workflow

Track three counters across the run: total reviewed candidates, reviewed High-match candidates, distinct discovery surfaces sampled. Maintain a prioritized queue from Channels A–F.

1. **Interpret** — Extract Campaign Context, choose Drivers, weights, Roles, and the closest family in **Embedded Historical Search Experience**. If no brief, run Brief Fallback.
2. **Seed** — Start from the proven creator worlds and mechanisms in the matched family, then generate dynamic hashtags/public-search queries. Run Channels A, D, and F when useful.
3. **Capture and qualify** — Open target posts, capture canonical URLs with `browser_console(expression="window.location.href")`, close via the in-page × button, then qualify each creator: region -> Reels activity -> product-context relevance -> Match Score -> Showcase Score.
4. **Measure performance** — On qualified profiles, sample 10–15 recent Reels, exclude the last 72 hours, run `veedcrawl_metadata`, and compute average views plus engagement rate. Prefer `avg_views ≥ 30,000` and `avg_engagement ≥ 3%`; borderline cases are flagged, not auto-rejected.
5. **Expand and finalize** — Use Channels B, C, and E for lateral expansion until stop conditions are met. Use screenshots always, `veedcrawl_metadata` for qualified KOLs, and `veedcrawl_extract` only on explicit request.

## Deliver Results
Return results only after the persistence criteria are satisfied. Start with a one-line verdict naming the **Best Overall KOL** (or an explicit blocker statement). Then return:

| Username | Profile URL | Followers | Avg Views | Engagement Rate | Region | Match Score | Showcase Score | Final Fit | Best Role | Buyer-Intent Fit | Showcase Evidence | Prior Competitor Collab | Why this creator fits this product |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|

**Sort priority**: 1) Final Fit (combined) descending 2) Role coverage (ensure each chosen Creator Role — including Showcase role — is represented near the top) 3) Prior competitor collab promoted within same tier 4) Showcase Score descending as final tiebreaker. Include a separate short list of "discarded" candidates with the failing criterion (note whether failure was on Match, Showcase, or both).

Also include:
- **Campaign Context summary**: extracted product category, core claims, persona, Primary + Secondary Purchase Drivers, chosen Creator Roles, applied Audience-Match weights, applied Match/Showcase combination weighting.
- **Historical prior used**: state which product-family prior from **Embedded Historical Search Experience** was used as the main benchmark, and whether the shortlisted creator resembles a historical commerce winner or only a historical hook winner.
- **Optional reference override used** (only if the user supplied newer winners): explain what changed versus the built-in historical priors.
- **Conversion mechanism matched**: for each top creator, state which proven mechanism they match best (e.g. milestone lifestyle, daily-use comfort, feature-demo, specialized setup, relatable narrative) and why that mechanism is likely to convert this product.
- **Why each creator fits this product**: 1–2 sentences tying content + audience to the purchase trigger, not generic aesthetic similarity.
- **Showcase evidence per top creator**: 2–3 concrete Reels/URLs or the closest analog plus residual risk if no direct precedent exists.
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
- ❌ Do NOT shortlist a creator on Audience Match alone — they must also clear the Showcase Capability bar. A creator with the perfect audience but who has never filmed a piece of furniture credibly is the wrong choice for a furniture promo Reel.
- ❌ Do NOT trade Match for Showcase or vice versa: both must independently clear their thresholds (Match ≥ 70 AND Showcase ≥ 50).
- ❌ Do NOT reject a creator solely because their top-line niche is tech, gaming, comedy, entertainment, fashion, or lifestyle. Reject only if buyer-intent fit, product-context fit, or Showcase Capability fails.
- ❌ Do NOT overfit to a past winning creator's surface style. Reuse the underlying conversion mechanism, not superficial traits.
- ⚠️ Browser Use ships a US residential proxy by default — do not disable for Instagram. For stricter bot detection, pass `customProxy` to a dedicated residential proxy.
  - When hashtag pages keep timing out: switch to seed creators discovered via Channel D, then use "Similar accounts" (Channel E).
  - When Instagram fails ≥ 3 times consecutively: pause crawling and either request residential proxies or fall back to Channel D. Direct profile URLs usually load even when search pages are blocked.
- ⚠️ Instagram UI selectors for Reel likes/comments/views change frequently; if JS extraction returns 0 values, fall back to page snapshots.
- ⚠️ Bio region check accepts city / state / flag emoji as valid NA signals. If the bio has no geographic signal, treat region as unknown and discard.
- ⚠️ Engagement rate uses `(likes + comments) / views`, NOT `/ followers`. Keep the formula consistent across all candidates.