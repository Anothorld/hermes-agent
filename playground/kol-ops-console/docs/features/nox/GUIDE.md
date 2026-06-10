# Nox 集成

## 功能说明

通过 **nox-kol-bridge** 做达人发现补充、配额统计、尽调/联系人/监控。Console 负责 LIVE 门禁、HMAC dispatch、调用 `nox_kol_tool.py`。

## 操作员路径

| 入口 | 组件 |
|------|------|
| 产品详情 shortlist | `ProductDetailPage`（批量尽调） |
| 活动配置区 | `NoxCampaignOpsPanel.tsx` |
| KOL 详情 | `KolDetailPage` + `NoxDiligencePanel`（尽调结论 + 统一 Nox 数据看板、分布类图表、缓存键可展开说明） |

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

## Discover 受众画像（Gate discovery_qualify）

- 当 campaign 已保存 `nox_quota_enabled: true` 时，Launch / rediscover brief 会附带
  `nox_discovery_enabled` 与签名 `campaign_config_file`（`allowed_gates: discovery_qualify`）。
- Agent 在 **IG 主页轻筛通过后、Reel 深筛前** 调用
  `diligence-pack --gate discovery_qualify --dimensions audience`（每 handle 每月 1 积分；
  `cache_hit` 为 0 积分）。
- 结果经 `upsert-identity` + `write-facts-multi` **立即入库**（含淘汰账号），与 Nox 月度缓存对齐。
- Gate A 批量尽调会 **增量拉取** 未缓存维度，复用 discover 已拉的 `audience`，避免重复扣费。
- 细则：`skills/social-media/instagram-kol-discovery/references/nox-audience-screen.md`

## 约束

- LIVE 配额与 supplement ledger；TEST 行为见 `nox_gate.py`
- 错误应对操作员可读（配额用尽、未配置 CLI 等）
- **Gate A/B 前**：在产品页展开「编辑 campaign_config」，勾选
  `nox_quota_enabled` 并保存（写入 CAL `nox_integration_json`）。未保存时
  `POST /kols/.../nox-diligence` 或 `nox-diligence-batch` 返回 `nox_quota_disabled`。
  保存后 shortlist 的「Nox 未启用」横幅应立刻消失；`PATCH /campaigns/{id}/config`
  会清除 `GET /campaigns/{id}/nox-stats` 的 45s 内存缓存（前端保存后也会带
  `bypass_cache=1` 刷新）。
- LIVE 首次使用前：在 **`kol-orchestrator` profile** 的 `.env` 配置
  `NOXINFLUENCER_API_KEY`，并运行
  `nox_kol_tool.py doctor --env LIVE`（会自动 `noxinfluencer auth`）。
  Nox CLI 只读 `~/.noxinfluencer/config.json`，仅有 env 变量不够。
- Agent **不要**把 `--campaign-config-file` 传给 `kol_bridge_tool.py`；
  该参数仅用于 `nox_kol_tool.py` 的 LIVE gated 子命令。
- 没有 `cache-lookup` 子命令；查缓存请重跑 `diligence-pack`（`cache_hit: true`）。
- Instagram 的 `diligence-pack` **profile 不含粉丝数**；Console 用 `avg_views ÷ view_per_followers` 推算并写入 `identity.followers`（`identity.nox_followers_source=inferred_views_ratio`）。与 `creator search` 的 `followers` 字段一致量级。
- Nox 受众指标（如 `audience_authenticity`、`audience_quality`）常为 `{ value, status, ... }` 对象；`summarize.py` 与前端 `noxValueFormat.ts` 会取出 `value` 再展示为百分比（如 **84%**）。旧事实若仍是整段 JSON，看板也会在前端解包；重新尽调可写回标量。
- **受众画像入库字段**（Gate A `diligence-pack` → `identity.nox_*`）：地区（`regions[].value` 含百分比）、性别（`genders` 数组）、年龄（`female_ages`/`male_ages`）、成人/儿童（`adults`）、语言（`languages`）、受众类型（`audience_types`，Console 显示中文标签）、真实度/区间、质量分、正面受众占比、推广吸引力/兴趣/专业度、兴趣标签（`content.audience_interests[].keyword`）。Instagram 等平台部分字段可能为 `null`——看板与尽调面板**隐藏**无数据字段，不展示占位符。
- Gate A 默认尽调维度为 **`profile,audience,content,cooperation`（4 次 API）**。旧缓存键仅含三维时需重新尽调以拉合作详情。
- **达人档案 / 内容** 从 `profile`+`content` 解析；**合作商业** 优先读 `cooperation` 维度（估价、响应时长、品牌合作史、广告视频占比等），缺失时回退 `profile` 内嵌合作字段。
- KOL 详情 **Nox 尽调与数据 (Gate A)** 面板为唯一 Nox 展示入口（已移除 profile 卡内重复的「Nox 数据看板」）。数据来自 `noxDashboardCategories` + `NoxInsightsSections`；仅有数据的字段/分区才会出现（档案/受众/内容/合作/联系方式/尽调记录）。
- **图表**：地区、性别、年龄、成人儿童、语言、受众类型、内容形式数量、分形式互动等**分布/占比**类字段用紧凑饼图 + 侧栏图例（`NoxPieChart`）；点击或悬停区块会高亮弹出、图例同步，并展示名称与占比；Nox 评分分项、对标排名、表现等级 (L1–L5) 等非占比指标仍用迷你条形图。纯 SVG，无额外图表库。
- 2026-06 前已尽调的老数据需点「重新尽调」以补全新字段与图表数据。
- `nox_score` 常为 `{ overall, growth, creativity, audience, engagement, credibility }`；入库写 `identity.nox_score`（综合分）+ `identity.nox_score_breakdown`（JSON 分项）；看板展示 **综合 · 增长 · 创意 · 受众 · 互动 · 可信** 六项（含 0）。仅旧数据只有综合分时，需重新尽调以补充分项。
- Console 调 `cache-stats` **不要**传 `--env`（该子命令无 LIVE/TEST 分支；配额读本地 ledger）。
- Console `nox_gate.extract_campaign_config` 必须识别 bridge 返回的**扁平**
  `GET /campaigns/{id}` 行（勿只读嵌套 `campaign_config` 键）。
