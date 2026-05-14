# fb_creator_discovery — Hermes Plugin

Wraps the [Meta Facebook Creator Discovery API](https://developers.facebook.com/docs/fb-creator-discovery/) and exposes three tools so an agent can find creators that match a brand's interests, audience demographics, and engagement criteria.

## Tools

| Tool | Purpose |
| --- | --- |
| `fb_creator_search` | Search creators by free-text query, category, country, interest, follower count, reach, interaction rate, or audience demographics. Paginated. |
| `fb_creator_profile` | Fetch a single creator by `creator_id` plus optional `insights` metrics (reach, interactions, audience country/gender, etc). |
| `fb_content_search` | Search posts/reels with filters (creator, content type, time range, sort by reach/reactions/comments…), or fetch one content item by id with optional comments + insights. |

All tools auto-gate on credential availability — they appear in `hermes tools` but the gate prevents dispatch when no token is configured.

## Prerequisites

1. **Meta App** of type **Business** with the following permissions:
   - `facebook_creator_marketplace_discovery`
   - `pages_show_list`
2. **Access level**: Permissions need **Advanced Access** (via App Review) to receive **real** creator data. **Standard Access** returns simulated/mocked data only.
3. **Page Access Token** for a Brand Page eligible for the Creator Discovery program.

### Get a Page Access Token (one-time, manual)

Use the [Graph API Explorer](https://developers.facebook.com/tools/explorer/):

1. Select your app, log in as the brand admin, grant the two permissions above.
2. Call `GET /me/accounts` — copy the `access_token` of the target Brand Page.
3. (Recommended) Exchange for a long-lived Page Access Token via the documented `oauth/access_token` flow.

## Configure credentials

Pick **one** of the following.

### Option A — JSON config file (recommended)

```bash
mkdir -p ~/.hermes
cat > ~/.hermes/fb_creator.json <<'EOF'
{
  "page_access_token": "<your_page_access_token>",
  "api_version": "v21.0"
}
EOF
chmod 600 ~/.hermes/fb_creator.json
```

### Option B — Environment variable (overrides the file)

```bash
export FB_CREATOR_PAGE_TOKEN="<your_page_access_token>"
export FB_CREATOR_API_VERSION="v21.0"  # optional, defaults to v21.0
```

You can override the config file path with `FB_CREATOR_CONFIG_PATH=/some/other/path.json`.

## Rate limits (enforced by Meta)

| Scope | Limit |
| --- | --- |
| Per Facebook account | `2000 req / rolling 1h` |
| Per app | `10000 req / rolling 1h` |

The plugin surfaces Graph error codes `4`, `17`, `32` as `FBCreatorAPIError` with the `Retry-After` header echoed in the JSON envelope so the agent can back off.

## Examples

### Find US creators with 100k–1M followers in a topic

```jsonc
// fb_creator_search
{
  "query": "cookies",
  "creator_countries": ["US", "CA"],
  "follower_count": { "min": 100000, "max": 1000000 }
}
```

### Filter by audience demographics

```jsonc
// fb_creator_search
{
  "creator_countries": ["US"],
  "major_audience_age_bucket": "25-34",
  "major_audience_gender": "female"
}
```

### Drill into one creator's insights

```jsonc
// fb_creator_profile
{
  "creator_id": "1234567890",
  "insights_metrics": ["creator_reach", "creator_interactions"],
  "insights_time_range": "L28",
  "recent_content_limit": 5
}
```

### Trending reels by reach over the last 7 days

```jsonc
// fb_content_search
{
  "content_type": "REELS",
  "sort_by": "reach",
  "time_range": "L7",
  "reach": { "min": 5000 }
}
```

### One post with comments and insights

```jsonc
// fb_content_search
{
  "content_id": "987654321",
  "include_comments": true,
  "comments_limit": 20,
  "insights_metrics": ["reach", "views", "comments_by_paid_organic"]
}
```

## Pagination

Responses contain `paging.cursors.after`. To fetch the next page, pass that string back as the `after` argument on the same tool with the same filters.

## Security & cost notes

- **Token never leaves disk/env**: this plugin never logs the token, never echoes it back to the agent, and never writes it.
- **Read-only**: the Creator Discovery API is read-only; the plugin therefore exposes no write/delete tools.
- **Cost**: Graph API calls are free of monetary charge but consume the rate-limit budget above. Default `limit=25` keeps response payloads small. Prefer `fb_creator_search` with the default field card for triage, then `fb_creator_profile` for deep-dive — this avoids inflating context with unwanted insights blocks.
- **Privacy**: Creator data is only accessible for creators who opted in to data sharing. Branded-content data is only shared with the sponsoring brand. Treat all returned data accordingly.

## File layout

```
fb_creator_discovery/
├── __init__.py        # plugin entry point + tool registration
├── plugin.yaml        # manifest
├── README.md          # this file
├── client.py          # FBCreatorClient facade
├── tools.py           # tool schemas + handlers
├── _internal/
│   ├── credentials.py # FBCredentialProvider + EnvFileCredentialProvider
│   ├── errors.py      # typed exceptions
│   ├── http.py        # FBGraphHTTPClient (Graph API transport)
│   └── params.py      # pure parameter normalization helpers
└── tests/             # pytest unit tests
```
