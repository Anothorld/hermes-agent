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
- **Personal bloggers preferred**: exclude agencies, media pages, and creators who self-sell furniture (own brand, DTC store, dropshipping, or persistent furniture storefront). Creators who self-sell non-furniture products (fashion, beauty, food, kitchenware, decor accessories, tech) are fine — that's commerce fluency, not competition.
- **NA is mandatory**: creator location and audience signals must point to US / Canada.
- **Buyer fit + showcase fit are both required**: a creator must reach the right buyer and credibly show this product on camera.
- **No preset persona or seed list**: derive the persona, driver, seeds, and weights from the brief plus the built-in historical priors below.

## Step 0 — Interpret the Brief
Before browsing, extract a compact **Campaign Context** from user input, product docs, research, or visible product claims:

- **Browser Mode**: `local-chrome` (default, CDP-attached local Chrome) or `cloud` (Browser Use). Read from the brief's `browser_mode:` field (NOT the `mode:` field — that carries env `LIVE`/`TEST`), then user message, then default to `local-chrome`. In `cloud` mode the runtime auto-falls-back to CDP local Chrome (auto-starting the debug profile if needed) when Browser Use returns 5xx; a hard failure is surfaced only when both cloud AND local-Chrome auto-start are unavailable. Browser mode governs the pre-flight gate, pacing, and failure handling — see **Mode Detection And Local Chrome Mode**.
- **Product**: category, key features, materials/mechanisms/tech, price tier.
- **Buyer**: likely age/life stage, household, home status, pain points, competitive alternatives.
- **Purchase driver**: one Primary Driver and 1-2 Secondary Drivers from the routing table.
- **Designer share target** (optional override): if brief carries `designer_share_target: [lo, hi]` (e.g. `[0.45, 0.75]` for a luxury statement piece, `[0.05, 0.25]` for a kid-proof family sofa), capture both bounds — they replace the driver default range in the **Vertical diversity floor** check. Absent this field, the driver default applies.
- **Scene**: room/use case, content angle, why the product belongs there.
- **References**: user-supplied winners/benchmarks and the likely conversion mechanism behind them.
- **Assumptions**: mark missing fields as `inferred` and disclose them later.

If no brief exists, infer a provisional persona from product category + visible claims. Use **Embedded Historical Search Experience** by default; newer user-supplied winners override it only when closer to the current product family and repeatedly commercial, not just visually similar.

## Driver And Historical Calibration
Pick **one Primary Purchase Driver**. If two drivers tie, choose the one closest to buyer intent, not product appearance. The same sofa may route to A for cozy aesthetics, B for family hosting, or D for home-theater/gaming setup.

| Driver | Bought for | Creator worlds to test | Cross-vertical bridges (REQUIRED seed diversity sources) |
|---|---|---|---|
| **A. Emotion / Aesthetic** | cozy, beautiful, premium, family warmth, design statement | decor, cozy lifestyle, interior styling, day-in-life, personality-led lifestyle | cozy booktok / book vloggers, slow-morning / coffee aesthetic creators, candle/scent ASMR, comfort foodies (home baking, ramen-at-home), film / K-drama mood-board creators, indie cafe culture |
| **B. Family Life / Practical** | hosting, kid/pet durability, big household, daily use | moms, family/couple lifestyle, homeowner, practical-home, family humor | parenting comedy duos, dog / cat household creators, big-family vloggers, dinner-party foodies, kid-activity / craft creators, RV / road-family lifestyle |
| **C. Function / Storage** | organization, hidden storage, layout, cable/space efficiency | organization, renovation, DIY, productivity/hacks, practical setup | small-space / 500sqft living, van life, dorm & first-apartment hacks, minimalist creators, ADHD / neurodivergent organization, WFH productivity creators |
| **D. Device / Specialized Use** | AV fit, ventilation, cable flow, gaming/vinyl/office compatibility | home theater, setup, gaming, tech-lifestyle, makers, explainers | streamer / podcast creators, vinyl / music collectors, cinephile / film-club, esports personalities, sneaker / collector culture, "creator-about-creating" content |
| **E. Design Authority** | materials, taste, elevated design, statement value | designers, premium stylists, design-forward creators, fashion/luxury taste-makers | fashion editorial / personal-style, art gallery / curator content, vintage / antique hunters, architecture appreciators, perfume / fragrance taste-makers, luxury travel |

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

