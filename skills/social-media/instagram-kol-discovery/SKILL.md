---
name: instagram-kol-discovery
description: Generic North America Instagram KOL discovery framework for any furniture product. First interprets the product brief, persona, and research documents to identify the buyer's real purchase driver, then dynamically routes to the right creator archetypes, seed terms, and scoring weights before crawling.
trigger: When user asks to find Instagram KOLs/influencers for any furniture product (sofa, bed, dining, storage, media console, cabinet, designer pieces, etc.), interpret a product brief / persona / research doc, route to the correct purchase-driver category, generate seeds dynamically, and qualify candidates against the framework.
tags: ["instagram", "kol", "influencer", "furniture", "home", "veedcrawl", "cloud browser"]
---

## Goal
Find qualified **North American (US / CA) Instagram creators** for sponsored furniture Reels. The target is not "home creators" by default; first infer **why this furniture is bought**, then find creators whose audience, content world, and video skill can convert that purchase motive.

Hard defaults:
- **Reels-first only**: static-only accounts are out of scope.
- **Personal bloggers preferred**: exclude agencies, media pages, brand accounts, and direct competitors.
- **NA is mandatory**: creator location and audience signals must point to US / Canada.
- **Buyer fit + showcase fit are both required**: a creator must reach the right buyer and credibly show this product on camera.
- **No preset persona or seed list**: derive the persona, driver, seeds, and weights from the brief plus the built-in historical priors below.

## Step 0 — Interpret the Brief
Before browsing, extract a compact **Campaign Context** from user input, product docs, research, or visible product claims:

- **Product**: category, key features, materials/mechanisms/tech, price tier.
- **Buyer**: likely age/life stage, household, home status, pain points, competitive alternatives.
- **Purchase driver**: one Primary Driver and 1-2 Secondary Drivers from the routing table.
- **Scene**: room/use case, content angle, why the product belongs there.
- **References**: user-supplied winners/benchmarks and the likely conversion mechanism behind them.
- **Assumptions**: mark missing fields as `inferred` and disclose them later.

If no brief exists, infer a provisional persona from product category + visible claims. Use **Embedded Historical Search Experience** by default; newer user-supplied winners override it only when closer to the current product family and repeatedly commercial, not just visually similar.

## Driver And Historical Calibration
Pick **one Primary Purchase Driver**. If two drivers tie, choose the one closest to buyer intent, not product appearance. The same sofa may route to A for cozy aesthetics, B for family hosting, or D for home-theater/gaming setup.

| Driver | Bought for | Creator worlds to test |
|---|---|---|
| **A. Emotion / Aesthetic** | cozy, beautiful, premium, family warmth, design statement | decor, cozy lifestyle, interior styling, day-in-life, personality-led lifestyle |
| **B. Family Life / Practical** | hosting, kid/pet durability, big household, daily use | moms, family/couple lifestyle, homeowner, practical-home, family humor |
| **C. Function / Storage** | organization, hidden storage, layout, cable/space efficiency | organization, renovation, DIY, productivity/hacks, practical setup |
| **D. Device / Specialized Use** | AV fit, ventilation, cable flow, gaming/vinyl/office compatibility | home theater, setup, gaming, tech-lifestyle, makers, explainers |
| **E. Design Authority** | materials, taste, elevated design, statement value | designers, premium stylists, design-forward creators, fashion/luxury taste-makers |

Historical priors, distilled from roughly **66 deduped campaigns / 205 raw rows**:

| Family | Benchmarks | Winning mechanism | Search priority |
|---|---|---|---|
| **SEB8008** sofa / sofa bed / electric sofa | `kathypicos`, `kennellymichelle`, `sofyaplotnikova`, `bebekolog_`, `haikettua_atl`, `deanwethers`, `starabelar` | comfort/movie-night, moving/newlywed, honest reassurance, setup/home-theater, culture hook | milestone -> comfort/family -> honest-review home -> setup/entertainment |
| **TS8279** media console / TV console | `make.one.studio` | setup authority, device compatibility, room upgrade | setup, gaming, home theater, desk/setup, maker before decor |
| **DT8168** dining table | `kubrayasun` | dining completion, hosting/family meals, statement + assembled ease | hosting, family meals, polished everyday home-life |
| **SF8220** family sofa | `evalunalife` | mom-approved comfort, kid/pet practicality, real-use proof | moms, family-home, kid/pet practical, comfort before pure design |
| **SSF8030** recliner / accent chair | `lifelybyrosa` | comfort, modern look, value/material reassurance | comfort-first, modern-look, realistic value |
| **SSF0005** electric chair / recliner | `ugc.aylinkenan` | ergonomic demo, leather quality, one-touch recline | close-up material, ergonomic demo, reassurance-led creators |
| **TS8136** room-upgrade furniture | `amerikada_hayattt`, `sydneywinbush` | home details, walnut/minimal styling, assembled ease, moving-in series | room-upgrade diaries, moving-in creators, home-details lifestyle |

Use hook priors (`dilamiraco`, `theozspace`, `daisy.diarys`, `miausalife`) for top-of-funnel expansion only; repeated commerce winners outrank one-week hook spikes.

## Creator Scope And Mechanisms
Creator vertical is a clue, not a gate. Home/family, tech/setup, gaming, DIY/maker, productivity, lifestyle, fashion/luxury, comedy/entertainment, and mixed creators are eligible when all pass:

1. Audience purchase intent matches.
2. The product has a believable role in their content world.
3. Their Reels can showcase this product credibly.

Search by **conversion mechanism**, not niche label: milestone lifestyle, daily-use comfort, feature demo, specialized setup, setup completion, relatable personality/humor.

