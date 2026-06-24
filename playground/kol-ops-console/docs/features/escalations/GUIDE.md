# 升级（Escalation）处理

## 功能说明

当 AI 无法自主推进（分类器规则、预算上限、身份异常等）时，系统会开 **升级工单** 等待操作员答复。操作员在 Console 提交答复并 **恢复（resume）** 后，Gateway 上的 `kol-escalation-resumer` 继续推进；若升级来自 **KOL 入站回信**，还会自动起草一封带 `linked_escalation_id` 的正式回信稿供待审批。

与 **操作员主动追信**（Campaign 页的 followup-draft / `kol-proactive-followup`）分离：追信不经过 dispatcher、不依赖新入站。

## 三条草稿路径对照

| 路径 | 触发 | 待审批标签 | `child_skill` / 标记 |
|------|------|------------|----------------------|
| **KOL 回信自动** | 入站 → dispatcher 正常起草 | `KOL回信自动` | negotiator / synthesizer 等 |
| **升级恢复稿** | 入站 → 开升级 → 操作员 resume | `升级恢复稿` | `linked_escalation_id` 有值 |
| **操作员追信** | Console followup-draft | `操作员追信` | `kol-proactive-followup` |
| **追信换新稿** | 对方追信后系统自动替换上一版待审批稿 | `追信换新稿` | `chase_supersede` + `prior_source_message_id` |
| **追信占位（已废弃）** | 升级 open 期间 chase regenerate（旧行为） | `追信占位(已废弃)` | `chase_supersede` 无 `prior_source_message_id` |

升级 `awaiting_answer` 期间：

- `reply_chase_hint` → **`defer_escalation`**（不生成追信占位稿）
- `persist-reply-draft` **无** `linked_escalation_id` → **409**（同轮 Step 3.5/3.1 也拦得住）
- 预览/resume 稿须带 `linked_escalation_id`；有待审 linked 稿时 chase supersede → **409**
- **追信合并**：每封 `kol_inbound_reply` 写入时，Bridge 自动追加到 **一条** 入站类 `awaiting_answer` 升级的 `resume_context.pending_inbounds`（同 Gmail `thread_id` 优先，否则最新 inbound-tagged 行；开升级时种子 `MSG1` 为 `trigger`，无种子时首封入站也标 `trigger`）
- **问题摘要更新**：同一时刻把最新追信摘要追加到 `question_to_operator` / 列表与详情里的 `suggested_question`（`【KOL 追信 · <msg_id>】` 块，按 message_id 去重）

### 升级页「待处理回信」折叠区

- API：`GET /escalations/{id}/inbound-context` 打开时自动调用 Bridge `POST /escalations/{id}/sync-pending-inbounds` 回填旧单；返回 `pending_inbounds[]`（`触发升级` + `追信（待处理）` 标签）
- 追信只挂到 **一条** 入站类升级（同 thread 优先，否则最新 inbound-tagged 行）；内部类升级（discovery 等）不会被误追加
- `suggested_question` 中 `【KOL 追信】` 块在列表/详情以 **琥珀色** 高亮
- 操作员在答复时应覆盖 **全部** pending 来信；resume 稿以 `latest_pending_inbound_message_id` 为 Gmail 回复锚点

## 升级页一体化（入站升级单页闭环）

入站类升级（`source=classifier|dispatcher` + 入站锚点）的 **linked 回信稿** 在升级仍为 `awaiting_answer` 时：

- **只在升级详情页** `/escalations/{id}` 的 **「③ 回信预览」** 区展示（只读预览，**不可批准**）；复用 `ApprovalContextCard`，含 **沟通历史要点**（`conversation_summary.bullets`）、**合同附件页面内预览**（`contract_signing` 稿）与对方来信卡片。提交并恢复后的 **升级恢复稿**（`require_draft` resume brief）同样须在 `persist-reply-draft` 时写入要点（单技能路径先 `summary_only` 调 `kol-reply-synthesizer`）。
- **待审批** `/approvals` 列表**隐藏**这些行，顶部琥珀色提示链回升级页
- 操作员填写「操作员答复」并 **提交并恢复** 后，同一页的预览区开放 **批准并创建 Gmail 草稿**

标签：`draft_phase=pre_answer` → **升级回信预览**；`post_resume` → **升级恢复稿**。

API：

| 路径 | 说明 |
|------|------|
| `GET /escalations/{id}/linked-draft` | 仅 linked 稿 + `draft_phase` |
| `GET /escalations/{id}/hub-context` | **P1 推荐** — 含 `topic_cards[]`、`workflow_step` / `workflow_steps[]` + linked 稿 |

`topic_cards` 从 `approval.pending_topics` 解析（`;` 分隔）；含 `escalation {id}` / `operator decision needed` 的段标 **需你决定**，其余标 **可自动回复**。无 pending_topics 时回退为「升级触发话题」（`kol_quote` / `question_to_operator`）。

`draft_phase`：`pre_answer` → 升级回信预览；`post_resume` → 升级恢复稿。

多话题入站（例：一封邮件里 Matt 需升级 + Alyssa 可自动回复）仍可能同轮出现升级 + 预览稿；话题分栏 + 步骤条引导操作员先决定红色话题，再提交并批准合并回信。

