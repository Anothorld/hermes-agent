# KOL Ops Console 项目进度与功能汇报

> 汇报日期：2026-05-25  
> 项目范围：Hermes `kol-ops-bridge` 插件 + 外部 KOL Ops Console Web 控制台

## 1. 项目目标

KOL Ops Console 的目标是把 Hermes Agent 的 KOL 招募、沟通、审批、升级和审计流程产品化为一个可运营的 Web 控制台。

当前设计分工清晰：

- Agent 负责开放性任务：KOL 发现、候选人分析、邮件草稿生成、谈判理解、异常归因。
- Web Console 负责人机协作界面：产品管理、Campaign 启动、候选池审核、审批队列、升级处理、策略编辑、审计查看。
- `kol-ops-bridge` 负责确定性读写：身份、事件、事实、候选池、审批、升级、Campaign 配置等均通过 Bridge API 或安全 CLI 落库。

## 2. 当前架构

项目采用 Console 与 Hermes Bridge 分离的架构。

| 模块 | 位置 | 职责 |
|---|---|---|
| Web Frontend | `playground/kol-ops-console/frontend/` | React + TypeScript SPA，提供运营控制台界面 |
| Web Backend | `playground/kol-ops-console/backend/` | FastAPI + SQLite，本地用户、RBAC、产品、Campaign 与审计状态 |
| Bridge Plugin | `plugins/kol-ops-bridge/` | Hermes 插件，维护 Conversation Audit Layer，并提供 Agent/Console 读写 API |
| Hermes Gateway | Hermes Gateway `/v1/runs` | Web 后端通过 Gateway 启动或继续 Agent run |
| CAL 数据库 | `~/.hermes/kol-ops-bridge/cal.db` | Hermes 侧对话审计与业务事实主库 |
| Console 数据库 | `~/.hermes/kol-ops-console/app.db` | Web 控制台本地状态库 |

架构原则：

- TEST / LIVE 数据隔离，所有运行态写入显式携带 `env`。
- 确定性 CRUD 不由 Agent 临时脚本直接操作，统一通过 Bridge API 或 `kol_bridge_tool.py`。
- CAL 写入失败不阻塞主流程，后续可通过 reconcile 机制补齐。
- Web 所有变更操作记录 `audit_log`。

## 3. 已实现功能

### 3.1 登录、权限与审计

- FastAPI 后端支持 JWT 登录、刷新和当前用户识别。
- 已定义 Owner / Operator / Viewer 三档 RBAC：
  - Owner：完整管理权限，包括用户管理和 TEST 数据清理。
  - Operator：可启动 Campaign、处理审批与升级。
  - Viewer：只读查看。
- 所有 mutating request 写入审计日志，便于追踪操作来源。

### 3.2 产品与 SKU 管理

- 支持产品本地目录：SKU、名称、URL、标签、备注、产品卖点、Pitch、默认预算。
- 产品详情页可查看关联 Campaign、运行状态、候选数、KOL 联系状态和最新事件。
- 支持产品变体列表 `variants_json`，Campaign 启动时可限定本次可提供的变体范围。
- 已有基础 URL 变体解析：可从 Povison/Shopify 风格 URL 中提取 `variant`、`sku`、`id` 等 token，并生成产品变体行。
- 当前变体解析仍属于轻量模式：能识别 `?variant=43962`，但要解析出完整规格（如尺寸、颜色、价格、SKU）仍需新增页面/接口级解析或维护本地映射表。

### 3.3 Campaign 启动与编排

- Web 后端支持从产品详情或 Campaign Wizard 启动 Campaign。
- 启动参数覆盖：预算、总预算、绝对底价、目标人数、候选池目标数、TEST 收件人、交付平台、每平台内容数量、审核标准、产品 Pitch、补充说明、可选产品变体。
- Campaign 启动前会校验：
  - SKU 是否存在。
  - 选中的变体 ID 是否属于产品已知变体。
  - 同一 Campaign / SKU 是否已有运行中任务，避免重复启动。
- 启动流程先写入 Bridge campaign_config，再通过 Hermes Gateway 创建 Agent run。
- Agent 启动 brief 中已包含严格运行契约：
  - 必须使用 `kol_bridge_tool.py` 进行 CAL 读写。
  - 必须按 intake → orchestrator → discovery → candidate persistence → relationship resolution 顺序执行。
  - 首轮只产出候选池，不自动发信。

### 3.4 KOL 候选池与审批门禁

- Campaign discovery 结果会进入候选池，由 Web Console 给运营人员审核。
- 支持候选人持久化、候选池查看、候选选择和 shortlist approval。
- Operator 批准 shortlist 后，系统会启动后续 Agent run 生成冷启动或复联邮件草稿。
- 草稿不会直接发送，必须写入 `approval.reply_draft`，进入 Web 审批队列。

### 3.5 KOL 关系与事实管理

- Bridge 维护全局 KOL identity，支持跨产品、跨 Campaign 复用。
- 支持 identity alias：thread_id、message_id、email、handle 等可映射回同一 KOL。
- 支持 KOL 时间线、事实、关系历史、历史合作结果、偏好 SKU 等记录。
- 前端已提供 KOL Kanban、KOL Detail、Relationship 页面。
- 已有 Repeat KOL 标识和 relationship path 判定能力，为冷启动/复联路径分流做基础。

### 3.6 回复监听、审批与升级

