---
name: instagram-kol-discovery
description: Generic North America Instagram KOL discovery framework for any furniture product. First interprets the product brief, persona, and research documents to identify the buyer's real purchase driver, then dynamically routes to the right creator archetypes, seed terms, and scoring weights before crawling.
trigger: When user asks to find Instagram KOLs/influencers for any furniture product (sofa, bed, dining, storage, media console, cabinet, designer pieces, etc.), interpret a product brief / persona / research doc, route to the correct purchase-driver category, generate seeds dynamically, and qualify candidates against the framework.
tags: ["instagram", "kol", "influencer", "furniture", "home", "veedcrawl", "local-chrome"]
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

- **Browser stack**: **local debug Chrome only** via built-in `browser_*`
  tools (`browser_navigate`, `browser_snapshot`, `browser_click`,
  `browser_console`, `browser_get_images`, `vision_analyze`). The
  `local-chrome-tab-pool` plugin auto-starts debug Chrome on the first
  call — do not open a browser manually. Do NOT use Browser Use cloud,
  `mcp_chrome_devtools_*`, `web_search`/`web_extract`, or **`delegate_task`**
  (discovery runs in the parent session; batching uses console `/rediscover`,
  not subagents). Brief may carry
  `browser_mode: local-chrome` (always treat as local-chrome). Do not
  confuse with the brief's `mode:` field — that is campaign env
  (`LIVE` / `TEST`). See **Local Chrome (`browser_*` tools)** below.
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
| Prior-collab skip list | DISCARD if the handle appears in the bridge discovery skip set: `competitor` (竞品不合作), `success` (已合作完成), `aborted` (主动叫停), `legacy_collab` (历史合作). Fetch ONCE at run start (see **Pre-discovery skip pull** below) and match before any per-profile qualification. 曾触达列表仅用于指标页的触达次数，**不**触发发现跳过。 |
| 14-day outreach cooldown | DISCARD if we already sent a confirmed outreach email to this handle within the last **14 days** (cross-campaign). Fetch the cooldown handle set ONCE at run start (see **Pre-discovery outreach cooldown pull** below). The bridge also hard-rejects `add-candidate` with `outreach_cooldown_active` if you skip the pre-check. |
| Competitor deals | no active exclusive direct competitor deal; past one-off competitor collab is a positive flag |
| Scores | Match ≥ 70 and Showcase ≥ 50 |

Before applying the follower threshold, normalize any locale-specific shorthand to an absolute count. Treat `K/k = 1,000`, `M = 1,000,000`, `B = 1,000,000,000`, `万/w = 10,000`, and `亿 = 100,000,000`. Example: `73.8万` = `738,000`, so it PASSES the `≥ 100k` gate; `4.6万` = `46,000`, so it fails.

## Nox audience screen (optional)
When the gateway brief includes `nox_discovery_enabled: true` and
`campaign_config_file:` (LIVE + `nox_quota_enabled`), run the **audience
screen** for each candidate **after** the profile visit passes handle /
follower pre-checks and **before** loading multiple Reels for ER/views scoring.

Purpose: discard creators whose **audience geography/demographics** fail the
US/CA mandate early — saves browser turns and keeps quota for viable handles.

Full procedure, CLI, persistence, and discard rules:
`references/nox-audience-screen.md`.

Summary:
1. Once per run: `quota-snapshot` (stop Nox on auth/quota exhaustion).
2. Per handle: check CAL `identity.nox_cache_month` + `identity.nox_top_region`
   first; on miss call `diligence-pack --gate discovery_qualify --dimensions audience`.
3. **Always persist** Nox facts via `upsert-identity` + `write-facts-multi`
   (even when discarding) so the monthly cache and CAL stay aligned.
4. On audience discard, log `nox_audience_discard: @handle — <reason>` and skip Reels.
5. When Nox is disabled or inconclusive, fall back to browser-only region signals.

Gate A shortlist diligence later reuses cached `audience` and only fetches
missing dimensions — discovery must not run the full four-dimension pack.