**Bias warning on these priors.** Every benchmark above is a home / design / lifestyle creator because that's the vertical historical campaigns over-tested, NOT because other verticals fail. Use the priors for **conversion mechanism** (what hook made the Reel convert) — never for **vertical anchoring** (what niche the creator sits in). When the routing table's "Cross-vertical bridges" column conflicts with what these priors imply about niche, the bridges column wins. Treat the prior list as evidence of what worked once, not as a model of who else can work.

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
| Self-commerce (furniture only) | NOT a furniture seller themselves — DISCARD if bio, link-in-bio (Linktree/Beacons/Stan etc.), pinned posts, or any of the last 10-15 Reels promote their own furniture brand, furniture DTC store, furniture dropshipping, or a persistent furniture storefront (e.g. Amazon shop / LTK / Shop My where furniture is a recurring category, not a one-off affiliate post). Non-furniture self-commerce (fashion / beauty / food / kitchenware / decor accessories / tech / pet) does NOT trigger this rule — those creators are fine and often better at branded-content execution. |
| Operator do-not-contact list | DISCARD if the handle appears in the bridge's do-not-contact set (operators flag accounts as `competitor` etc. via the console's archive modal). Fetch the set ONCE at run start (see **Pre-discovery do-not-contact pull** below) and match candidate handles against it before any per-profile qualification. |
| Competitor deals | no active exclusive direct competitor deal; past one-off competitor collab is a positive flag |
| Scores | Match ≥ 70 and Showcase ≥ 50 |

Before applying the follower threshold, normalize any locale-specific shorthand to an absolute count. Treat `K/k = 1,000`, `M = 1,000,000`, `B = 1,000,000,000`, `万/w = 10,000`, and `亿 = 100,000,000`. Example: `73.8万` = `738,000`, so it PASSES the `≥ 100k` gate; `4.6万` = `46,000`, so it fails.

## Pre-discovery do-not-contact pull
Before the first seed search, pull the operator-maintained do-not-contact list ONCE per run from the bridge and keep it in memory for the rest of the run. These are accounts that operators have manually archived via the console with reasons like `competitor` (self-selling furniture, brand-owned account, etc.) — they must never reappear in a shortlist regardless of how good their content looks.

```bash
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py list-relationships \
  --env LIVE --last-outcome competitor --limit 1000
```

Extract `items[*].primary_handle` (lowercased) into a set. Repeat for any other do-not-contact outcomes if the operator adds new ones (today: only `competitor`). During qualification, before issuing `browser_navigate` to a candidate profile, lowercase-compare the handle against this set — if matched, log a one-line skip and move on. **Do not** spend tool turns measuring views/ER on a do-not-contact handle.

If the bridge call itself fails (network, auth), do NOT silently proceed with an empty list — that would defeat the operator gate. Report `do_not_contact_pull_failed: <reason>` and either retry once or stop the run; treat it like a pre-flight gate failure.

## Prior runs handling
If the brief contains a `# prior_runs` block, **read it BEFORE generating any seeds**. Each entry lists what an earlier round of this same campaign generation already tried (`attempted_angles`, `remediation_attempted`), where it fell short (`floor_unmet_reason`, `diversity_floor_unmet`, `underserved_verticals`), and — most important — what it flagged as worth investigating next (`next_round_focus`). Rules, in priority order:

1. **Work the most recent round's `next_round_focus` FIRST.** This is a concrete, agent-curated queue of @handles / hashtags / seeds / reels the previous run identified as the highest-payoff next steps. Burn through it before doing any open-ended exploration. Each item carries its own one-sentence rationale; respect it. (If you disagree with the rationale, note it in your own `next_round_focus` rather than silently skipping.)
2. **Do NOT re-issue any seed/hashtag/public-web query** that appears in any prior round's `attempted_angles` or `remediation_attempted`, UNLESS the prior `floor_unmet_reason` was infrastructural (`rate_limit`, `cdp_lost`, `checkpoint`, `bridge_down`, `gateway_down`). Content-exhaustion reasons (e.g. "niche exhausted", "no new candidates surfaced") do NOT warrant re-trying the same seeds.
3. **After `next_round_focus` is exhausted**, prioritize NEW seeds that fill the most recent round's `underserved_verticals`.
4. The `# this_round_guidance` block in the brief restates these rules; treat any conflict as the guidance winning.

If no `# prior_runs` block is present, this is round 1 of the generation — proceed normally.

## Discovery
Maintain a prioritized queue and cover at least **2 discovery surfaces** unless blocked.

- **Hashtags**: generate 12-16 dynamic seeds, split into THREE buckets with HARD QUOTAS to prevent filter-bubble collapse. The two non-product buckets are mandatory, NOT "when relevant" suggestions:
  - **Product / category seeds (4-5, easy):** home/decor/category vocabulary tied to the driver — e.g. `#sectionalcouch`, `#mediaconsole`, `#diningtablestyling`. These mostly surface home/design creators; that's fine but it's also the bubble's gravity well, so don't stop here.
  - **Buyer-moment seeds (4-5, MANDATORY):** anchor on the life moment where the product appears, NOT on the product itself. These cut across verticals by pulling creators who never tag furniture. Examples: `#firstapartment`, `#movingvlog`, `#newlywedhome`, `#datenightin`, `#movienightin`, `#postpartumlife`, `#hostingseason`, `#emptynesthome`, `#wfhlife`. Pick from the buyer's life stage in the Campaign Context.
  - **Cross-vertical bridge seeds (4-5, MANDATORY):** pulled from the driver routing table's "Cross-vertical bridges" column. **At least 3 of these must come from non-home subcultures** (gaming, comedy, foodie, fashion, pet, book, fitness, etc.). E.g. for driver A: `#cozybooktok`, `#slowmorning`, `#comfortmeal`; for D: `#streamersetup`, `#vinylcollection`, `#cinephile`; for B: `#dogmomlife`, `#parentingcomedy`, `#dinnerpartytok`.
  Direct URL: `https://www.instagram.com/explore/search/keyword/?q=%23<tag-name>`. If you cannot populate the two mandatory buckets, list the gap under `attempted_angles` and treat the discovery surface as incomplete — do NOT proceed to lateral expansion until you've at least tried the cross-vertical seeds, because lateral expansion from home-only seeds is precisely the loop that produces designer-heavy shortlists.
