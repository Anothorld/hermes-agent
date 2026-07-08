# kol-discovery-rpa

RPA tools for KOL Instagram discovery — structured IG profile/reels/comments
extraction via local-chrome tab-pool CDP, hard qualification gates synced from
`instagram-kol-discovery` skill, and optional Reel video download for content
evaluation.

## Why

The `instagram-kol-discovery` skill previously required 10-20 `browser_*`
LLM turns per handle (navigate, snapshot, scroll, console extraction, manual
follower counting). This plugin wraps those fixed-flow operations into one-shot
structured tools that return JSON with a `qualification` block — reducing
browser LLM turns to 1-2 per handle (50-70% token savings).

## Tools (13 total, phased rollout)

| # | Tool | Phase | Purpose |
|---|------|-------|---------|
| 1 | `rpa_check_ip` | 1 | US IP preflight (replaces `browser_navigate(ipinfo.io)`) |
| 2 | `rpa_precheck_handle` | 1 | Zero page-load exclusion_set/skip/cooldown precheck |
| 3 | `rpa_fetch_ig_profile` | 1 | Profile data + account country + followers/region qualification gates |
| 4 | `rpa_fetch_ig_reels` | 2 | Profile grid: 10 reels + thumbnails + `content_eval` plan |
| 5 | `rpa_fetch_google_serp` | 2 | Google SERP extraction |
| 6 | `rpa_download_ig_reel` | 2 | yt-dlp MP4 (single reel; video eval mode only) |
| 7 | `rpa_download_ig_cover` | 2 | Single cover (RPA thumbnail URL or yt-dlp fallback) |
| 8 | `rpa_download_ig_content` | 2 | Batch: 10 covers + random 3 videos from `content_eval` |
| 9 | `rpa_fetch_reel_comments` | 2/3 | Reel comments (evaluation + discovery modes) |
| 10 | `rpa_cleanup_reels` | 2 | Delete old MP4 + cover image files |
| 11 | `rpa_fetch_hashtag_candidates` | 3 | Hashtag explore |
| 12 | `rpa_fetch_similar_accounts` | 3 | Similar/suggested accounts |
| 13 | `rpa_fetch_following_list` | 3 | Following list |

Set `KOL_RPA_PHASE=1|2|3` to control how many tools are registered.
**Default is 2** (all discovery + content-eval tools implemented). Phase 1
exposes only `rpa_check_ip` / `rpa_precheck_handle` / `rpa_fetch_ig_profile`
and leaves the agent without reels/download tools — forcing veedcrawl
fallbacks. Phase 3 adds hashtag/similar/following (currently stubs).

## Hard Qualification Gates (synced from skill)

These constants in `internal/qualification_rules.py` are the **single source of
truth** — synced 1:1 with `instagram-kol-discovery` SKILL.md `## Roles And
Qualification` (L134-155). When the skill or bridge thresholds change, the
**same PR** must update this file + this README table + skill cross-reference.

| Criterion | Threshold | RPA enforcement |
|-----------|-----------|-----------------|
| Followers | >= 100k (K/M/万/亿 normalized) | `qualification.gates.followers` |
| Followers borderline | 100k-110k | Gate passes; `borderline=true` flag for Agent |
| Region | US / Canada; unknown = discard | `qualification.gates.region` |
| Reels activity | >= 5 Reels in last 90 days | `qualification.gates.reels_3mo` |
| Static-only | 0 reels → discard | `static_only_account` discard reason |
| Avg Reel views | >= 30k (excluding 72h) | `qualification.gates.avg_views_excl_72h` |
| Reel ER | >= 3% = (likes+comments)/views | `qualification.gates.reel_er` |
| Account type | individual, not agency/brand | `qualification.gates.account_type` (heuristic) |
| Furniture self-commerce | NOT a furniture seller | `qualification.gates.furniture_self_commerce` (heuristic) |
| Skip list / cooldown | exclusion_set precheck | `qualification.gates.exclusion_precheck` |

**Hard rule:** When `qualification.hard_discard=true`, the Agent MUST discard
the candidate and cannot override with learned criteria (skill L100-103
priority: HARD > learned > default).

## Video Eval Switch

| Switch | Mode | Screening combination |
|--------|------|-----------------------|
| OFF (default) | cover | 10 covers + 10 comments |
| ON | video | 10 covers + 3 random videos + 10 comments |

Priority: brief field `rpa_video_eval_enabled` > env `KOL_RPA_VIDEO_EVAL_ENABLED` > default OFF.

