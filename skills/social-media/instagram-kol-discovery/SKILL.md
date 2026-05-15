---
name: instagram-kol-discovery
description: Discover qualified North American home/lifestyle KOLs on Instagram via hashtag search, comment mining, following-graph expansion, or public web search fallback; extract content with veedcrawl and filter by hard criteria.
trigger: When user asks to find Instagram KOLs/influencers for a home/furniture/lifestyle product, scrape hashtag results, mine commenters or followings of an existing creator, qualify an Instagram account against KOL criteria, or use public curated lists when proxy access is unavailable.
tags: ["instagram", "kol", "influencer", "home decor", "furniture", "veedcrawl", "cloud browser"]
---

## Goal
Find Instagram **individual creators** who fit the brand's KOL profile (mid-to-high-end home & living: sofas, tables, chairs, cabinets) using three complementary discovery channels plus a fallback method, then qualify each candidate against hard criteria before delivering a final shortlist.

**Business objective**: Identify potential collaborators who can produce sponsored home-product promotional videos (Reels/short-form) on our behalf. Priority is given to personal bloggers with authentic lifestyle content; accounts operated by organizations, agencies, media companies, or brands are excluded. Accounts that compete directly with our product category are also excluded.

## Hard Qualification Criteria (ALL must be met)
| # | Criterion | Threshold | How to verify |
|---|-----------|-----------|---------------|
| 1 | Region | North America (US / CA) | Bio contains a US/CA city, state, country name, or 🇺🇸/🇨🇦 flag emoji. Bio signal alone is sufficient. |
| 2 | Followers | ≥ 100,000 | Read from profile header. |
| 3 | Niche | Home, interior, furniture, lifestyle, decor | Bio keywords + last 10–15 posts visually about home/interior/furniture/lifestyle. Reject pure fashion/food/travel/tech. |
| 4 | Avg. views (recent) | ≥ 30,000 | Average the view counts of the **most recent 10–15 reels/videos**, **excluding any posted within the last 72 hours** (data not yet stabilized). |
| 5 | Engagement rate | ≥ 3% | Formula: `(likes + comments) / views`, computed per video then averaged across the same 10–15 sample. |
| 6 | Account type | Individual personal blogger | Bio/about section shows a real person's name; no agency/studio/media/brand language. Profile picture shows a person (not a logo). Account is not verified as a business entity. Discard if the account clearly represents a company, media outlet, or talent agency. |
| 7 | No competitor conflict | Account must not sell, manufacture, or primarily promote competing home furniture/decor products | Check bio for shop links, brand handles, or "founder of" language pointing to a competing furniture/home goods brand. Check recent posts for recurring product promotion of a competing brand. Discard if a clear commercial conflict exists. |

When mining commenters as candidates, only enqueue accounts already showing **≥ 100k followers** on the hover/profile preview. Do NOT keep nano/micro candidates.

## Discovery Channels

### Channel A — Hashtag search (seed discovery)
Default seed hashtags for a mid/high-end furniture & home brand (use unless user specifies others):
`#homedecor` `#interiordesign` `#homeinspo` `#moderninteriors` `#livingroomdecor` `#furnituredesign` `#homestyling` `#cozyhome` `#apartmenttherapy` `#interiorinspo`

Navigate directly to the hashtag results page (avoids the search-box redirect bug):
```
https://www.instagram.com/explore/search/keyword/?q=%23<tag-name>
```
Replace `<tag-name>` with the hashtag (omit `#`).

### Channel B — Comment mining (lateral discovery)
On a qualified KOL's top-performing recent reel:
1. Open the reel and scroll the comments panel.
2. For each commenter whose username badge / quick-preview suggests a creator account, hover or open the profile in a new context.
3. Enqueue only if profile header shows **≥ 100k followers**. Otherwise discard immediately (do NOT keep micro/nano).

### Channel C — Following / Suggested graph (lateral discovery)
On a qualified KOL's profile:
1. Open the **Following** list (or the "Suggested for you" sidebar on their profile).
2. Scan usernames + profile pictures; open accounts that look like home/lifestyle creators.
3. Apply the same ≥ 100k filter before enqueuing.