- **Comment mining (two sources, both required):**
  - From **qualified top Reels** (same-vertical signal): inspect creator-looking commenters; enqueue only if preview/profile shows ≥ 100k followers.
  - From **buyer-moment hashtag Reels** (cross-vertical signal — the single most effective break-out lever): pick 2-3 high-engagement Reels under buyer-moment hashtags whose AUTHOR is NOT a qualified home/design KOL (could be a comedy duo, couple vlog, foodie, pet creator — anyone). The criterion is that the AUDIENCE overlaps with our buyer, not that the creator sits in our niche. Mine their commenters the same way (≥ 100k filter still applies). Audience overlap predicts branded-content fit better than creator vertical.
- **Following / Suggested / Similar**: expand from qualified profiles, applying ≥ 100k and NA checks before enqueueing. **Cross-vertical jump rule:** in every 3-hop expansion chain, AT LEAST ONE hop must land on a creator whose primary vertical differs from the seed's vertical (verify via their last 10-15 Reels content theme, not just bio). Prefer hops that follow a visible cross-vertical collab (a home creator collab'd with a foodie → enqueue the foodie). Pure same-vertical chains beyond hop 2 are not allowed; if IG's similar-accounts panel only surfaces same-vertical handles for two hops in a row, abandon the chain and switch surface — that's the algorithm telling you the bubble is closed.
- **Public web (Google / TikTok / Reddit) — co-primary surface, not just fallback**: required, NOT only when IG search blocks. IG's similar-accounts engine is structurally same-vertical, so this is the primary lever for surfacing creators that IG won't recommend to you. Run NA-scoped queries against the buyer-moment and cross-vertical seeds — e.g. `"first apartment tour" instagram reels`, `"streamer setup" creator NA 100k`, `site:tiktok.com cozy bookshelf US`, `reddit r/InteriorDesign favorite non-designer home creators`. MUST be invoked when (a) IG seed search returns < 5 distinct vertical sources after 2 hashtags, OR (b) the running persisted-candidate pool is ≥ 70% concentrated in one vertical (designer / interior / home-decor). Cross-verify each surfaced handle on IG before qualifying. Treat this surface as cheap insurance against the bubble — invoke it early, not only after IG breaks.
- **Reference expansion**: if user supplies winners, inspect 5-10 Reels, extract the conversion mechanism, then expand through following/similar/commenters even outside home vertical.

Lateral expansion from seed results is capped at **3 hops**. One failed hashtag, browser session, selector, or extraction call never ends the run; switch surface or seed.

## Persistence And Run
Do not stop at the first acceptable candidate. Continue until each priority product feature / selling-point group has a defensible creator set, or all relevant surfaces are exhausted.

**Structured diagnostics (mandatory in EVERY final answer).** Every run — whether you hit the floor or not — MUST end with the following YAML block so the backend can persist it for future rounds. The console parser keys on these exact field names; do not rename them.

```
attempted_angles:
  - <hashtag / seed / public-web query / surface 1>
  - <hashtag / seed / public-web query / surface 2>
  - ...
vertical_coverage:
  - designer: 0.XX
  - family_practical: 0.XX
  - tech_setup: 0.XX
  - foodie: 0.XX
  - comedy_lifestyle: 0.XX
  - other: 0.XX
next_round_focus:
  - "<@handle | #hashtag | seed phrase | reel URL> — <one-sentence why this is worth prioritizing next>"
  - ...
```

**`next_round_focus` rules** (read carefully — this is what makes auto-retries non-redundant):
- Concrete items only: a specific @handle to verify reels for, an unattempted hashtag/seed, a specific reel URL to load, or a niche to expand into. Not generic advice ("try more").
- Each item MUST end with ` — <why>`: the one-sentence rationale that tells the next round why this beats fresh exploration. A bare handle without rationale is useless context.
- Max **10 items**. If you have more candidates than that, pick the 10 with the highest expected payoff. The composer hard-caps at 10 anyway; items 11+ are dropped silently.
- Emit at least 1 item whenever you have ANY honest lead — even a single qualified-but-uncrawled handle is signal. Only emit an empty list when you genuinely have nothing actionable for the next round (rare; usually means you should report a hard blocker via `floor_unmet_reason`).

When you stopped short of the quantity floor OR landed outside the active designer range, also include these fields in the same block (already specified in "Quantity floor" and "Vertical diversity floor" below):