## Scoring
Score only after reviewing recent Reels, not from profile niche alone.

**Match Score (0-100)**: demographic fit, need-state fit, space-context fit, purchase-stage fit, content-native fit, performance, authority/professionalism. Weight dynamically by driver: A favors content-native/aesthetic fit; B favors household/practical fit; C/D favor need-state and use-case fit; E favors authority and visual taste. Disclose any weight shift.

**Showcase Score (0-100)**: visual quality, on-camera/demo skill, scene fit, prior furniture/large-object/AV/organization placement, format fit, branded-content execution. Strong ≥ 70, Workable 50-69, Weak < 50.

**Final Fit**:
```
Final Fit = 0.6 × Match Score + 0.4 × Showcase Score
```
Use **50/50** for Driver D or E. Shortlist eligibility requires **Match Score ≥ 70 AND Showcase Score ≥ 50**. No score-trading.

## Roles And Qualification
Choose 1-3 roles per campaign: **Conversion**, **Authority**, **Lifestyle**, **Niche use-case**, **Showcase**, **Narrative / entertainment**. The shortlist must cover the chosen roles instead of duplicating one archetype; non-home creators are valid when they fill a role better.

All candidates must meet:

| Criterion | Threshold |
|---|---|
| Region | US / Canada creator and audience signals; unknown region = discard |
| Followers | ≥ 100k |
| Video activity | ≥ 5 Reels in last 3 months; static-only = discard |
| Product context | Last 10-15 Reels contain believable scenes for this product/driver |
| Avg Reel views | ≥ 30k, excluding Reels posted in last 72h |
| Reel ER | ≥ 3%, using `(likes + comments) / views` |
| Account type | individual personal blogger, not agency/media/brand |
| Competitors | no active exclusive direct competitor deal; past one-off competitor collab is a positive flag |
| Scores | Match ≥ 70 and Showcase ≥ 50 |

## Discovery
Maintain a prioritized queue and cover at least **2 discovery surfaces** unless blocked.

- **Hashtags**: generate 8-12 dynamic seeds from product, room, buyer, driver, and closest historical family. Include non-home subculture terms when relevant. Direct URL: `https://www.instagram.com/explore/search/keyword/?q=%23<tag-name>`.
- **Comment mining**: from qualified top Reels, inspect creator-looking commenters; enqueue only if preview/profile shows ≥ 100k followers.
- **Following / Suggested / Similar**: expand from qualified profiles, applying ≥ 100k and NA checks before enqueueing.
- **Public web fallback**: when Instagram search blocks, use NA-scoped queries and cross-verify profiles.
- **Reference expansion**: if user supplies winners, inspect 5-10 Reels, extract the conversion mechanism, then expand through following/similar/commenters even outside home vertical.

Lateral expansion from seed results is capped at **3 hops**. One failed hashtag, browser session, selector, or extraction call never ends the run; switch surface or seed.

## Persistence And Run
Do not stop at the first acceptable candidate. Continue until a defensible **Best Overall KOL** exists or all relevant surfaces are exhausted.

Minimum evidence when reachable:
- review at least **3 High-Match candidates**;
- sample at least **2 discovery surfaces**;
- measure 10-15 recent Reels per qualified creator;
- use screenshots and run **`veedcrawl_metadata(url=...)` for every qualified KOL's recent Reels** because it is mandatory/free for qualification;
- use `veedcrawl_extract(url=..., prompt=...)` only when the user explicitly requests paid/deep extraction.

Workflow: interpret context -> choose driver/roles/history prior -> seed and enqueue -> capture canonical URLs with `browser_console(expression="window.location.href")` -> qualify region/Reels/context/scores -> measure views + ER -> expand laterally -> rank by Final Fit and role coverage. Close posts via the in-page × button, not `browser_back`.

If no clear winner exists, return **"No best-fit KOL identified yet"** with the blocker.

## Deliver Results
Start with one verdict naming **Best Overall KOL** or the blocker. Then provide:

| Username | Profile URL | Followers | Avg Views | ER | Region | Match | Showcase | Final Fit | Role | Mechanism | Showcase Evidence | Competitor Collab | Why Fit |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|

Sort by: Final Fit desc -> role coverage -> prior competitor collab within tier -> Showcase Score. Also include discarded candidates with failing criterion.

Required summary: Campaign Context, chosen driver/roles/weights, historical prior used, reference override if any, conversion mechanism per top creator, 2-3 Reel/URL showcase evidence points per top creator, assumptions from Brief Fallback, and search coverage (reviewed total, High-match total, surfaces used/blocked).

## Cloud Browser Notes
- Set `BROWSER_USE_API_KEY`; optional `BROWSER_USE_PROFILE_ID` preserves Instagram login state. Browser Use `/browsers` has no `keepAlive`; reuse by not stopping the session.
- Keep default US proxy for Instagram; do not disable it.
- Act when visible, avoid redundant screenshots/snapshots, wait only 1-2 seconds unless needed.
- If hashtag pages time out, switch to public-web seed creators + Similar accounts. If Instagram fails 3 consecutive times, document the blocker and fall back.

## Pitfalls
- Do not default to home/decor creators just because the product is furniture.
- Do not let visual similarity outrank buyer intent for functional, technical, family-practical, or use-case products.
- Do not shortlist on Audience Match alone; Match ≥ 70 and Showcase ≥ 50 must both pass.
- Do not reject tech, gaming, comedy, entertainment, fashion, or lifestyle creators solely by niche.
- Do not overfit historical winners' surface style; reuse the conversion mechanism.
- Do not include Reels posted within the last 72h in averages.
- Do not keep commenters with < 100k followers.
- Do not call `veedcrawl_extract` without explicit request and both `url` + `prompt`.