The `pre_tool_call` hook blocks `rpa_download_ig_reel` when OFF, and limits
to 3 downloads per candidate when ON.

## Anti-Scrape Strategy

- Uses local debug Chrome with real user profile (not headless)
- Pacing: 2-4s between profiles, 1-2s between reels (jitter)
- Per-run caps: 40 profiles, 200 reel page loads
- Risk detection: checkpoint/captcha/login-wall → stop run
- Read-only: no follow/like/comment/DM actions
- Fallback: RPA failure grants one-shot browser fallback token

## Profile Extraction Strategy

`rpa_fetch_ig_profile` reads follower/following/posts counts from the rendered
`<header>` DOM **first** (`extraction_source="dom_word"`), with a
locale-independent **structural** fallback (`extraction_source="dom_structural"`),
and finally `<meta>` tags (`meta_description` / `og_description`).

**Why this matters:** the local debug Chrome renders IG in the operator's locale
(e.g. `--lang=zh-CN` → `4.5万粉丝`, `2361帖子`, `3571关注`). An earlier
English-only regex (`\d+\s*followers`) silently returned `followers=0` +
`hard_discard` for every KOL on a non-English locale — even 600K-follower
accounts — because the rendered word was `粉丝`, not `followers`. The current
extractor matches the follower/following/posts word across en/zh/es/pt/fr/de/ja/ko,
and falls back to reading the three `<span>count</span>` stats by IG's global
header order `[posts, followers, following]` when the localized word isn't
recognised.

When the page shows a follower signal (any locale's word) but no count is
extracted on the first pass (SPA hydration race), `fetch_profile` settles ~1s
and re-evals once. If the count is still not extractable, it raises
`DomChangedError` so the agent's one-shot browser fallback token fires — instead
of silently discarding the candidate with `followers=0` + `region_unknown`.

`data.extraction_source` is returned in the tool payload (`dom_word` |
`dom_structural` | `meta_description` | `og_description` | `""`) for debugging.

## Account Location (账户所在地)

`fetch_profile` reads the authoritative account country from the header `...` →
`账户简介` / `About this account` dialog (gated by `include_account_location`,
default True). The dialog renders the account's country (e.g. `账户所在地 美国`),
date joined, and verification date — far more reliable than guessing region from
bio text (a `📍SoCal` bio line resolved to `region_unknown` because `socal`
matched no city pattern, while the dialog states `美国` = US directly).

Returned fields:
- `data.account_location` — raw country name in the page locale (e.g. `美国`).
- `data.account_country` — ISO code (`US`, `CA`, `GB`, `AU`, ...). US/CA pass the
  region gate; others → `region_unknown` discard (correct for non-allowed regions).
- `data.account_date_joined` / `data.account_verified_date` — bonus operator info.

The account country is **prepended** to the region signals so it wins over
bio-derived guesses (e.g. a bio `ca` substring that would falsely resolve to
Canada is overridden by the dialog's `US`). When the dialog flow fails (button
not found, account is your own, etc.) the region gate falls back to bio signals.

**Cost:** the dialog flow adds ~2-3s + 2 clicks per profile. Set
`include_account_location=False` for faster batch runs where region can be
inferred from bio.

## Locale-Awareness Across RPA Tools

The debug Chrome renders IG in the operator locale, so every RPA extractor that
word-matches must be locale-aware — not just `rpa_fetch_ig_profile`:

| Tool | Field | Locales covered |
|------|-------|-----------------|
| `rpa_fetch_ig_profile` | followers/following/posts | en/zh/es/pt/fr/de/ja/ko + structural fallback |
| `rpa_fetch_ig_profile` | account country | en/zh/es/fr/de/ja/ko (+ GB/AU/DE/FR/ES/IT/JP/KR/MX/BR) |
| `rpa_fetch_ig_reels` | reel views | en/zh/es/fr/ja/ko, both "num word" and "word num" layouts |
| `rpa_fetch_ig_reels` | reel thumbnail_url | CSS `background-image` on `<div>` (current bloks layout), `<img>` fallback, `<video>` poster fallback |
| `rpa_fetch_reel_comments` | reel likes + comment count | en/zh from og:description prefix (`N likes, N comments` / `N 次赞，N 条评论`) |
| `rpa_fetch_reel_comments` | comment text + likes | en/zh/ja/ko/es; `/reel/` auto-rewritten to `/p/` for inline comments |
| `rpa_fetch_google_serp` | — | h3-anchored extraction + Google-domain filter + `/url?q=` unwrap; locale-independent |
| `rpa_check_ip` | — | ipinfo.io JSON (locale-independent) |

## SPA Hydration Timing

Several IG surfaces hydrate their content AFTER the navigate readyState check
passes. Each affected tool now settles before its first eval:

| Tool | Issue | Fix |
|------|-------|-----|
| `rpa_fetch_ig_profile` | follower counts populate after shell render | ~1s settle-retry when a follower signal is present but no count extracted |
| `rpa_fetch_ig_reels` | reels grid renders ~1s after `/reels/` navigate | ~1s settle before first eval + settle around the lazy-load scroll retry |
| `rpa_fetch_reel_comments` | commenter handle links appear after the video player loads | ~1.2s settle before eval |

## Reel Comments Extraction (bloks DOM)

The reel page uses IG's "bloks" component system — there is no `<article>`,
`<ul>`, or `<li role="listitem">`, so the legacy selector-based comment
extraction returned 0 comments. The current extractor anchors on what IS
reliable on a bloks reel page:

- **Caption**: from `og:description`'s trailing quoted text (locale-stable;
  the `"<likes> likes, <N> comments - <author>，<date> : \\"<caption>\\""`
  format is consistent across locales).