## Pre-discovery skip pull
Before the first seed search, pull the bridge **discovery skip** handle set ONCE per run and keep it in memory. This union covers:

| `reason` (from CLI) | Operator meaning |
| ------------------- | ---------------- |
| `competitor` | 竞品 — 不合作 |
| `success` | 已合作完成 / 达成合作 |
| `aborted` | 主动叫停 |
| `legacy_collab` | 历史合作（系统归档） |

```bash
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py list-discovery-skip-handles \
  --env <TEST|LIVE>
```

Parse the JSON stdout (do **not** use `--plain` — that drops `reason`). Build an in-memory
`handle → reason` map from `items[*]`:

- `handle` = lowercase, strip leading `@`
- `reason` = `items[i].reason` verbatim (`competitor` | `success` | `aborted` | `legacy_collab`)
- If the same handle appears more than once, keep the first `reason` encountered

During qualification, before `browser_navigate`, look up the candidate handle in this map —
if present, log `skip_prior_collab: @handle — <reason>` (use the mapped `reason`, not a guess)
and move on. **Do not** spend tool turns on skipped handles. The bridge also hard-rejects
`ingest-confirmed-candidate` with HTTP 409 `discovery_skip_active` if you skip this pre-check.

If the bridge call fails (network, auth), do NOT silently proceed with an empty set — report `discovery_skip_pull_failed: <reason>` and retry once or stop the run (same severity as the outreach-cooldown gate).

## Pre-discovery outreach cooldown pull
Before the first seed search (right after the skip pull), fetch every handle we outreached within the last **14 days** across **all** campaigns. Discovery must skip them even if they look like a perfect fit.

```bash
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py list-outreach-cooldown-handles \
  --env <TEST|LIVE> --plain
```

Lowercase each line into a set. During qualification, before `browser_navigate`, compare the handle — if matched, log `skip_outreach_cooldown: @handle` and move on. Do **not** call `add-candidate` for these handles; the bridge returns HTTP 409 `outreach_cooldown_active` if you try.

Handles outreached **more than 14 days ago** may be discovered again, but the console shortlist and KOL detail page will still show an **曾触达** tag with how long ago — operators use that context when approving.

If this bridge call fails, report `outreach_cooldown_pull_failed: <reason>` and retry once or stop the run (same severity as the skip pull gate).

## Prior runs handling
If the brief contains a `# prior_runs` block, **read it BEFORE generating any seeds**. Each entry lists what an earlier round of this same campaign generation already tried (`attempted_angles`, `remediation_attempted`), where it fell short (`floor_unmet_reason`, `diversity_floor_unmet`, `underserved_verticals`), pending ingests (`pending_ingests`), and what to explore next (`next_round_focus`). Rules, in priority order:

0. **If the brief contains `# resume_directives`, complete STEP_0 FIRST** — ingest every handle listed under `pending_ingests` that is not already in CAL (`list-candidates` exclusion set). Rebuild `/tmp/ingest_<handle>.json` (nested JSON per `references/bridge-cli-json-payloads.md`); do **not** assume `/tmp` files survive across runs. Call `ingest-confirmed-candidate` and verify before any new `browser_navigate` for exploration.
1. **Work the most recent round's `next_round_focus` FIRST** (after STEP_0). This is a concrete, agent-curated queue of @handles / hashtags / seeds / reels the previous run identified as the highest-payoff next steps. Burn through it before doing any open-ended exploration. Each item carries its own one-sentence rationale; respect it. (If you disagree with the rationale, note it in your own `next_round_focus` rather than silently skipping.)
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

### Veedcrawl supplement (optional — does NOT replace browser discovery)

When the `veedcrawl` toolset is enabled, use native Hermes plugin tools only
(`veedcrawl_*`). **Forbidden:** `mcp_veedcrawl_*`, direct REST/curl,
`execute_code` API calls. Browser discovery surfaces above remain **primary**;
at least **2 surfaces must be browser-based** (hashtag, comment mining,
following/similar, or public web). Veedcrawl search alone cannot satisfy the
≥2-surface rule.

