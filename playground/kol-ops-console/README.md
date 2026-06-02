# KOL Ops Console — learning UI

## 操作员快速上手（约 3 分钟）

1. **日常**：在 **待审批** 处理 AI 回信草稿（批准 / 结构化驳回）。
2. **看效果**：在 **门禁效果看板** 查看首轮通过率与高频驳回标签。
3. **学习闭环**（偶尔）：
   - **自主学习** → 查看编辑批次进度 → 达阈值后「生成学习提案」
   - **待审批** → 切到「学习提案」Tab → 批准 style + 策略沉淀
   - **自主学习** → 「策略反哺」预览并升格（可选，需 sync skills）

中文标签：`constants/domainLabels.ts`（goal/lane/学习任务）、`components/factKeyLabel.ts`（事实字段）。设置里可开启「显示原始字段」查看英文 key。

## 自主学习页 `/learning`

导航 **自主学习** → [`LearningPage.tsx`](frontend/src/pages/LearningPage.tsx)

| 区域 | Console API | Bridge |
|------|-------------|--------|
| 状态总览 | `GET /learning/overview` | `GET /learning/overview` |
| 手动触发 cron | `POST /learning/run-jobs` | `POST /learning/run-scheduled-jobs` (LIVE only) |
| 任务审计 | `GET /learning/job-runs` | `GET /learning/job-runs` |
| 生成 style 提案 | `POST /learning/propose-edit-policy` | `POST /learning/apply-edit-policy` |
| 编辑/驳回信号 | `GET /learning/edit-events`, `reject-events` | 同名 |
| 策略升格 | `POST /learning/promote-strategy` | 同名 |
| Policy 预览 | `GET /learning/policies/{scope}?env=` | `GET /policies/{scope}` |

**待审批**（`/approvals`）仍负责批准 `approval.style_learning_proposal` 与 `reply_draft`。

本地开发（示例）：

```bash
cd playground/kol-ops-console/frontend && npm run dev
# 后端需运行 Console FastAPI + Bridge
```

## New components

| File | Purpose |
|------|---------|
| `constants/domainLabels.ts` | Goal/lane/学习任务/policy 中文标签 |
| `constants/rejectTags.ts` | Controlled reject tag enum + labels |
| `components/LearningWorkflowStepper.tsx` | 学习闭环 5 步引导条 |
| `components/RejectCorrectionModal.tsx` | Tag multi-select + note + suggested_fix |
| `components/DraftEditDiffPanel.tsx` | Agent draft vs Gmail SENT body diff |
| `components/ApprovalActionBar.tsx` | Approve/reject bar; reply_draft reject → modal |
| `components/ApprovalContextCard.tsx` | Draft preview + inbound context; renders style + 策略 提案 |
| `components/ApprovalDetailPanel.tsx` | Context + diff + action bar (recommended wrapper) |
| `components/StrategyPromotionPanel.tsx` | 反哺：升格稳定 `reply_strategy` → `references/learned/<goal>.md` |

## Strategy 反哺 (promote → skill reference)

`StrategyPromotionPanel` (on **Learning** page) previews and applies
promotion of a stabilized `reply_strategy` goal section into the owning skill's
advisory playbook. Proxied via Console `POST /learning/promote-strategy` →
Bridge `POST /learning/promote-strategy`.

- Default is **dry-run preview**; 升格写入 only when eligible (`min_approvals`/`min_age_days`).
- Promoted content is **advisory** — skill HARD rules / fact ownership / pricing
  engine / escalation gates win on conflict.
- After 升格，operator must run `python playground/learning/sync_skills.py`.

## Reject API contract

```http
POST /approvals/approval.reply_draft/reject
X-Bridge-Key: …

{
  "identity_id": 42,
  "campaign_id": "C1",
  "env": "LIVE",
  "decided_by": "operator:alice",
  "correction": {
    "tags": ["premature_pricing", "too_long"],
    "note": "Do not mention price before scope is clear",
    "suggested_fix": "Ask which deliverables they prefer first"
  }
}
```

## Integration (wired in `ApprovalsPage`)

Pending ``approval.reply_draft`` rows use **`ApprovalDetailPanel`** (context +
structured reject modal + approve). Other fact paths keep the legacy prompt-based
reject flow.

`GET /learning/edit-events` is proxied by the Console backend (`routers/learning.py`)
so sent-body diffs load after approve without calling Bridge directly from the browser.

### Manual integration (other pages)

```tsx
import ApprovalDetailPanel from './components/ApprovalDetailPanel';

<ApprovalDetailPanel
  factPath={row.fact_path}
  context={row.context}
  identityId={row.identity_id}
  campaignId={row.campaign_id}
  env={row.env}
  decidedBy={`operator:${currentUser.id}`}
  approveButtonLabel="批准并创建 Gmail 草稿"
  onRejected={() => refreshApprovals()}
  onApproved={() => refreshApprovals()}
/>
```

After send + `reconcile-sent`, optional diff for sent approvals:

```tsx
import DraftEditDiffPanel from './components/DraftEditDiffPanel';

<DraftEditDiffPanel editLearning={editEvent.payload} agentBody={…} />
```

Fetch edit events: `GET /learning/edit-events?env=&identity_id=&campaign_id=&limit=1`