- **Thumbnail**: from `og:image`.
- **Commenters**: `a[href="/<handle>/"]` profile links whose text matches the
  handle (excludes nav like "主页"/Home and the "已验证"/verified suffix). The
  post author (from the URL path) is excluded.
- **Comment text + likes**: parsed from `body.innerText`, slicing each comment's
  block from its handle to the next commenter's handle so likes don't bleed
  across comments. Text is captured between the time token and the first
  `回复`/`查看所有`/`次赞` marker; likes from `<N>次赞`/`<N> likes` in that block.

## Content Eval Plan (`content_eval`)

`rpa_fetch_ig_reels` returns `data.content_eval` alongside the raw reels list:

| Field | Meaning |
|-------|---------|
| `cover_reels` | First 10 reels from the profile `/reels/` grid (most recent), each with RPA-scraped `thumbnail_url` |
| `video_reels` | When video eval is ON: **random 3** sampled from that same 10-reel pool (deterministic per handle) |
| `eval_mode` | `cover` or `video` (from brief/env switch) |
| `selection` | Metadata for ingest payloads (`random_3_from_recent_10`, etc.) |

**Recommended flow:**

1. `rpa_fetch_ig_reels(handle, max_reels=10)` → read `data.content_eval`
2. `rpa_download_ig_content(content_eval=...)` → local `cover_path` for all 10 + MP4 for random 3 when ON
3. `rpa_fetch_reel_comments` ×10 on `cover_reels[].url`
4. `vision_analyze(cover_path=...)` ×10; `video_analyze(file_path=...)` ×3 when ON

Cover downloads prefer the RPA grid `thumbnail_url` (HTTP GET with IG Referer) —
no yt-dlp round-trip when the grid already exposed the CDN URL. Falls back to
`rpa_download_ig_cover` / yt-dlp when the thumbnail is missing or HTTP fails.

## Reel Download (yt-dlp)

`rpa_download_ig_reel` downloads the video **and** cover image; the separate
`rpa_download_ig_cover` downloads **only** the cover (for cover-mode screening
without spending video bandwidth). Both use yt-dlp + cookies exported from the
Chrome profile.

**Prerequisites**:
- **yt-dlp on PATH** (`pip install yt-dlp`). A missing binary raises a clean
  `DownloadError("yt_dlp_not_found")`. Tested with yt-dlp 2026.07.04.
- **ffmpeg recommended** for best video quality (merges bestvideo+bestaudio).
  Without it, yt-dlp falls back to a pre-merged format (still a valid MP4).

**How it works** (both functions):
- Exports IG cookies from Chrome's `Cookies` SQLite DB to Netscape format.
  Chrome **encrypts** cookie values (`encrypted_value` column); on macOS they're
  decrypted with the Keychain "Chrome Safe Storage" key (AES-128-CBC). If the
  decrypted sessionid isn't printable, cookies are skipped and the download runs
  as a public client — **public reels download fine without cookies**; only
  private/age-gated reels need valid auth.