### P2：同页闭环（批准后不跳转）

- **提交并恢复** 后 Console 在本页轮询 `hub-context`，直到出现可批准稿或 `completion`（无需去待审批）。
- **批准回信** 后自动轮询刷新；`hub-context.completion.status=draft_approved` 时展示 **「本升级已处理完成」** 卡片（含 Gmail 草稿 ID、KOL 链接、返回队列）。
- 完成后隐藏操作员答复表单与待批准面板；步骤条全部标为已完成。

## 操作员清单（入站升级）

**KOL 筛选**：升级队列工具栏支持按 **@handle / 邮箱** 搜索；从 KOL 详情或看板跳转时 URL 含 `identity_id`、`campaign_id`、`q`，列表自动只显示该 KOL 的升级。点「已筛选 ✕」恢复全量。

**排序**：工具栏下拉可选 **智能优先级**（默认）、**等待最久**（`created_at` 升序）、**最新到达**（`created_at` 降序）。

1. 在 **升级队列** `/escalations` 打开工单，阅读触发回信与 **③ 回信预览**（若有）。
2. 阅读 **请求操作员答复**（`suggested_question` / `question_to_operator`）— 文案应为 **简体中文**（来自策略规则模板、Bridge 确定性文案，或 Agent 开单时写入；KOL 追信块由系统自动追加）。
3. 填写 **操作员答复** 与所需 facts（如 `approval.paid_ceiling_override`）。
4. 点 **提交并恢复**（或先「让 AI 试写草稿」预览，不关闭升级）。
5. 升级处理完成后，在 **本页 ③ 回信预览** 批准草稿 → Gmail 草稿 → 发送（勿去待审批页找 linked 稿）。

**勿批准** 仍带「追信占位(已废弃)」且升级未关的旧稿；升级已 resolved 后的占位稿建议驳回。「追信换新稿」为针对最新来信的正式回复，核对正文后可批准。

## 关键文件

| 层 | 文件 |
|----|------|
| FE 列表/详情 | `EscalationConsolePage.tsx` |
| FE 折叠回信 / 问题高亮 | `InboundEmailStack.tsx`, `EscalationSuggestedQuestion.tsx` |
| FE 话题分栏 / 步骤条 | `EscalationTopicCards.tsx`, `EscalationWorkflowStepper.tsx` |
| BE hub 解析 | `backend/app/escalation_hub.py` |
| FE 待审批标签 | `ApprovalsPage.tsx`（`draft_origin` badge） |
| BE 升级 | `routers/escalations.py` — `_escalation_needs_reply_draft`, `draft_expected` |
| BE 待审批 | `routers/approvals.py` — `_derive_draft_origin` |
| Bridge chase | `plugins/kol-ops-bridge/reply_chase.py`, `cal.py` |
| Bridge 开升级 / 追信 | `plugin_api.py` `open_escalation`；`escalation_inbounds.py`；`cal.py` `append_pending_inbound_on_inbound_event` |
| Agent | `kol-reply-dispatcher` Step 3.1/3.5, `kol-escalation-resumer` |

## 主要 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/escalations` | 列表（可按 `state` / `env` / `identity_id` / `campaign_id` 过滤；`sort=priority\|time`、`order=asc\|desc`；行内带 `handle`、`email`） |
| GET | `/escalations/{id}` | 详情 + 关联入站 |
| PATCH | `/escalations/{id}` | resume / terminate；返回 `draft_expected` + `draft_followup`（`expected` / `already_pending` / `in_flight` / `none`） |
| POST | `/escalations/{id}/preview-draft` | 试写草稿（不 resolve） |
| GET | `/escalations/{id}/inbound-context` | 入站上下文 + `pending_inbounds[]`（旧单自动 sync） |
| GET | `/escalations/{id}/linked-draft` | 关联 pending `approval.reply_draft` + `draft_phase` / `can_approve` |
| GET | `/escalations/{id}/hub-context` | 话题分栏 + 五步步骤条 + linked 稿（详情页主数据源） |
| POST | Bridge `/escalations/{id}/sync-pending-inbounds` | 从 timeline 回填 `pending_inbounds`（Console inbound-context 自动调用） |

## #109 类 incident 复盘要点

- **现象**：`source=classifier` 升级 resume 后无新 `approval.reply_draft`。
- **根因**：Console 门控只认 `source=dispatcher` + `source_message_id`；分类器开升级时未写 `source_message_id`。
- **并发**：升级 open 时 chase `regenerate` 仍写出 `chase_supersede` 占位稿，与「等操作员」冲突。
- **修复**：扩展门控认 classifier/dispatcher + 入站锚点；open 升级期间 `defer_escalation`；待审批用 `draft_origin` 区分稿型。

## 关联模块

- [approvals](../approvals/GUIDE.md) — 批准发送
- [campaigns](../campaigns/GUIDE.md) — 操作员追信（followup-draft）
- [agent-gateway](../agent-gateway/GUIDE.md) — resume / resumer 运行
- `docs/kol-reply-chase-auto-regenerate.md` — chase 与 defer 规则
- `docs/kol-bridge-agent-tooling.md` — CLI 与 resume brief