### Channel D — Public web search fallback (proxy-unavailable fallback)
When direct Instagram access is blocked/timing out due to missing residential proxies:
1. Search public curated lists with keywords like "top North American home decor Instagram influencers 2026", "best interior design KOLs US Canada 100k+ followers", "home lifestyle Instagram creators to collaborate with"
2. Extract usernames from reliable lists (marketing agency roundups, industry blogs, influencer directory sites)
3. Cross-verify against the same qualification criteria before enqueuing

### Expansion depth
Lateral expansion (B + C) is allowed up to **3 hops** from any seed hashtag result. Track the hop count per candidate; stop expanding from a node once it reaches hop 3 to prevent runaway crawling.

### Pre-curated Seed List
A verified list of 10 high-authority North American home/lifestyle KOLs is available at `references/north-america-home-kol-seeds.md` for use as seed candidates when hashtag crawling is blocked by anti-scraping measures.

### Channel D — Similar accounts fallback (when hashtag search fails due to anti-scraping/network issues)
If hashtag search pages (Channel A) fail to load or time out repeatedly:
1. Start with known qualified home/lifestyle KOL profiles as default seeds (e.g. @justinablakeney, @thejungalow; full list at `references/default-seed-kols.md`)
2. Click the "Similar accounts" button on the profile to open the recommended creators list
3. Apply the same ≥100k followers filter before enqueuing candidates
4. Expansion depth allowed up to 5 hops from the initial seed profile.

## Workflow

1. **Seed phase** — Run Channel A on each seed hashtag (or Channel D if proxy is unavailable). Collect post/reel URLs of high-performing content (visibly high view counts in the grid) or curated creator usernames from public lists.

2. **Extract direct URLs** — For each target post/reel:
   a. Click the post/reel to open the modal.
   b. Run `browser_console(expression="window.location.href")` to capture the canonical URL.
   c. **Close the modal using the in-page close (×) button.** Only if no close button is present, fall back to `browser_back`. Never use `browser_back` as the default — it frequently lands on `about:blank` or the IG home feed and forces a full re-navigation.

3. **Profile qualification** — For each unique creator behind those posts or from public lists:
   a. Open their profile.
   b. Read followers, bio (region + niche).
   c. **Take a screenshot of the profile page** (grid view of post thumbnails) to visually assess the creator's video cover style, content type, and overall aesthetic. This gives a quick gestalt signal on niche fit before investing in per-reel analysis.
   d. If followers ≥ 100k AND region = NA AND niche fits → proceed to step 4. Else discard.

4. **Performance qualification** — On the qualified profile's Reels/Posts tab:
   a. List the 10–15 most recent reels.
   b. Drop any posted within the last 72 hours.
   c. For each remaining reel, call `veedcrawl_metadata(url=<reel_url>)` to retrieve structured metrics (views, likes, comments, publish time) at zero cost. Fall back to reading metrics from a page screenshot only if `veedcrawl_metadata` returns no data for that URL.
   d. Compute `avg_views = mean(views)` and `avg_engagement = mean((likes+comments)/views)`.
   e. Keep only if `avg_views ≥ 30,000` AND `avg_engagement ≥ 3%`.

5. **Lateral expansion** — For each KOL that passes step 4, run Channel B and Channel C to enqueue new candidates (respect the 3-hop cap and the ≥ 100k comment-mining filter). Loop back to step 3 for new candidates.

6. **Content extraction** — Apply the following three-tier approach in order:

   **Tier 1 — Screenshot (always, zero cost):** Take a screenshot of the profile grid and individual reel pages to capture cover images, post titles, and visible metrics. Sufficient for style/niche assessment and basic stats.

   **Tier 2 — Metadata fetch (always for qualified KOLs, zero cost):** Call `veedcrawl_metadata(url=<reel_url>)` for every qualified KOL's recent reels to retrieve structured data (caption, like/comment/share counts, hashtags, publish time, video duration) without downloading video. This is a mandatory step — run it on all qualified KOLs regardless of whether deep analysis is requested.

   **Tier 3 — Full extraction (only on explicit user request, has cost):** Call `veedcrawl_extract()` only when the user asks for deep content mining (e.g., spoken content, visual scene analysis, product placement detection). Both parameters are required:
   - `url`: full direct Instagram reel/post URL
   - `prompt`: e.g. *"Extract this Instagram video's caption, hashtags, spoken/visual content summary, likes, comments, shares, and any product mentions, brand tags, or calls to action."*
   
   Do NOT call `veedcrawl_extract` speculatively — only invoke it when the user explicitly needs content-level intelligence beyond what metadata and screenshots provide.