| Supplement use | Tool | Per-run cap | Browser still required |
|---|---|---|---|
| Seed queue expansion | `veedcrawl_search_social_videos` (`q`, `platform=instagram`, `limit`≤20) | **3 calls** | Yes — `browser_navigate` registration + qualification |
| Pre-screen followers/reels | `veedcrawl_instagram_profile` | same handles as profile visits | Yes — bio/region/self-sell checks |
| Reel view/like stats | `veedcrawl_metadata` | free; prefer over reel page load | Yes — covers, showcase, comment mining |
| Showcase semantics thin | `veedcrawl_extract` | **10 calls** | Yes — does not replace profile gate |

**Tool response shape.** Discovery tools return a persist envelope. Read business
data from `response`; use `cache_hit`, `persisted`, and `api_calls` in run
diagnostics. If `persisted: false` or `ok: false`, fall back to browser for that
signal — do not treat as success.

**Order of operations:** run browser discovery first; veedcrawl search/profile
calls may interleave with browser work on **different** handles, but never open
parallel `browser_navigate` sessions or fan out multiple browser tabs in one run.
Then optionally enqueue handles from `veedcrawl_search_social_videos` (log as
`veedcrawl_search:<q>` in `attempted_angles`). Per handle: optional
`veedcrawl_instagram_profile` → **mandatory** `browser_navigate` profile → reel
scoring. Pass `identity_id` + `env` on profile/metadata/extract when the handle
is already ingested so CAL gets `identity.veedcrawl_*` index facts.

When veedcrawl is unavailable, continue with **pure browser** — do not abort.

**Canonical tool calls (copy args exactly — never invoke with `{}`).**
Empty-arg calls are blocked by the plugin hook and waste iterations.

1. **Seed expansion** — `veedcrawl_search_social_videos` (≤3/run):
```json
{"q": "cozy living room makeover instagram reels", "platform": "instagram", "limit": 12}
```
```json
{"q": "first apartment tour furniture NA creator", "platform": "instagram", "limit": 12}
```

2. **Pre-screen profile** — `veedcrawl_instagram_profile` (prefer over `veedcrawl_profile` for IG):
```json
{"username": "kathypicos", "limit": 12, "env": "LIVE"}
```
When the handle is already ingested, add CAL attribution:
```json
{"username": "kathypicos", "limit": 12, "env": "LIVE", "identity_id": 42, "handle": "kathypicos"}
```

3. **Reel stats** — `veedcrawl_metadata` (prefer over loading the reel page):
```json
{"url": "https://www.instagram.com/reel/ABC123xyz/", "env": "LIVE", "identity_id": 42, "handle": "kathypicos"}
```

4. **Showcase gap** — `veedcrawl_extract` (≤10/run; **both** `url` + `prompt` required):
```json
{
  "url": "https://www.instagram.com/reel/ABC123xyz/",
  "prompt": "Does this Reel show furniture placement, room styling, or a large home product demo? Reply with scene type, product category if visible, and on-camera demo quality (1-10).",
  "env": "LIVE",
  "identity_id": 42,
  "handle": "kathypicos"
}
```