```
floor_unmet_reason: <one-sentence why>
diversity_floor_unmet: <e.g. 0.85>
active_range: [<lo>, <hi>]
active_range_source: <"brief_override" or "driver_default:A|B|C|D|E">
underserved_verticals:
  - <vertical 1>
  - <vertical 2>
remediation_attempted:
  - <cross-vertical seeds you tried>
  - <buyer-moment hashtags you mined>
  - <public-web queries you ran>
```

These fields feed the rediscover brief composer; round N+1 reads them from a `# prior_runs` block (see **Prior runs handling**) and avoids re-tracing exhausted angles. Omitting them silently degrades subsequent auto-retries.

**Quantity floor (hard).** When the brief carries `discovery_target_count` or `additional_target_count`, treat it as a HARD FLOOR on PERSISTED candidates (visited via `browser_navigate`, then qualified, then `add-candidate`). The console's quantity gate compares your persisted count against the floor immediately after this run terminates. If you are short of the floor AND auto-retry budget remains, the backend AUTO-FIRES the rediscover skill again (up to 5 auto-retries total = 6 runs max); after that, the operator gets a `discovery_floor_unmet` escalation. Stopping short is therefore a failure mode — finishing partial is acceptable only when truly blocked (rate limits, niche exhausted, IG checkpoint). When stopping short, you MUST set `floor_unmet_reason` (one-sentence why) in the structured diagnostics block above so the backend can decide between auto-retry and early escalation; `attempted_angles` is already mandatory regardless.

**Vertical diversity floor (hard).** Across the persisted shortlist, the **designer / interior-stylist share** must fall inside the **active range** for this run. "Designer" = creators whose bio or last 15 Reels primarily anchor in interior design, home staging, design education, premium stylist content, or "design firm / studio principal" identity.

The active range is resolved in this priority order:

1. **Brief override (highest priority):** if the brief contains `designer_share_target: [lo, hi]` with `0 ≤ lo < hi ≤ 1`, use those bounds. Use this for edge cases the driver default doesn't capture well (e.g. a luxury statement piece routed to A might want `[0.45, 0.75]`; a kid-proof family sofa routed to B might want `[0.05, 0.25]`).
2. **Driver default (fallback):** look up the Primary Driver in this table.

| Primary driver | Default designer share range |
|---|---|
| **A. Emotion / Aesthetic** | 30% – 60% |
| **B. Family Life / Practical** | 15% – 40% |
| **C. Function / Storage** | 15% – 40% |
| **D. Device / Specialized Use** | 10% – 35% |
| **E. Design Authority** | 50% – 80% |

Rationale: A/E lean toward visual taste so designers should be plural; B/C/D are bought for non-aesthetic reasons (family, organization, device fit) so designer share above ~40% almost always means filter-bubble drift rather than genuine fit. Designers are valuable — do NOT eliminate them — but exceeding the upper bound means the run has collapsed into IG's similar-accounts bubble; falling below the lower bound means design authority is underserved. Other verticals (moms, gaming, comedy, foodie, fashion, pet, book, fitness, tech-setup, etc.) are NOT individually capped — let them fill the remainder freely. When the persisted share lands outside the active range, populate these fields in the structured diagnostics block above:

```
diversity_floor_unmet: <designer_share value, e.g. 0.85>
active_range: [<lo>, <hi>]
active_range_source: <"brief_override" or "driver_default:A|B|C|D|E">
underserved_verticals:
  - <vertical 1, e.g. family/practical creators>
  - <vertical 2, e.g. cross-vertical buyer-moment creators>
remediation_attempted:
  - <which cross-vertical seeds you tried>
  - <which buyer-moment hashtags you mined>
  - <which public-web queries you ran>
```

Treat this with the same severity as `floor_unmet_reason` — the backend can auto-retry with a stronger cross-vertical bias. **Mid-run rebalancing is cheaper than escalation:** while the run is still in progress, if you notice the share drifting past the upper bound, STOP adding more designer candidates and run a buyer-moment hashtag pass + public-web cross-vertical query before continuing. Rebalancing now beats failing the floor at the end.

Minimum evidence when reachable:
- review at least **3 High-Match candidates**;
- sample at least **2 discovery surfaces**;
- measure 10-15 recent Reels per qualified creator;
- run `browser_navigate` to every candidate's profile URL (`https://www.instagram.com/<handle>/`) at least once in this run — this is the hard registration gate the orchestrator skill enforces before allowing `shortlist_ready`;
- use screenshots (`browser_snapshot` / `browser_vision`) and extract numbers via `browser_console(expression="...")` from the rendered page;
- when `veedcrawl_metadata(url=...)` is in your toolset, prefer it for per-Reel facts because it is free; when it is NOT in your toolset (e.g. the active agent profile has not enabled the veedcrawl plugin), fall back to `browser_navigate` on the Reel URL plus `browser_console`/`browser_vision` to read view counts, likes and dates. Do not abort the run because veedcrawl is unavailable;
- use `veedcrawl_extract(url=..., prompt=...)` only when the user explicitly requests paid/deep extraction.

