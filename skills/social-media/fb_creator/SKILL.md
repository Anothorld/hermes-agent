---
name: fb_creator
description: "Find Facebook creators that match a brand's interest, audience and engagement criteria via the Meta Creator Discovery API."
version: 0.1.0
author: Hermes Agent
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [facebook, meta, creator, influencer, social-media, partnership-ads]
    homepage: https://developers.facebook.com/docs/fb-creator-discovery/
---

# fb_creator — Facebook Creator Discovery workflow

Use this skill when the user asks to:
- find Facebook creators / influencers matching an interest, niche, country, follower band, or audience demographic
- evaluate a creator's reach, interactions, audience country/gender breakdown
- discover trending Facebook posts/reels by topic, reach, or engagement
- prepare a shortlist for a brand's partnership-ads campaign

## Context — when this works

- Backed by the `fb_creator_discovery` plugin (auto-loaded). It calls
  `https://graph.facebook.com/{version}/creator_marketplace/{creators,content}`.
- Requires a Page Access Token for an eligible Brand Page with the
  `facebook_creator_marketplace_discovery` + `pages_show_list` permissions.
  Configure via `~/.hermes/fb_creator.json` or `FB_CREATOR_PAGE_TOKEN` env var
  (see plugin README).
- **Standard Access returns mocked data**; only Advanced Access (post App
  Review) returns real creators. If results look obviously synthetic,
  surface that fact to the user.
- Read-only API — there are no booking / outreach tools here.

## Procedure

### 1. Translate the user's interest into search parameters

Map the user request to the most specific filters available. Don't dump
everything into `query` — combine structured filters for better precision.

| User intent | Tool args |
| --- | --- |
| "美妆类、美国、10w-100w 粉丝、女性受众" | `fb_creator_search` `creator_countries=["US"]` `follower_count={min:100000,max:1000000}` `major_audience_gender="female"` `query="beauty"` |
| "Top food reels this week" | `fb_content_search` `content_type="REELS"` `sort_by="reach"` `time_range="L7"` `query` is not supported on content — use `creator_id` if narrowing by creator |
| "Disney-related creators" | `fb_creator_search` `query="Disney"` |
| "Creators with high engagement among non-followers" | `fb_creator_search` `interaction_rate={min:1.5,time_range:"L14",breakdown:"non_follower"}` |

`time_range` allow-list: `L1`, `L7`, `L14`, `L28`, `L90`, `L180`.

### 2. Triage with `fb_creator_search`

Default fields are intentionally a lightweight card
(display name, alias, bio, profile url, follower count, categories, country,
onboarding status). Don't request `insights` here — it inflates context.

Always inspect `paging.cursors.after`. If the user wants more, call again
with `after=<cursor>` and the same filters.

### 3. Deep-dive with `fb_creator_profile`

Once a candidate is identified, call `fb_creator_profile` with:
- `creator_id` from the search result
- `insights_metrics` such as
  `["creator_reach", "creator_interactions", "creator_audience_country", "creator_audience_gender"]`
- `insights_time_range` (default to `L28` unless the user said otherwise)
- `recent_content_limit=5` to embed a few recent posts inline

Avoid passing every metric in one call; prefer the smallest set the user needs.

### 4. (Optional) Inspect content with `fb_content_search`

- Multi-content search: combine `creator_id`, `content_type`, `sort_by`,
  `time_range`, and metric filters (`reach` / `reactions` / `comments`).
- Single-content fetch: set `content_id` (then `creator_id`/`sort_by` are
  ignored) and optionally `include_comments=true` + `insights_metrics`.

### 5. Summarize for the user

When you have ≥3 candidates, present a compact table:
`name | followers | country | top categories | recent reach (L28) | profile_url`.
Then ask whether to expand any candidate or refine filters.

## Examples

### Successful flow

> "帮我找 5 个美国、女性受众为主、关注烘焙的 Facebook creator，10w-100w 粉丝。"

```
1. fb_creator_search { query:"baking", creator_countries:["US"],
    major_audience_gender:"female",
    follower_count:{min:100000,max:1000000}, limit:10 }
2. fb_creator_profile { creator_id:"<id>",
    insights_metrics:["creator_reach","creator_interactions"],
    insights_time_range:"L28", recent_content_limit:3 }   # for top 3 candidates
3. Present a 5-row markdown table; offer to drill deeper or paginate.
```

### Failed flow (and how to recover)

> "Find creators on Instagram for a streetwear campaign."

This API only covers **Facebook** creators. Tell the user explicitly,
suggest the Instagram Graph API path, and don't invent a fake call.

> Error envelope contains `auth_required: true`.

Tell the user the Page Access Token is missing or expired, and reference
`~/.hermes/fb_creator.json` / `FB_CREATOR_PAGE_TOKEN`. **Never** ask the user
to paste a token into chat — they should populate the file or env var
themselves.

> Error envelope has `fb_error_code` in `[4, 17, 32]`.

Rate limited. Surface the `retry_after` value, stop the loop, and ask the
user how they want to proceed (wait / narrow the query / try again later).

## Pitfalls

- **Mocked data** under Standard Access — easy to mistake for real results.
  Look for placeholder names / round numbers and warn the user.
- **`query` does not exist on content search** — use `creator_id` to scope.
- **Lookback window typos** silently drop the param. Stick to the allow-list.
- **Audience filters require opted-in creators** — empty results often mean
  filters are too tight, not that the API is broken.
- **Don't print the access token** under any circumstance — even on errors.
