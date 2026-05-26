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
| Self-commerce (furniture only) | NOT a furniture seller themselves — DISCARD if bio, link-in-bio (Linktree/Beacons/Stan etc.), pinned posts, or any of the last 10-15 Reels promote their own furniture brand, furniture DTC store, furniture dropshipping, or a persistent furniture storefront (e.g. Amazon shop / LTK / Shop My where furniture is a recurring category, not a one-off affiliate post). Non-furniture self-commerce (fashion / beauty / food / kitchenware / decor accessories / tech / pet) does NOT trigger this rule — those creators are fine and often better at branded-content execution. |
| Operator do-not-contact list | DISCARD if the handle appears in the bridge's do-not-contact set (operators flag accounts as `competitor` etc. via the console's archive modal). Fetch the set ONCE at run start (see **Pre-discovery do-not-contact pull** below) and match candidate handles against it before any per-profile qualification. |
| Competitor deals | no active exclusive direct competitor deal; past one-off competitor collab is a positive flag |
| Scores | Match ≥ 70 and Showcase ≥ 50 |

## Pre-discovery do-not-contact pull
Before the first seed search, pull the operator-maintained do-not-contact list ONCE per run from the bridge and keep it in memory for the rest of the run. These are accounts that operators have manually archived via the console with reasons like `competitor` (self-selling furniture, brand-owned account, etc.) — they must never reappear in a shortlist regardless of how good their content looks.

```bash
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py list-relationships \
  --env LIVE --last-outcome competitor --limit 1000
```

Extract `items[*].primary_handle` (lowercased) into a set. Repeat for any other do-not-contact outcomes if the operator adds new ones (today: only `competitor`). During qualification, before issuing `browser_navigate` to a candidate profile, lowercase-compare the handle against this set — if matched, log a one-line skip and move on. **Do not** spend tool turns measuring views/ER on a do-not-contact handle.

If the bridge call itself fails (network, auth), do NOT silently proceed with an empty list — that would defeat the operator gate. Report `do_not_contact_pull_failed: <reason>` and either retry once or stop the run; treat it like a pre-flight gate failure.

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

**Quantity floor (hard).** When the brief carries `discovery_target_count` or `additional_target_count`, treat it as a HARD FLOOR on PERSISTED candidates (visited via `browser_navigate`, then qualified, then `add-candidate`). The console's quantity gate compares your persisted count against the floor immediately after this run terminates. If you are short of the floor AND auto-retry budget remains, the backend AUTO-FIRES the rediscover skill again (up to 3 auto-retries total = 4 runs max); after that, the operator gets a `discovery_floor_unmet` escalation. Stopping short is therefore a failure mode — finishing partial is acceptable only when truly blocked (rate limits, niche exhausted, IG checkpoint). When stopping short, your final answer MUST contain:

```
floor_unmet_reason: <one-sentence why>
attempted_angles:
  - <keyword/angle 1>
  - <keyword/angle 2>
  - <keyword/angle 3>
```

so the backend can decide between auto-retry and early escalation.

Minimum evidence when reachable:
- review at least **3 High-Match candidates**;
- sample at least **2 discovery surfaces**;
- measure 10-15 recent Reels per qualified creator;
- run `browser_navigate` to every candidate's profile URL (`https://www.instagram.com/<handle>/`) at least once in this run — this is the hard registration gate the orchestrator skill enforces before allowing `shortlist_ready`;
- use screenshots (`browser_snapshot` / `browser_vision`) and extract numbers via `browser_console(expression="...")` from the rendered page;
- when `veedcrawl_metadata(url=...)` is in your toolset, prefer it for per-Reel facts because it is free; when it is NOT in your toolset (e.g. the active agent profile has not enabled the veedcrawl plugin), fall back to `browser_navigate` on the Reel URL plus `browser_console`/`browser_vision` to read view counts, likes and dates. Do not abort the run because veedcrawl is unavailable;
- use `veedcrawl_extract(url=..., prompt=...)` only when the user explicitly requests paid/deep extraction.

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

## Pitfalls
- Do not default to home/decor creators just because the product is furniture.
- Do not let visual similarity outrank buyer intent for functional, technical, family-practical, or use-case products.
- Do not shortlist on Audience Match alone; Match ≥ 70 and Showcase ≥ 50 must both pass.
- Do not reject tech, gaming, comedy, entertainment, fashion, or lifestyle creators solely by niche.
- Do not keep creators who self-sell furniture (own brand, DTC, persistent furniture storefront like `mytexashouse`-style accounts) — they are direct competitors no matter how lifestyle-personal the feed looks. Always check bio, link-in-bio, pinned posts, and the last 10-15 Reels for recurring furniture-commerce signals. Self-commerce in other categories (fashion / beauty / food / kitchenware / decor accessories / tech / pet) does NOT trigger this rule.
- Do not overfit historical winners' surface style; reuse the conversion mechanism.
- Do not include Reels posted within the last 72h in averages.
- Do not keep commenters with < 100k followers.
- Do not call `veedcrawl_extract` without explicit request and both `url` + `prompt`.
- **Local Mode — never** issue a `follow / like / comment / save / DM / share` action, even when a snapshot lists it as the easiest-looking element. The skill is read-only on the main account.
- **Local Mode — never** retry a URL that returned a checkpoint/captcha; never refresh hoping it resolves. Stop the run instead.
- **Local Mode — never** skip the `ipinfo.io` pre-flight, even if the previous run in the same hermes session passed it (VPN state can flip mid-session).