**Partial Reel-cover load is acceptable (soft).** IG's Reel grid thumbnails frequently fail to render for transient reasons (CDN flakes, lazy-load delays, IG throttling, viewport virtualization) — this is normal and does NOT mean the candidate is unjudgeable. Rules:
- Judge showcase fit from whatever covers DID load. **6+ visible covers out of 12-15** is enough to assess content theme, scene fit, and on-camera style; do not gate qualification on a full grid.
- Before declaring "no signal", do ONE scroll + re-snapshot to give lazy-load a chance. If still empty, try `browser_get_images` to pull whatever the page has cached.
- Only abandon a candidate as unjudgeable when **zero** Reel covers render after that one retry AND captions/alt-text are also empty. In that case skip the candidate and move on — do NOT escalate to `mode_gate_blocked` or count this as a surface failure.
- Do not penalize a creator's Showcase Score for a partial grid; score from the covers you can see and note "partial grid" in evidence if it affected sample size.

**Anti-fabrication rule (hard).** Every handle you place into the orchestrator's `shortlist_ready` `candidates` array MUST be a handle that you actually visited via `browser_navigate("https://www.instagram.com/<handle>/")` earlier in the same run, with on-page evidence supporting the numbers you write into `audience_fit`, `engagement_quality`, `niche_match`, and `reason`. Generic-sounding placeholders (`home_style_lover`, `minimalist_home`, `cozy_living_xx`, `test_kol_*`) are red flags; if you cannot point to the corresponding `browser_navigate` call, omit the handle. It is better to return fewer real candidates (or invoke the orchestrator's zero-results escape hatch after at least 3 distinct surface visits) than to invent any.

**IG profile URL persistence (free side effect of `add-candidate`).** Every handle that survives qualification has by definition been visited at `https://www.instagram.com/<handle>/`. After you call `add-candidate` for a handle, also persist that profile URL as a reusable identity fact so the web detail page can offer a one-click quick-link to the creator's IG profile and so the next campaign that touches this KOL inherits the URL. Issue ONE `write-facts-multi` call per candidate immediately after `add-candidate` succeeds (the response carries `identity_id`):

```bash
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py write-facts-multi \
  --identity-id <identity_id> --env <env> \
  --json '{"campaign_id": null,
            "source": "skill:instagram-kol-discovery",
            "namespaces": {
              "identity": {
                "identity.instagram_profile_url":                 "https://www.instagram.com/<handle>/",
                "identity.instagram_profile_url_source":          "ig_bio",
                "identity.instagram_profile_url_discovered_at":   "<iso8601 now>",
                "identity.instagram_profile_url_discovered_url":  "https://www.instagram.com/<handle>/"
              }
            }}'
```

