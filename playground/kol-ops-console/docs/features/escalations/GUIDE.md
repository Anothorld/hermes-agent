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
| **追信占位（已废弃）** | 升级 open 期间 chase regenerate（旧行为） | `追信占位(已废弃)` | `chase_supersede` |

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

## 操作员清单（入站升级）

1. 在 **升级队列** `/escalations` 打开工单，阅读触发回信。
2. 填写 **操作员答复** 与所需 facts（如 `approval.paid_ceiling_override`）。
3. 点 **提交并恢复**（或先「让 AI 试写草稿」预览，不关闭升级）。
4. 若 toast 提示「约 30–60 秒后请到待审批」→ 打开 **待审批** `/approvals`，找 **升级恢复稿** 标签。
5. 批准草稿 → Gmail 草稿 → 发送。

**勿批准** 仍带「追信占位」且升级未关的旧稿；升级已 resolved 后的占位稿建议驳回。

## 关键文件

| 层 | 文件 |
|----|------|
| FE 列表/详情 | `EscalationConsolePage.tsx` |
| FE 折叠回信 / 问题高亮 | `InboundEmailStack.tsx`, `EscalationSuggestedQuestion.tsx` |
| FE 待审批标签 | `ApprovalsPage.tsx`（`draft_origin` badge） |
| BE 升级 | `routers/escalations.py` — `_escalation_needs_reply_draft`, `draft_expected` |
| BE 待审批 | `routers/approvals.py` — `_derive_draft_origin` |
| Bridge chase | `plugins/kol-ops-bridge/reply_chase.py`, `cal.py` |
| Bridge 开升级 / 追信 | `plugin_api.py` `open_escalation`；`escalation_inbounds.py`；`cal.py` `append_pending_inbound_on_inbound_event` |
| Agent | `kol-reply-dispatcher` Step 3.1/3.5, `kol-escalation-resumer` |

## 主要 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/escalations` | 列表（可按 state/env 过滤） |
| GET | `/escalations/{id}` | 详情 + 关联入站 |
| PATCH | `/escalations/{id}` | resume / terminate；返回 `draft_expected` + `draft_followup`（`expected` / `already_pending` / `in_flight` / `none`） |
| POST | `/escalations/{id}/preview-draft` | 试写草稿（不 resolve） |
| GET | `/escalations/{id}/inbound-context` | 入站上下文 + `pending_inbounds[]`（旧单自动 sync） |
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