5. **TikTok cross-check only** — `veedcrawl_profile` (IG handles use #2 instead):
```json
{"platform": "tiktok", "username": "creator_handle", "limit": 12, "env": "LIVE"}
```

See `references/veedcrawl-tools.md` for parameters, cache semantics, and failure examples.

Lateral expansion from seed results is capped at **3 hops**. One failed hashtag, browser session, selector, or extraction call never ends the run; switch surface or seed.

## Persistence And Run
Do not stop at the first acceptable candidate. Continue until each priority product feature / selling-point group has a defensible creator set, or all relevant surfaces are exhausted.

**Immediate persistence rule (hard).** To reduce long-context drift and
memory contamination, persistence is **streaming, one candidate at a
time**:

1. Qualify one handle from on-page evidence.
2. Immediately persist that single handle via `ingest-confirmed-candidate`
   (nested JSON per `references/bridge-cli-json-payloads.md`) before moving
   to the next profile.
3. After the write succeeds, treat the candidate as "persisted state"
   and continue browsing; do **not** keep unpersisted candidate queues in
   memory across many profiles.

Forbidden pattern: browsing/LLM-summarizing 5-20 candidates first and
batch-writing at the end of the run. If a run crashes midway, all
already-qualified candidates must already be durable in CAL.

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
pending_ingests:
  - "<handle> — <why not ingested; e.g. iteration_limit, json_validation, bridge_error>"
  - ...
```

**Do NOT** use a prose-only `### Next round should:` numbered list — the console parser only reads the YAML field names above.

**`pending_ingests` rules** (required whenever you qualified a handle but did not complete `ingest-confirmed-candidate`):
- List every qualified-but-not-ingested handle before ending the run (iteration limit, JSON shape error, bridge down, etc.).
- Format: `"<handle> — <one-sentence why ingest did not complete>"`.
- Max **5 items** (composer cap). Put ingest work here; put **new exploration** leads in `next_round_focus` (a handle can appear in both if it still needs ingest AND more reel review).
- Cross-run: `/tmp/ingest_*.json` is **not** preserved — the next round rebuilds JSON from profile evidence.

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

These fields feed the rediscover brief composer; round N+1 reads them from `# prior_runs` and `# resume_directives` (see **Prior runs handling**) and avoids re-tracing exhausted angles or losing pending ingests. Omitting them silently degrades subsequent auto-retries.

**Quantity floor (hard).** When the brief carries `discovery_target_count` or `additional_target_count`, treat it as a HARD FLOOR on PERSISTED candidates (visited via `browser_navigate`, then qualified, then successful `ingest-confirmed-candidate`). The console's quantity gate compares your persisted count against the floor immediately after this run terminates. If you are short of the floor AND auto-retry budget remains, the backend AUTO-FIRES the rediscover skill again (up to 5 auto-retries total = 6 runs max); after that, the operator gets a `discovery_floor_unmet` escalation. Stopping short is therefore a failure mode — finishing partial is acceptable only when truly blocked (rate limits, niche exhausted, IG checkpoint). When stopping short, you MUST set `floor_unmet_reason` (one-sentence why) in the structured diagnostics block above so the backend can decide between auto-retry and early escalation; `attempted_angles` is already mandatory regardless.

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
- when `veedcrawl_metadata` is in your toolset, prefer it for per-Reel view/like/date facts (read `response` from the envelope); on `persisted: false` or missing fields, fall back to `browser_navigate` on the Reel URL plus `browser_console`/`browser_vision`. Do not abort the run because veedcrawl is unavailable;
- use `veedcrawl_extract` when showcase scoring still lacks semantic signal after metadata + browser covers/captions (on-demand during Reel review, ≤10/run). Also honor explicit operator requests for paid extraction.

**Partial Reel-cover load is acceptable (soft).** IG's Reel grid thumbnails frequently fail to render for transient reasons (CDN flakes, lazy-load delays, IG throttling, viewport virtualization) — this is normal and does NOT mean the candidate is unjudgeable. Rules:
- Judge showcase fit from whatever covers DID load. **6+ visible covers out of 12-15** is enough to assess content theme, scene fit, and on-camera style; do not gate qualification on a full grid.
- Before declaring "no signal", do ONE scroll + re-snapshot to give lazy-load a chance. If still empty, try `browser_get_images` to pull whatever the page has cached.
- Only abandon a candidate as unjudgeable when **zero** Reel covers render after that one retry AND captions/alt-text are also empty. In that case skip the candidate and move on — do NOT escalate to `mode_gate_blocked` or count this as a surface failure.
- Do not penalize a creator's Showcase Score for a partial grid; score from the covers you can see and note "partial grid" in evidence if it affected sample size.

**Anti-fabrication rule (hard).** Every handle you place into the orchestrator's `shortlist_ready` `candidates` array MUST be a handle that you actually visited via `browser_navigate("https://www.instagram.com/<handle>/")` earlier in the same run, with on-page evidence supporting the numbers you write into `audience_fit`, `engagement_quality`, `niche_match`, and `reason`. Generic-sounding placeholders (`home_style_lover`, `minimalist_home`, `cozy_living_xx`, `test_kol_*`) are red flags; if you cannot point to the corresponding `browser_navigate` call, omit the handle. It is better to return fewer real candidates (or invoke the orchestrator's zero-results escape hatch after at least 3 distinct surface visits) than to invent any.

