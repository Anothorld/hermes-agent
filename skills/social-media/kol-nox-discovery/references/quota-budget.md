# Nox quota budget (KOL ops)

- Plan ceiling: **2000** credits/month (Nox dashboard).
- Local ledger default: **1800** (`campaign_config.nox_monthly_budget`).
- Supplement reserve: **≤200** total, capped per campaign via `nox_supplement_max_calls` (default **30**).

## Gates (typical cost)

| Gate | Subcommand | Calls |
|------|------------|-------|
| A | `diligence-pack` | 3 (profile+audience+content) |
| B | `contacts` | 1 |
| C | `monitor-setup` | 2 when `--force` |
| Supplement | `creator-search` | 1 per platform page |

`quota-snapshot` also calls remote `quota` (1 credit) unless cached locally for 5 minutes.

## Commands

```bash
python plugins/nox-kol-bridge/scripts/nox_kol_tool.py quota-snapshot --env LIVE
python plugins/nox-kol-bridge/scripts/nox_kol_tool.py cache-stats --env LIVE --campaign-id <cid>
```