- Web 产品详情页已提供 Reply watcher 控制面板，可启动、停止、重启 Gmail 回复监听，并同步 sent 状态。
- 回复链路目标：Gmail replies → CAL inbound event → reply router → draft approval 或 escalation。
- 已有 Approvals 页面承载待审批草稿与操作。
- 已有 Escalation Console，支持升级列表、详情、人工处理和恢复上下文。
- Bridge 支持 stuck-goal scan，可按 Campaign 的 follow-up interval 识别长期未推进目标，并通过 DingTalk 通知运营。

### 3.7 策略与配置

- 支持策略编辑页面，包括 company style、user style、escalation rules 等。
- 支持 Campaign 配置编辑面板，用于调整运行时策略、变体政策、审计标准等。
- Contract readiness 面板已接入产品详情页，用于检查合同所需字段是否齐备。

### 3.8 前端页面与交互面

当前前端主导航包括：

- Products：产品列表与产品详情。
- KOLs：KOL 看板与详情。
- Escalations：升级处理台。
- Approvals：审批队列。
- Policies：策略编辑。
- Settings：设置。

已存在但不作为主导航入口的页面包括：

- Campaign Wizard。
- Campaign Candidates。
- Agent Transcript。
- Reply Monitor。

## 4. 当前重点进展

近期重点主要集中在 Campaign 运行闭环和商品变体能力上：

1. Campaign 启动链路已经从 Web 后端直接接入 Hermes Gateway，Bridge 保持确定性 CAL 读写职责。
2. Campaign brief 已结构化包含 `campaign_config`、产品信息、预算、候选池目标、交付要求、产品 Pitch 和可选变体。
3. 产品变体已经进入数据模型和 Campaign 约束：运营可在产品层维护变体，启动 Campaign 时选择允许的变体范围。
4. Povison `variant` 参数已验证为商品子款 ID，不是追踪参数；同一商品页会根据不同 `variant` 切换尺寸、颜色、SKU、价格和图片。
5. 已明确下一步如果要“根据 variant 自动解析规格”，应采用本地映射表或页面/API 解析方式，而不能依赖数字递增规律。

## 5. 测试与验证现状

已有测试覆盖分布：

- Console Backend：
  - `tests/test_agent_stream.py`
  - `tests/test_policy_rbac.py`
- Bridge Plugin：
  - goal machine
  - dispatcher bundle
  - discovery router
  - email classifier
  - policies
  - notifier
  - escalation flow
  - stuck goals
  - approval reply draft validation
  - render contract
  - e2e flow 等 19 个测试文件

最近一次相关命令记录显示，`plugins/kol-ops-bridge/tests/` 测试集在排除一个特定 guard 测试后通过。后续若要作为正式汇报材料，建议补一次完整测试输出并记录版本号/commit hash。

## 6. 当前限制与风险

| 风险/限制 | 说明 | 建议 |
|---|---|---|
| Povison 规格解析尚未全自动 | 当前只能从 URL 解析 variant ID，不能直接稳定解析尺寸/颜色/价格 | 新增 `resolve-variant` 能力：优先本地映射，必要时页面/API 解析 |
| 外部页面解析易变 | Povison 前端结构、接口、弹窗和反爬策略可能变化 | 把解析放在产品录入阶段，并缓存到本地 `variants_json` |
| Agent run 依赖 Bridge/Gateway 双服务 | 启动 Campaign 需要 Bridge、Gateway、密钥和环境变量齐备 | 增加启动前健康检查和 UI 级错误提示 |
| TEST/LIVE 安全要求高 | 邮件、候选池、Campaign 写入必须严格隔离 | 保持所有 mutating API 显式要求 `env`，避免默认值写入 |
| 审批链路需要持续打磨 | 草稿生成、审批、发送、回写之间需要强一致体验 | 增加端到端用例与可视化状态标识 |

## 7. 建议下一步

### 短期（1-2 天）

- 为产品变体新增“解析完整规格”能力：
  - 输入商品 URL。
  - 输出 `{id, label, url, attributes}`。
  - attributes 包含 `sku`、`size`、`color`、`price`、`availability` 等字段。
- 在产品详情页增加“刷新/解析变体规格”按钮，把解析结果写回 `variants_json`。
- 补齐 TS8319 这类 Povison 商品的完整变体对照表，验证 Campaign 选择变体后的 brief 输出是否准确。

### 中期（3-5 天）

- 完整跑通 Web 发起 Campaign → Agent 发现候选 → Web 审核 shortlist → Agent 生成草稿 → Web 审批的闭环。
- 增加 Bridge/Gateway/Reply watcher 健康检查页。
- 加强审批队列的状态流转：pending、approved、rejected、sent、escalated。
- 为合同 readiness 和交付审核标准增加更明确的 UI 提示。

### 后续优化

- 对 Povison 以外的商家建立可插拔解析器策略。
- 将产品、Campaign、KOL、审批、升级的关键状态做成运营仪表盘。
- 完善 LIVE 模式安全门禁，包括发送前确认、收件人白名单和异常中止机制。
- 增加更多 e2e 测试，覆盖 TEST/LIVE 隔离、重复启动保护、变体选择和审批恢复。

## 8. 汇报结论

KOL Ops Console 目前已经具备从产品管理、Campaign 启动、KOL 候选池沉淀、人工审批、升级处理到审计追踪的核心骨架。系统设计上已经把 Agent 的开放性判断与确定性状态操作拆开，Bridge/CLI 作为统一写入入口，符合可审计、可回滚、可运营的方向。

当前最值得继续推进的是两个闭环：

1. **业务闭环**：完整跑通从候选发现到审批草稿的端到端流程。
2. **商品闭环**：把 Povison `variant` 从“ID 提取”升级为“规格解析 + 本地缓存 + Campaign 可选范围”。

完成这两点后，项目就可以从功能骨架进入小规模真实运营验证阶段。
