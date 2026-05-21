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
Do not stop at the first acceptable candidate. Continue until each priority product feature / selling-point group has a defensible creator set, or all relevant surfaces are exhausted.

Minimum evidence when reachable:
- review at least **3 High-Match candidates**;
- sample at least **2 discovery surfaces**;
- measure 10-15 recent Reels per qualified creator;
- run `browser_navigate` to every candidate's profile URL (`https://www.instagram.com/<handle>/`) at least once in this run — this is the hard registration gate the orchestrator skill enforces before allowing `shortlist_ready`;
- use screenshots (`browser_snapshot` / `browser_vision`) and extract numbers via `browser_console(expression="...")` from the rendered page;
- when `veedcrawl_metadata(url=...)` is in your toolset, prefer it for per-Reel facts because it is free; when it is NOT in your toolset (e.g. the active agent profile has not enabled the veedcrawl plugin), fall back to `browser_navigate` on the Reel URL plus `browser_console`/`browser_vision` to read view counts, likes and dates. Do not abort the run because veedcrawl is unavailable;
- use `veedcrawl_extract(url=..., prompt=...)` only when the user explicitly requests paid/deep extraction.

**Anti-fabrication rule (hard).** Every handle you place into the orchestrator's `shortlist_ready` `candidates` array MUST be a handle that you actually visited via `browser_navigate("https://www.instagram.com/<handle>/")` earlier in the same run, with on-page evidence supporting the numbers you write into `audience_fit`, `engagement_quality`, `niche_match`, and `reason`. Generic-sounding placeholders (`home_style_lover`, `minimalist_home`, `cozy_living_xx`, `test_kol_*`) are red flags; if you cannot point to the corresponding `browser_navigate` call, omit the handle. It is better to return fewer real candidates (or invoke the orchestrator's zero-results escape hatch after at least 3 distinct surface visits) than to invent any.

Workflow: interpret context -> split product into 2-4 feature/selling-point groups -> choose driver/roles/history prior per group -> seed and enqueue -> capture canonical URLs with `browser_console(expression="window.location.href")` -> qualify region/Reels/context/scores -> measure views + ER -> expand laterally -> rank by Final Fit and role coverage within each group. Close posts via the in-page × button, not `browser_back`.

If no group has clear recommendations, return **"No best-fit KOL identified yet"** with the blocker. If only some groups are weak, keep the group and mark the evidence gap.

## Deliver Results
Final output must be a **Markdown document** organized by product features / selling points, not only one global leaderboard. Start with a short verdict naming the strongest group and strongest overall creator if clear, then provide 2-4 groups. Each group represents a distinct product value angle such as comfort, family use, storage/function, setup/AV fit, design statement, material reassurance, or moving/new-home lifestyle.

Required structure:

```md
# Instagram KOL Recommendations For [Product]

## Campaign Context
- Product / key selling points:
- Primary + secondary drivers:
- Historical prior used:
- Search coverage:

## Group 1: [Feature / Selling Point]
Why this group matters: [buyer motive + content angle]

| Username | Profile URL | Followers | Avg Views | ER | Region | Creator Type | Match | Showcase | Final Fit | Recommendation Reason |
|---|---|---|---|---|---|---|---|---|---|---|

Recommended creators: 3-5 per group when available. If fewer than 3 pass, explain the blocker.

### Evidence Notes
- Showcase evidence: 2-3 Reel/URL examples or closest analogs for top creators.
- Conversion mechanism: milestone, comfort, feature demo, setup completion, relatable personality, etc.
- Risks / assumptions:
```

For every group, show **3-5 recommended bloggers** where the search surface allows. Include creator data, creator type, and the product-specific recommendation reason. Sort within each group by: Final Fit desc -> role coverage -> prior competitor collab within tier -> Showcase Score. Avoid repeating the same creator across groups unless they clearly serve different selling points.

Also include: discarded candidates with failing criterion, optional reference override if any, assumptions from Brief Fallback, and search coverage (reviewed total, High-match total, surfaces used/blocked).

## Cloud Browser Notes
- Set `BROWSER_USE_API_KEY`; optional `BROWSER_USE_PROFILE_ID` preserves Instagram login state. Browser Use `/browsers` has no `keepAlive`; reuse by not stopping the session.
- Keep default US proxy for Instagram; do not disable it.
- Act when visible, avoid redundant screenshots/snapshots, wait only 1-2 seconds unless needed.
- If hashtag pages time out, switch to public-web seed creators + Similar accounts. If Instagram fails 3 consecutive times, document the blocker and fall back.

### Browser Reliability Rules (learned from agent.log failures)
- **Navigation timeout**: the default 60s is too short for Browser Use free tier on IG / heavy SPA pages. Pass `timeout=150` (or higher) on `browser_navigate` for first-load of `instagram.com/*`, `xiaohongshu.com/*`, or any infinite-scroll feed. Subsequent in-SPA navigation can use the default.
- **Always re-snapshot after navigation**: any `browser_navigate`, `browser_click` that triggers a route change, or page reload **invalidates all `@eXX` refs**. Before the next `browser_click` / `browser_type`, you MUST call `browser_snapshot` and use refs from the new snapshot. Reusing a ref across pages will surface as `Unknown ref: eXX` and waste a tool turn.
- **Stop retrying the same call**: if `browser_navigate` to the same URL fails twice, do NOT issue a third identical call. Switch tactic in this order: (a) change `wait_until` (`domcontentloaded` instead of `load`), (b) try the public/non-login URL variant, (c) `cleanup_browser(task_id)` then retry once to force a fresh cloud session, (d) fall back to `veedcrawl_extract` or public-web search. The runtime emits `same_tool_failure_warning` at count=3 — treat that as a hard stop signal.
- **CDP closed / channel errors**: when you see `CDP response channel closed`, `Could not compute box model`, or `Failed to take screenshot ... CDP response channel closed`, the cloud session is dead. Call `cleanup_browser(task_id)` once, then re-issue the navigate; the next tool call will auto-create a new Browser Use session. Do not keep clicking on the dead session.
- **Element-not-found loops**: `Could not locate element with role=...` usually means the page hasn't finished rendering or the element is inside a virtualized list. Resolve by: snapshot → scroll once → snapshot again, instead of retrying the same click.
- **Cloud 400 / provider failure**: `BrowserUseProvider failed (... 400 Invalid HTTP request ...)` is a transient upstream issue; the runtime auto-falls-back to local Chromium. Don't change strategy — continue with the same plan; just note `fallback_from_cloud=true` in the run report.

## Pitfalls
- Do not default to home/decor creators just because the product is furniture.
- Do not let visual similarity outrank buyer intent for functional, technical, family-practical, or use-case products.
- Do not shortlist on Audience Match alone; Match ≥ 70 and Showcase ≥ 50 must both pass.
- Do not reject tech, gaming, comedy, entertainment, fashion, or lifestyle creators solely by niche.
- Do not overfit historical winners' surface style; reuse the conversion mechanism.
- Do not include Reels posted within the last 72h in averages.
- Do not keep commenters with < 100k followers.
- Do not call `veedcrawl_extract` without explicit request and both `url` + `prompt`.