**Confirmed-candidate ingest (hard).** After you confirm one qualified handle,
persist it immediately through the deterministic Bridge endpoint — do NOT
hand-roll three separate CLI calls and do NOT batch multiple handles.

Preferred path (direct ingest):

```bash
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py ingest-confirmed-candidate \
  --campaign-id <campaign_id> --env <env> \
  --json @/tmp/ingest_<handle>.json
```

`/tmp/ingest_<handle>.json` shape (full anti-patterns and field map:
`references/bridge-cli-json-payloads.md`):

```json
{
  "env": "LIVE",
  "source": "skill:instagram-kol-discovery",
  "ingest_id": "<uuid-or-stable-id>",
  "identity": {
    "primary_handle": "<handle>",
    "platform": "instagram",
    "display_name": "<optional>",
    "primary_email": "<optional x@y.tld only>"
  },
  "candidate": {
    "source": "discovery:profile_verification",
    "discovery_score": 82,
    "payload": {
      "evidence_url": "https://www.instagram.com/<handle>/",
      "followers": "220K",
      "reason": "..."
    }
  },
  "identity_facts": {
    "identity.instagram_profile_url": "https://www.instagram.com/<handle>/",
    "identity.instagram_profile_url_source": "ig_bio",
    "identity.instagram_profile_url_discovered_at": "<iso8601>",
    "identity.instagram_profile_url_discovered_url": "https://www.instagram.com/<handle>/",
    "identity.hero_post_url": "https://www.instagram.com/reel/<shortcode>/",
    "identity.hero_post_url_source": "ig_reel_pick",
    "identity.hero_post_url_discovered_at": "<iso8601>",
    "identity.hero_post_url_discovered_url": "https://www.instagram.com/<handle>/",
    "identity.hero_post_note": "<one-sentence why this reel is representative>",
    "identity.hero_post_note_source": "llm_summary",
    "identity.hero_post_note_discovered_at": "<iso8601>",
    "identity.hero_post_note_discovered_url": "https://www.instagram.com/reel/<shortcode>/"
  }
}
```

Fallback path (when Bridge ingest endpoint is temporarily unavailable):

```bash
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py buffer-confirmed-candidate \
  --campaign-id <campaign_id> --env <env> \
  --json @/tmp/ingest_<handle>.json
```

Then replay buffered rows:

```bash
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py replay-ingest-buffer --limit 50
```

## Bridge CLI (gateway runs)

- Use absolute **`kol-bridge-cli`** from the gateway brief `# terminal_safety`
  block (terminal cwd is often `$HOME`). From `hermes-agent/` repo root in a
  local shell, `python3 plugins/kol-ops-bridge/scripts/kol_bridge_tool.py` works.
- CLI failures emit JSON on **stdout**; empty output + exit 2 → read stdout for
  `error`/`hint` — never `execute_code` + subprocess.

Rules:
- One handle per ingest call; never accumulate unpersisted candidates in memory.
- **Never** batch ingest via `execute_code` loops over `/tmp/ingest_*.json` files.
  Use **terminal** + `ingest-confirmed-candidate` immediately after each handle qualifies.
- If `identity.hero_post_note` is present, include its provenance triple
  in the SAME write:
  `identity.hero_post_note_source`,
  `identity.hero_post_note_discovered_at`,
  `identity.hero_post_note_discovered_url`.
- `identity.hero_post_url` MUST be canonical `/reel/<shortcode>/` or `/p/<shortcode>/` (no `/<handle>/reel/...`, no query/fragment).
- `identity.hero_post_url_discovered_url` MUST be the creator's own profile URL and match `identity.primary_handle`.
- Do not confuse top-level `source` with identity fact provenance:
  - top-level `source` is the ingest workflow source (e.g. `skill:instagram-kol-discovery`);
  - each `identity.*_source` field must be one of:
    `google_search_result`, `linktree`, `ig_bio`, `facebook_about`,
    `fb_creator_profile`, `personal_site`, `media_kit`, `agency_page`,
    `ig_profile_and_reels`, `ig_reel_pick`, `llm_summary`.