Notes:
- `campaign_id: null` makes the URL a reusable identity fact (the IG handle doesn't change per campaign).
- **Do NOT overwrite a non-empty existing value.** If `read-facts` (or a prior call) already shows `identity.instagram_profile_url` is set, skip the write.
- Treat this as best-effort: if the write fails, log it but do NOT block the run. The `add-candidate` registration is the authoritative signal; the URL fact is convenience data.
- This applies to ALL qualified candidates you call `add-candidate` on — not only the ones that make the final shortlist. They're equally valid future-campaign reuse candidates.

**`primary_email` — only a real email address, never anything else.**

- If the IG profile (bio text, contact button reveal, pinned post, or bio image you OCR'd via `vision_analyze`) clearly exposes a real address matching `x@y.tld` and it visibly belongs to the creator (not a sponsor / unrelated brand sidebar), you MAY include it in the `upsert-identity` payload. Attach provenance facts in the same `write-facts-multi` call: `identity.email_source = "ig_bio"`, `identity.email_discovered_at`, `identity.email_discovered_url`, `identity.email_discovery_tier = "0"` (tier 0 = discovered during shortlist qualification, before `kol-email-discovery` ever runs). Do NOT overwrite a non-empty existing `primary_email`.
- If the profile shows ONLY a link-in-bio URL, a personal website domain, or a brand display name, do NOT shove those into `primary_email` — the bridge will 422 with a `ValueError`, wasting a turn. Route them to identity facts instead (table below) and leave `primary_email` for `kol-email-discovery` (which runs post-approval) to resolve.

Identity facts for non-email contact signals — write these in the same `write-facts-multi` call you already issue for `identity.instagram_profile_url`:

| Bio string | Fact key |
|---|---|
| `linktr.ee/…`, `beacons.ai/…`, `bio.link/…`, `lnk.bio/…`, `solo.to/…`, `linkin.bio/…` | `identity.linktree_url` |
| Creator's personal/brand domain (their site, not a third-party shop) | `identity.personal_site_url` |

These are the same keys `kol-email-discovery` writes, so the two skills don't diverge. Apply the same "do NOT overwrite a non-empty existing value" rule, and attach the provenance triple (`<key>_source = "ig_bio"`, `_discovered_at`, `_discovered_url`).

**Creator brief persistence (free side effect of `add-candidate`).** Downstream outreach drafters (`kol-cold-outreach`, `kol-reengagement-outreach`) personalize the opening by reading a small "creator brief" off the identity facts. You have already navigated this candidate's profile + multiple Reels to qualify them, so you have the raw material already — **emit it as 6 identity-level facts in the same `write-facts-multi` call** that writes `identity.instagram_profile_url` above. Do not open extra pages for this; do not extend the page-load budget.

For each qualified candidate, merge these keys into the same `write-facts-multi` payload (under the `identity` namespace, alongside the IG profile URL fields):

| Fact key | Value shape | Source it from |
|---|---|---|
| `identity.content_pillars` | `list[str]`, 2-4 short phrases | Bio + recurring Reel themes |
| `identity.signature_hooks` | `list[str]`, 2-3 hook types | The structural pattern of top Reels (e.g. "before/after walk-through", "POV diary", "honest unboxing") |
| `identity.voice_descriptors` | `list[str]`, 2-3 tone words | **Prefer descriptors that appear repeatedly in the comments section** ("so cozy", "deadpan humor", "honest reviews") over the creator's self-description |
| `identity.hero_post_url` | `str`, single Reel URL | The single best Reel for this product fit (highest views *and* clearest theme match) |
| `identity.hero_post_note` | `str`, 1 sentence | Why this post is representative (e.g. "412k-view comfort tour of her new house") |
| `identity.recommendation_reason` | `str`, 1 sentence | Same content you write into the candidate `payload.reason` — campaign-fit angle in plain language |

Each of the 6 keys MUST also carry a provenance triple (same pattern as the IG profile URL above):

```bash
"identity.content_pillars_source":         "ig_profile_and_reels",
"identity.content_pillars_discovered_at":  "<iso8601 now>",
"identity.content_pillars_discovered_url": "<the profile or hero post URL>",
```

and likewise for the other 5 keys.

**Signal sources** — all already in your tool surface, no new page loads:
- Bio text from the profile page (already loaded for qualification).
- Captions / hashtags from the 2-3 Reels you scored.
- Reel cover overlay text via `browser_get_images` + `vision_analyze` when the caption is too thin (creators often print the theme on the cover).
- Top-of-page Reel comments (first viewport only, do NOT scroll or expand "View replies") via `browser_console` — comments reveal **how viewers describe the creator**, which is more honest signal for `voice_descriptors` and `signature_hooks` than the creator's self-pitch.

**Write rules** (same as the IG URL above):
- **Do NOT overwrite a non-empty existing value.** Read `identity.content_pillars_discovered_at` first; if it exists and is **within 90 days**, skip the write. If it's older than 90 days, the loader (`kol-creator-brief-loader`) will refresh on next draft anyway — leave the stale value alone here.
- Best-effort: if the brief generation fails (vision call errors, comments empty, LLM disagrees with itself), skip the brief writes but still write the IG profile URL. The loader has its own fallback path.
- Applies to ALL qualified candidates, not only the final shortlist.

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
- Search coverage: (surfaces used, seed counts per bucket: product / buyer-moment / cross-vertical, public-web queries run) — must match the `attempted_angles` YAML emitted at the end of the answer (see **Structured diagnostics** in Persistence And Run)
- Vertical coverage: designer X% | family/practical X% | tech-setup X% | foodie X% | comedy/lifestyle X% | other X% — record the active designer range (driver default or `designer_share_target` override) AND the actual share, and confirm the share lands inside that range. This human-readable line is a view of the `vertical_coverage` YAML block (the parser keys on the YAML, not this line).

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

Also include: discarded candidates with failing criterion, optional reference override if any, assumptions from Brief Fallback, search coverage (reviewed total, High-match total, surfaces used/blocked), and vertical-diversity stats (designer share, full vertical distribution, list of cross-vertical seeds and public-web queries attempted, plus any mid-run rebalancing actions taken).

## Mode Detection And Local Chrome Mode
The browser tool surface (`browser_navigate / click / snapshot / console / vision`) routes through one of two backends, picked at the worker process by the presence of `BROWSER_CDP_URL`:

- **Cloud Mode** (default, no `BROWSER_CDP_URL`): goes through Browser Use cloud — built-in US residential proxy, ephemeral session per task, `BROWSER_USE_PROFILE_ID` for IG login persistence. See **Cloud Mode (Browser Use) Notes** below.
- **Local Chrome Mode** (`BROWSER_CDP_URL` set, typically via `/browser connect` after the user runs `playground/local-chrome-debug/start-debug-chrome.sh`): drives the user's real Chrome over CDP. Exit IP, IG login, and account identity belong to the user — there is no sandbox.

You cannot inspect the active backend at runtime, so the brief's **`browser_mode:`** field is authoritative. Default to `local-chrome` when unset. Do not confuse with the brief's `mode:` field — that is the campaign env (`LIVE` / `TEST`), unrelated to browser backend selection.

**Cloud-to-CDP auto-fallback** (always on for both modes): when the Browser Use API returns 5xx / connection errors, the runtime probes `http://127.0.0.1:9222`; if no debug Chrome is running it auto-launches one via `start-debug-chrome.sh` and routes subsequent calls through CDP. Tool responses for the rescued session carry `fallback_from_cloud=true` plus `fallback_mode=cdp` (already running) or `fallback_mode=cdp_autostart` (we launched it). If both fail you'll see a `RuntimeError("Cloud provider ... failed ... and local debug Chrome could not be reached or auto-started")` — stop the run, tell the user to manually run `playground/local-chrome-debug/start-debug-chrome.sh` and log into Instagram once in that isolated profile, then retry. Auto-started Chrome uses an isolated profile (`~/.hermes/local-chrome-debug-profile`) which starts logged out on first run.

### Pre-flight gate (mandatory in Local Mode, optional in Cloud Mode)
First tool call of the run, before any IG URL:
1. `browser_navigate("https://ipinfo.io/json", timeout=30)`
2. `browser_console(expression="document.body.innerText")` — parse JSON, read `country`.
3. If `country != "US"` → stop the run immediately and return `mode_gate_blocked: non-US exit (got <country>)`. Do not navigate to instagram.com, do not retry — surface to the user so they can fix their VPN.
4. If `country == "US"` → log the org/IP in the run report and proceed.

### Local Chrome Mode — conservative rules (main-account protection)
This is the default browser mode (used when brief says `browser_mode: local-chrome` or omits the field). The agent is operating the user's primary Instagram session. Treat every action as visible to IG's risk system.

- **Pacing**: insert a random `2-4s` pause between candidate profiles; `1-2s` between reels within the same profile. No concurrent profile/reel browsing.
- **Per-run caps**: at most **40 distinct profiles** and **200 reel page loads** per skill invocation. On hitting either cap, stop and deliver partial results; tell the user to resume in a new run.
- **Forbidden actions** (never issue these clicks/inputs, even by accident): `follow`, `unfollow`, `like`, `save`, `comment`, `send DM`, `share`, `subscribe`, any form submission, any login-page interaction. Read-only navigation, screenshots, `browser_console` JS extraction, and scrolling are allowed.
- **Login assumption**: the user has already logged into their main IG account inside the debug-Chrome profile. Never navigate to `/accounts/login/`, `/accounts/signup/`, or any auth flow. Never type credentials.
- **Risk-page response**: if you encounter a checkpoint, captcha, "Suspicious login attempt", "Action blocked", "Try again later", or any consent/age-gate interstitial → stop immediately, return `mode_gate_blocked: rate_limited`. Do not refresh, do not switch accounts, do not retry the offending URL.
- **Metadata source preference**: when `veedcrawl_metadata` is available, prefer it over loading the reel page in the user's browser — every IG page load on the main account costs risk budget.

### Cloud Mode — when this section applies
Used only when brief explicitly says `browser_mode: cloud` (default is `local-chrome`). All current Cloud Browser Notes below apply; pre-flight gate is recommended but not mandatory (Browser Use already pins US).

## Cloud Mode (Browser Use) Notes
- Set `BROWSER_USE_API_KEY`; optional `BROWSER_USE_PROFILE_ID` preserves Instagram login state. Browser Use `/browsers` has no `keepAlive`; reuse by not stopping the session.
- Keep default US proxy for Instagram; do not disable it.
- Act when visible, avoid redundant screenshots/snapshots, wait only 1-2 seconds unless needed.
- If hashtag pages time out, switch to public-web seed creators + Similar accounts. If Instagram fails 3 consecutive times, document the blocker and fall back.

### Browser Reliability Rules (learned from agent.log failures)

**Apply in both modes:**
- **Always re-snapshot after navigation**: any `browser_navigate`, `browser_click` that triggers a route change, or page reload **invalidates all `@eXX` refs**. Before the next `browser_click` / `browser_type`, you MUST call `browser_snapshot` and use refs from the new snapshot. Reusing a ref across pages will surface as `Unknown ref: eXX` and waste a tool turn.
- **Stop retrying the same call**: if `browser_navigate` to the same URL fails twice, do NOT issue a third identical call. Switch tactic — see mode-specific tactic order below. The runtime emits `same_tool_failure_warning` at count=3 — treat that as a hard stop signal.

**Cloud Mode only:**
- **Navigation timeout**: the default 60s is too short for Browser Use free tier on IG / heavy SPA pages. Pass `timeout=150` (or higher) on `browser_navigate` for first-load of `instagram.com/*`, `xiaohongshu.com/*`, or any infinite-scroll feed. Subsequent in-SPA navigation can use the default.
- **Retry tactic order (cloud)**: (a) change `wait_until` (`domcontentloaded` instead of `load`), (b) try the public/non-login URL variant, (c) `cleanup_browser(task_id)` then retry once to force a fresh cloud session, (d) fall back to `veedcrawl_extract` or public-web search.
- **CDP closed / channel errors**: when you see `CDP response channel closed`, `Could not compute box model`, or `Failed to take screenshot ... CDP response channel closed`, the cloud session is dead. Call `cleanup_browser(task_id)` once, then re-issue the navigate; the next tool call will auto-create a new Browser Use session. Do not keep clicking on the dead session.
- **Element-not-found loops**: `Could not locate element with role=...` usually means the page hasn't finished rendering or the element is inside a virtualized list. Resolve by: snapshot → scroll once → snapshot again, instead of retrying the same click.
- **Cloud provider failure (4xx transient / 5xx)**: `BrowserUseProvider failed (...)` triggers the cloud-to-CDP auto-fallback described above. When you observe `fallback_from_cloud=true` with `fallback_mode in {cdp, cdp_autostart}`, **don't change strategy** — continue with the same plan, just note the marker in the run report. If instead `browser_navigate` raises `RuntimeError("... and local debug Chrome could not be reached or auto-started")`, **stop the run** and surface to the user — repeated cloud calls won't recover.

**Local Chrome Mode only:**
- **Default navigation timeout is fine** — the local Chrome is much faster than the Browser Use free tier. Don't pad `timeout` unless you have evidence of slow loads.
- **Retry tactic order (local)**: (a) snapshot → scroll once → snapshot again to handle virtualized lists, (b) try the public/non-login URL variant, (c) **move on to the next candidate**. Never run `cleanup_browser` (it would drop the user's attached CDP session), never repeatedly hit the same profile — repeat probes on the main account are exactly what IG flags.
- **No CDP "channel closed" recovery**: if CDP truly drops, the user needs to re-run the launcher script and re-issue `/browser connect`. Surface this as `mode_gate_blocked: cdp_lost` and stop.
- **Element-not-found** → one snapshot/scroll/snapshot retry, then skip the candidate. Do NOT keep clicking.

## References
- `references/bridge-cli-json-payloads.md` — exact kol_bridge_tool JSON field names and per-candidate persistence order for rediscovery runs.

## Pitfalls
- For bridge CLI persistence, do not guess JSON keys per subcommand. `upsert-identity` expects `primary_handle`; `write-facts-multi` should be called with `--identity-id`; `add-candidate` is safest with `identity_id` already embedded in the JSON payload. Prefer file-backed `@/tmp/*.json` payloads.
- Do not default to home/decor creators just because the product is furniture.
- Do not let designer / interior-stylist creators exceed the **active upper bound** (driver default from the Vertical diversity floor table, or `designer_share_target[1]` if brief overrides). Designers are good — concentration is the failure mode, not their presence. If you're already at upper bound and about to add another designer before any cross-vertical candidate has cleared qualification, STOP and run a buyer-moment hashtag pass plus a public-web cross-vertical query first. Inversely: do NOT fall below the **active lower bound** either — that signals over-correction and a lost design-authority leg. Note the bounds differ sharply by driver (E: 50–80% designers; D: only 10–35%); pulling the right range from the table is part of the floor check, not a footnote.
- Do not skip the buyer-moment or cross-vertical seed buckets when generating hashtags. They are mandatory quotas, not "when relevant" suggestions. Three same-vertical hashtags in a row is a sign you skipped the quota and need to back up.
- Do not treat public web (Google / TikTok / Reddit) search as a fallback that only fires when IG breaks. It is the primary break-out lever — IG's similar-accounts engine cannot show you creators it doesn't already cluster with home.
- Do not chain 3 same-vertical lateral hops just because each individual hop met the follower / region threshold. The cross-vertical jump rule requires at least one vertical-switch per 3-hop chain; pure same-niche chains reinforce the bubble even when every individual candidate is qualified.
- Do not let visual similarity outrank buyer intent for functional, technical, family-practical, or use-case products.
- Do not shortlist on Audience Match alone; Match ≥ 70 and Showcase ≥ 50 must both pass.
- Do not reject tech, gaming, comedy, entertainment, fashion, or lifestyle creators solely by niche.
- Do not keep creators who self-sell furniture (own brand, DTC, persistent furniture storefront like `mytexashouse`-style accounts) — they are direct competitors no matter how lifestyle-personal the feed looks. Always check bio, link-in-bio, pinned posts, and the last 10-15 Reels for recurring furniture-commerce signals. Self-commerce in other categories (fashion / beauty / food / kitchenware / decor accessories / tech / pet) does NOT trigger this rule.
- Do not overfit historical winners' surface style; reuse the conversion mechanism.
- Do not include Reels posted within the last 72h in averages.
- Do not compare follower thresholds against locale-formatted shorthand until you have normalized it to an absolute count. `73.8万` is `738,000`, not `73.8k`.
- Do not keep commenters with < 100k followers.
- Do not call `veedcrawl_extract` without explicit request and both `url` + `prompt`.
- **Local Mode — never** issue a `follow / like / comment / save / DM / share` action, even when a snapshot lists it as the easiest-looking element. The skill is read-only on the main account.
- **Local Mode — never** retry a URL that returned a checkpoint/captcha; never refresh hoping it resolves. Stop the run instead.
- **Local Mode — never** skip the `ipinfo.io` pre-flight, even if the previous run in the same hermes session passed it (VPN state can flip mid-session).