- Invokes yt-dlp with `--no-simulate --print thumbnail` (in yt-dlp ≥ 2026,
  `--dump-json` silently simulates and writes no file, so it can't be used to
  both fetch metadata and download). `quality="best"` (default) omits `-f` so
  yt-dlp picks the best muxed format (auto-falls-back when ffmpeg is absent).
- `download_reel` adds `--write-thumbnail` so the cover image is saved alongside
  the video; `download_cover` uses `--skip-download --write-thumbnail` for
  cover-only.
- Locates the saved file by globbing the dest dir for `<reel_id>.*` (yt-dlp's IG
  extractor doesn't populate `filesize`/`duration`, so the JSON isn't reliable).

**Returned fields**:
- `download_reel`: `file_path`, `file_size_bytes`, `thumbnail_url`, `cover_path`,
  `reel_id`.
- `download_cover`: `cover_path`, `file_size_bytes`, `thumbnail_url`, `reel_id`.

**Limits & gating**:
- `rpa_download_ig_reel` is blocked by the pre-tool-call hook when video eval is
  OFF (`KOL_RPA_VIDEO_EVAL_ENABLED=0`) and capped at 3 distinct reels per run
  when ON.
- `rpa_download_ig_cover` is **not** gated by the video-eval switch — cover mode
  is the default, so the agent can always fetch cover images for `vision_analyze`.
- Disk: auto-cleans videos **and** cover images older than 1 hour; 2 GB cap.

**Upstream IG breakage**: yt-dlp's Instagram extractor intermittently returns
HTTP 404 / "empty media response" when IG changes its media API. This surfaces
as a distinct `yt_dlp_ig_extractor_failed` error code (operator updates yt-dlp
or switches forks). The reel video itself streams via a `blob:` URL (MSE), so
there is no direct MP4 URL in the page DOM to fall back to.

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `KOL_RPA_ENABLED` | `1` | Master kill switch (`0` = disable all RPA tools) |
| `KOL_RPA_PHASE` | `1` | Which tools to register (1=3, 2=8, 3=11) |
| `KOL_RPA_VIDEO_EVAL_ENABLED` | `0` | Video eval switch (`1` = ON) |
| `KOL_RPA_STRICT_BROWSER_BLOCK` | `1` | Guard blocks browser_* to IG/Google URLs in discovery |
| `KOL_RPA_STRICT` | `0` | `1` = block ALL browser_* in discovery (extreme mode) |
| `KOL_RPA_MAX_PROFILES_PER_RUN` | `40` | Profile visit quota |
| `KOL_RPA_MAX_REEL_LOADS_PER_RUN` | `200` | Reel page load quota |
| `KOL_RPA_PROFILE_DELAY_S` | `2.0,4.0` | Jitter range between profiles |
| `KOL_RPA_REEL_DELAY_S` | `1.0,2.0` | Jitter range between reels |

## Dependencies

- `local-chrome-tab-pool` plugin (for CDP tab management)
- `yt-dlp` (for video download, Phase 2)
- `websockets` (for CDP page-level communication)

## File Layout

```
kol-discovery-rpa/
  __init__.py          # register(ctx) — loads tools.py + hooks.py via importlib
  tools.py             # 11 SCHEMA constants + handlers + as_function_schema
  hooks.py             # pre_tool_call: video eval switch enforcement
  plugin.yaml          # manifest
  internal/
    cdp_runner.py       # tab_pool.acquire + cdp_page wrapper + _seed_session
    errors.py           # RpaError hierarchy
    qualification_rules.py  # HARD_THRESHOLDS (single source of truth)
    followers_normalize.py  # K/M/万/亿 normalization
    qualify_evaluator.py    # profile + reels gate evaluator
    precheck.py             # exclusion_set precheck
    eval_mode.py            # video/cover switch resolver
    pacing.py               # jitter sleep + per-run quota
    risk_detector.py        # checkpoint/captcha/login-wall detection
    session_health.py       # sessionid cookie check
    ip_check.py             # ipinfo.io preflight
    ig_profile.py           # IG profile extraction JS + qualification
  tests/               # (Phase 1 — to be populated)
```

## Related

- `instagram-kol-discovery` SKILL.md — Step 0.5 RPA 优先, Step 1.5 内容筛选
- `kol-bridge-agent-guard` — bootstrap gate + URL block + fallback token
- `kol-discovery-precompress-guard` — visited handle tracking (rpa_* + browser_*)
- `local-chrome-tab-pool` — CDP tab management
- `docs/kol-discovery-rpa-guide.md` — integration guide + threshold sync process