- Every `identity.*_url` value must be an absolute `http(s)` URL.
- `identity.linktree_url` accepts only:
  `linktr.ee`, `beacons.ai`, `bio.link`, `lnk.bio`, `solo.to`, `linkin.bio`.
  If the host is outside this list (e.g. `msha.ke`), either write it to
  `identity.personal_site_url` when creator-owned, or omit the field.
- Optional-field retry policy: when ingest fails on an optional field
  format, remove/fix that field and retry the same handle immediately;
  do not keep guessing alternate string formats across multiple retries.
- If ingest returns validation errors, fix payload and retry the same handle before moving on.

**IG profile URL persistence (included in ingest payload).** Every handle that survives qualification has by definition been visited at `https://www.instagram.com/<handle>/`. Include profile URL + creator brief facts in the same `identity_facts` object passed to `ingest-confirmed-candidate` (legacy separate `upsert-identity` → `write-facts-multi` → `add-candidate` chain is deprecated for this skill).

Legacy reference (do not use in new runs):

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
- `campaign_id: null` in `identity_facts` scope is enforced server-side (identity-level reusable facts).
- **Do NOT overwrite a non-empty existing value.** The ingest endpoint skips existing identity facts automatically.
- This write is mandatory for every qualified candidate, not only final shortlist members.

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
| `identity.hero_post_url` | `str`, single Reel URL | The single best Reel for this product fit (highest views *and* clearest theme match). MUST be canonical `https://www.instagram.com/reel/<shortcode>/` (or `/p/<shortcode>/`), never `/<handle>/reel/...` |
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

Before writing `identity.hero_post_url`, do a canonicalization check:
- open the candidate Reel URL once;
- read `window.location.href`;
- persist that final canonical URL only when it is `https://www.instagram.com/reel/<shortcode>/` or `/p/<shortcode>/` with no query/fragment.
If the resolved URL includes `/share/`, a handle-prefixed path, query params, or bounces to a different structure, reject it and pick another Reel.

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

## Local Chrome (`browser_*` tools)

All Instagram discovery uses **local debug Chrome** through built-in
`browser_*` tools. The runtime attaches via CDP (`local-chrome-tab-pool`);
on first call it auto-launches debug Chrome via `start-debug-chrome.sh` if
CDP is not already up. Isolated profile: `~/.hermes/local-chrome-debug-profile`.

**Forbidden:** Browser Use cloud, `mcp_chrome_devtools_*`, `web_search`,
`web_extract`, terminal HTTP scraping. Tool names are exactly
`browser_navigate`, `browser_snapshot`, `browser_click`, `browser_console`,
`browser_get_images`, `browser_type`, `vision_analyze`.

If `browser_navigate` raises that local debug Chrome could not be reached
or auto-started, stop the run with `mode_gate_blocked: browser_unavailable`
and tell the operator to run `playground/local-chrome-debug/start-debug-chrome.sh`,
log into Instagram once in that profile, then retry.

### Pre-flight gate (mandatory)
First tool call of the run, before any IG URL:
1. `browser_navigate("https://ipinfo.io/json", timeout=30)`
2. `browser_console(expression="document.body.innerText")` — parse JSON, read `country`.
3. If `country != "US"` → stop immediately and return `mode_gate_blocked: non-US exit (got <country>)`. Do not navigate to instagram.com.
4. If `country == "US"` → log the org/IP in the run report and proceed.

### Conservative rules (main-account protection)
The agent operates the user's Instagram session in debug Chrome. Treat every
action as visible to IG's risk system.

