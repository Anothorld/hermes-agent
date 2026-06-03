# Nox 集成

## 功能说明

通过 **nox-kol-bridge** 做达人发现补充、配额统计、尽调/联系人/监控。Console 负责 LIVE 门禁、HMAC dispatch、调用 `nox_kol_tool.py`。

## 操作员路径

| 入口 | 组件 |
|------|------|
| 产品详情 shortlist | `ProductDetailPage`（批量尽调） |
| 活动配置区 | `NoxCampaignOpsPanel.tsx` |
| KOL 详情 | `KolDetailPage` + `NoxDiligencePanel`（尽调结论中文标签、缓存键可展开说明） |

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
| KOL 尽调 | `POST /kols/{id}/nox-diligence`（同步 CLI + 全量 fact 沉淀）；`nox_diligence_sync.py` |

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
- Instagram 的 `diligence-pack` **profile 不含粉丝数**；Console 用 `avg_views ÷ view_per_followers` 推算并写入 `identity.followers`（`identity.nox_followers_source=inferred_views_ratio`）。与 `creator search` 的 `followers` 字段一致量级。
- Nox 受众指标（如 `audience_authenticity`、`audience_quality`）常为 `{ value, status, ... }` 对象；`summarize.py` 与前端 `noxValueFormat.ts` 会取出 `value` 再展示为百分比（如 **84%**）。旧事实若仍是整段 JSON，看板也会在前端解包；重新尽调可写回标量。
- `nox_score` 常为 `{ overall, growth, creativity, audience, engagement, credibility }`；入库写 `identity.nox_score`（综合分）+ `identity.nox_score_breakdown`（JSON 分项）；看板展示 **综合 · 增长 · 创意 · 受众 · 互动 · 可信** 六项（含 0）。仅旧数据只有综合分时，需重新尽调以补充分项。
- Console 调 `cache-stats` **不要**传 `--env`（该子命令无 LIVE/TEST 分支；配额读本地 ledger）。
- Console `nox_gate.extract_campaign_config` 必须识别 bridge 返回的**扁平**
  `GET /campaigns/{id}` 行（勿只读嵌套 `campaign_config` 键）。
