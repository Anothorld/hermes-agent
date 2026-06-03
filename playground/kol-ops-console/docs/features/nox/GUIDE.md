# Nox 集成

## 功能说明

通过 **nox-kol-bridge** 做达人发现补充、配额统计、尽调/联系人/监控。Console 负责 LIVE 门禁、HMAC dispatch、调用 `nox_kol_tool.py`。

## 操作员路径

| 入口 | 组件 |
|------|------|
| 产品详情 shortlist | `ProductDetailPage`（批量尽调） |
| 活动配置区 | `NoxCampaignOpsPanel.tsx` |
| KOL 详情 | `KolDetailPage`（diligence/contacts 等） |

## 关键文件

| 层 | 文件 |
|----|------|
| BE | `nox_gate.py`, `nox_dispatch.py`, `nox_console_dispatch.py`, `nox_tool_runner.py`；`routers/campaigns.py`, `routers/kols.py` 中的 nox 路由 |
| FE | `NoxCampaignOpsPanel.tsx` |
| 插件 | `hermes-agent/plugins/nox-kol-bridge/` |
| 文档 | `agent_prj/docs/kol-nox-integration.md` |

## 主要 API（节选，以 bridge 为准）

| 场景 | 典型路径 |
|------|----------|
| 活动补充 | `POST /campaigns/{id}/nox/supplement` |
| 统计 | `GET /campaigns/{id}/nox/stats` |
| KOL 尽调 | `POST /kols/{id}/nox/diligence` 等 |

## 关联模块

- [campaigns](../campaigns/GUIDE.md)
- [agent-gateway](../agent-gateway/GUIDE.md) — supplement 可能走 gateway brief
- [products](../products/GUIDE.md) — shortlist

## 约束

- LIVE 配额与 supplement ledger；TEST 行为见 `nox_gate.py`
- 错误应对操作员可读（配额用尽、未配置 CLI 等）
- **Gate A/B 前**：在产品页展开「编辑 campaign_config」，勾选
  `nox_quota_enabled` 并保存（写入 CAL `nox_integration_json`）。未保存时
  `POST /kols/.../nox-diligence` 或 `nox-diligence-batch` 返回 `nox_quota_disabled`。
- LIVE 首次使用前：在 **`kol-orchestrator` profile** 的 `.env` 配置
  `NOXINFLUENCER_API_KEY`，并运行
  `nox_kol_tool.py doctor --env LIVE`（会自动 `noxinfluencer auth`）。
  Nox CLI 只读 `~/.noxinfluencer/config.json`，仅有 env 变量不够。
- Agent **不要**把 `--campaign-config-file` 传给 `kol_bridge_tool.py`；
  该参数仅用于 `nox_kol_tool.py` 的 LIVE gated 子命令。
- 没有 `cache-lookup` 子命令；查缓存请重跑 `diligence-pack`（`cache_hit: true`）。
- Console 调 `cache-stats` **不要**传 `--env`（该子命令无 LIVE/TEST 分支；配额读本地 ledger）。
- Console `nox_gate.extract_campaign_config` 必须识别 bridge 返回的**扁平**
  `GET /campaigns/{id}` 行（勿只读嵌套 `campaign_config` 键）。