- **Pacing**: random `2-4s` pause between candidate profiles; `1-2s` between reels within the same profile. No concurrent profile/reel browsing.
- **Per-run caps**: at most **40 distinct profiles** and **200 reel page loads** per invocation. On hitting either cap, stop and deliver partial results.
- **Forbidden actions**: `follow`, `unfollow`, `like`, `save`, `comment`, send DM, `share`, `subscribe`, any form submission, any login-page interaction. Read-only navigation, snapshots, `browser_console` extraction, and scrolling are allowed.
- **Login assumption**: user already logged into IG in the debug-Chrome profile. Never navigate to auth flows or type credentials.
- **Risk-page response**: checkpoint, captcha, "Action blocked", etc. → stop with `mode_gate_blocked: rate_limited`. Do not refresh or retry.
- **Metadata preference**: when `veedcrawl_metadata` is available, prefer it over loading reel pages in the user's browser.

### Browser reliability rules

- **Always re-snapshot after navigation**: any `browser_navigate`, `browser_click` that changes route, or reload **invalidates all `@eXX` refs**. Call `browser_snapshot` before the next click/type.
- **Stop retrying the same call**: if `browser_navigate` to the same URL fails twice, switch tactic. `same_tool_failure_warning` at count=3 is a hard stop.
- **Default navigation timeout is fine** for local Chrome — don't pad `timeout` unless you have evidence of slow loads.
- **Retry tactic order**: (a) snapshot → scroll once → snapshot for virtualized lists, (b) try public/non-login URL variant, (c) **move on to next candidate**. Never run `cleanup_browser` (drops the attached CDP session). Never repeatedly hit the same profile.
- **CDP lost**: if CDP truly drops, surface `mode_gate_blocked: cdp_lost` and stop — operator re-runs the launcher script.
- **Element-not-found** → one snapshot/scroll/snapshot retry, then skip the candidate.

## References
- `references/bridge-cli-json-payloads.md` — exact kol_bridge_tool JSON field names and per-candidate persistence order for rediscovery runs.
- `references/veedcrawl-tools.md` — plugin tool names, persist envelope, monthly cache, per-run budgets.
- `references/veedcrawl-api.md` — REST endpoints (search/profile/metadata/extract), MCP vs plugin.

## Pitfalls
- Do not call `delegate_task` to batch discovery (e.g. "search public web for 150 handles"). Subagents lose the campaign tab-pool session, loop on empty `veedcrawl_*` args, and block the parent run for minutes. Browse and persist in the current run; let the console auto-fire `/rediscover` when the quantity floor is unmet.
- For bridge CLI persistence, do not guess JSON keys per subcommand. `upsert-identity` expects `primary_handle`; `write-facts-multi` should be called with `--identity-id`; `add-candidate` is safest with `identity_id` already embedded in the JSON payload. Prefer file-backed `@/tmp/*.json` payloads.
- To inspect persisted candidate handles/counts, call `list-candidate-handles --env <TEST|LIVE> --campaign-id <id> --plain`; do not pipe `list-candidates` through generated `python -c` snippets.
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
- Do not use Veedcrawl search/profile as the only discovery path — browser surfaces are mandatory.
- Do not call `mcp_veedcrawl_*` or bypass plugin tools for discovery.
- Do not ignore `persisted: false` on veedcrawl tool results — fall back to browser.
- Do not call any `veedcrawl_*` tool with an empty object `{}` or missing
  required fields — the hook blocks it. Always pass the canonical JSON from
  **Veedcrawl supplement → Canonical tool calls** (at minimum: `q` for search,
  `username` or `url` for profile, `url` for metadata, `url`+`prompt` for extract).
- Do not call `veedcrawl_extract` without both `url` + `prompt` (on-demand showcase gap or explicit operator request).
- Do not use `veedcrawl_profile` for Instagram when `veedcrawl_instagram_profile` is available — use `{"username": "<handle>"}`.
- **Local Chrome — never** issue a `follow / like / comment / save / DM / share` action, even when a snapshot lists it as the easiest-looking element. The skill is read-only on the main account.
- **Local Chrome — never** retry a URL that returned a checkpoint/captcha; never refresh hoping it resolves. Stop the run instead.
- **Local Chrome — never** skip the `ipinfo.io` pre-flight, even if the previous run in the same hermes session passed it (VPN state can flip mid-session).