7. **Deliver results** — Return a Markdown table:

   | Username | Profile URL | Followers | Avg Views (10–15 reels, >72h old) | Engagement Rate | Region | Match Reason |
   |---|---|---|---|---|---|---|
   | @example | https://instagram.com/example | 245k | 58,400 | 4.2% | Brooklyn, NY 🇺🇸 | Mid-century furniture styling, frequent sofa/cabinet content |

   Sort by engagement rate descending. Include a separate short list of "discarded" candidates with the failing criterion, so the user can audit the filter.

## Cloud Browser Operation Principles
- **Session setup (Browser Use)**: Set `BROWSER_USE_API_KEY` for direct API access. Optionally set `BROWSER_USE_PROFILE_ID` to attach a persistent browser profile that carries login cookies/local storage across sessions — useful for staying logged in to Instagram across multiple crawl runs. The Browser Use `/browsers` endpoint does **not** support `keepAlive`; to reuse a session across tasks, keep the session alive by not stopping it between actions rather than relying on an API flag. Proxy is included by default (`proxyCountryCode: "us"`), which provides basic bot-detection evasion — do not disable it for Instagram crawls.
- **Act decisively**: Issue each action (click, navigate, scroll) as soon as the target element is visible. Do not wait for animations to fully settle unless the next action depends on their result.
- **Minimal delays**: Add a wait only when strictly needed (e.g., waiting for a reel list to load after scrolling, or for a modal to open). Keep waits short — 1–2 seconds is usually enough; never idle for more than 3–5 seconds without a concrete reason.
- **No redundant checks**: Do not re-screenshot or re-snapshot the same state you just confirmed. Move to the next step immediately.
- **Fail fast**: If an element is not found within one retry, skip that item and continue with the queue rather than stalling the session.

## Pitfalls
- ❌ Do NOT use `browser_back` as the primary way to leave a post — prefer the in-modal × close button. `browser_back` often lands on `about:blank` or the IG home feed, forcing a full re-navigation to the hashtag URL.
- ❌ Do NOT include reels posted within the last 72 hours in the average — early-life view/like ratios distort the mean.
- ❌ Do NOT keep commenters with < 100k followers as KOL candidates, even if they look on-brand.
- ❌ Never call `veedcrawl_extract` without both `url` and `prompt` — both are required. And only call it when user explicitly requests deep content analysis; it has a cost.
- ❌ Do NOT expand laterally beyond 3 hops from a seed hashtag — crawl will explode.
- ⚠️ Browser Use includes a US residential proxy by default (`proxyCountryCode: "us"`). Do NOT disable the proxy for Instagram crawls — it is the primary anti-detection measure. For stricter bot detection scenarios, consider passing a `customProxy` pointing to a dedicated residential proxy service (requires an active Browser Use subscription).
  - Workaround for non-proxy access: Skip hashtag crawling entirely; use known high-authority home/lifestyle KOL profiles as seeds, then use Instagram's built-in "Similar accounts" feature for discovery, which has far lower bot detection rates than hashtag search.
  - Workaround for repeated timeout failures: If Instagram pages time out 3+ times consecutively, pause crawling and request the user to enable residential proxies, or fallback to using publicly curated home/lifestyle KOL recommendation lists as seed candidates for verification. If user explicitly requests browser-only access, retry failed page loads once with extended timeout before offering fallback options.
- ⚠️ Instagram UI selectors for Reel likes/comments/views are periodically updated; if automated JS extraction returns 0 values, manually extract metrics from page snapshots instead of relying on selectors. If proxy access is unavailable, use Channel D (public web search fallback) instead of direct Instagram crawling to avoid timeouts and blocks.
- ⚠️ Bio region check is intentionally lenient (city / state / flag emoji acceptable). If bio is empty, treat region as **unknown** and discard rather than guess from content.
- ⚠️ Engagement rate uses `(likes + comments) / views`, NOT `/ followers`. Keep the formula consistent across all candidates for fair ranking.
- ⚠️ Default cloud browser without residential proxies may fail to load Instagram hashtag search pages consistently; use Channel D as a fallback when hashtag search times out repeatedly. Direct profile URLs usually load successfully even when search pages are blocked.