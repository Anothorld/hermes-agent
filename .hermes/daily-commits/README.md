# 每日提交汇总

---

## 📋 2026-06-10 提交汇总

### 当日进度概览

今日共 3 个提交，主要围绕 kol-ops 决策学习闭环、邮件发现后自动起草、以及升级提示中文化三大方向。新增 9,886 行代码，删除 842 行，涉及 112 个文件，覆盖后端核心逻辑、前端交互组件和插件测试。

### 重点变更

#### 1. 发现决策学习闭环 (discovery decision learning loop)

- 新增 `discovery_decision_learning.py`（379行）与 `learning_discovery.py`（751行），实现发现阶段的决策学习与蒸馏
- 新增 `discovery_decision_tags.py`（215行），支持行级 approve 标签
- 新增前端组件：`DiscoveryLearningPanel.tsx`（482行）、`ShortlistDecisionFeedbackDialog.tsx`（435行）、`KolApproveAnnotationDialog.tsx`（228行）
- 后端新增 `learned_criteria.py`、`gate_metrics_audit.py`、`gate_metrics_trends.py`，支撑指标审计与趋势分析
- 前端新增 `MetricTrendSparkline.tsx`、`ApprovalContextCard.tsx`、`ProductCategoryField.tsx`

#### 2. 邮件发现后自动起草 (auto-draft after email discover)

- 新增 `email_discover_dispatch.py`（477行）与 `post_email_discover_draft.py`（300行）
- `gmail_console.py` 扩展 141 行，`gmail_reconcile.py` 更新
- `cal.py` 大幅扩展（+615行），`plugin_api.py` 扩展（+307行）
- `inbound_reply` 模块更新（orchestrator、processor、recovery）

#### 3. 升级提示中文化 & 其他优化

- `mailbox_escalation.py` 更新，升级操作提示改为中文
- `local-chrome-tab-pool` 修复与优化（hooks.py、tab_pool.py）
- `veedcrawl` 新增 `search.py`（148行）搜索模块及测试
- skills 目录下多个 SKILL.md 更新（instagram-kol-discovery、email-discovery 等）

### 提交明细

| 短Hash | 作者 | 时间 | Message |
|--------|------|------|---------|
| cfd9cdde9 | Anothorld | 16:03 | feat(kol-ops): discover decision learning loop and row-level approve tags |
| 66f620151 | Anothorld | 11:19 | feat(kol-ops): auto-draft after email discover and harden outreach prep |
| d94dc672f | Anothorld | 10:14 | feat(kol-ops): show escalation operator prompts in Chinese |

### 变更统计

- **文件数**：112
- **新增行**：9,886
- **删除行**：842
