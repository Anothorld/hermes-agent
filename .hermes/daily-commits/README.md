# 📋 2026-06-11 每日提交汇总

## 当日进度概览
当日有 1 个提交，涉及 70 个文件，净增 3385 行（+4007 / -622）。主要工作集中在三个方向：CDP 浏览器 Tab 所有权机制、LLM 压缩学习管线（learning_discover/distill/store）、以及 KOL 运营控制台 UX 优化（审批流程、产品详情页、升级标签体系）。

## 重点变更

### 🔧 CDP Tab 所有权 & Chrome Tab Pool
- `local-chrome-tab-pool/hooks.py` 重构 tab 所有权模型
- 新增 CDP tab-pool workaround 参考文档
- `tab_pool.py` 精简，移除冗余代码

### 🧠 Learning 管线（llm_compress）
- `learning_discovery.py` — 发现逻辑增强
- `learning_distill.py` — 大幅扩展蒸馏策略（+676 行）
- `learning_store.py` — 存储层重构，支持 edit learning 去重与策略
- 新增 `learning_overview.py` 模块
- 新增测试：`test_discovery_decision_learning`、`test_edit_learning_dedupe`、`test_edit_learning_strategy`、`test_policy_delta_patch`

### 🖥️ Console 前端 UX
- `ApprovalsPage.tsx` — 审批页面大幅优化
- `ProductDetailPage.tsx` — 产品详情页重构
- `KolDetailPage.tsx` — KOL 详情页调整
- `EscalationConsolePage.tsx` — 升级控制台改进
- `LearningPage.tsx` — 学习页面更新
- 新增 `escalationLabels.ts` 常量文件（+105 行）
- `PolicyMergeDiffPreview.tsx`、`ApprovalDetailPanel.tsx` 等组件优化

### 🔌 Kol-Ops-Bridge 插件
- `cal.py` — CAL 层大幅扩展（+232 行）
- `gmail_client.py` / `gmail_reconcile.py` — Gmail 集成改进
- `plugin_api.py` — API 层扩展
- 新增 `reply_draft_kind.py`、`sku_prior_approval.py`、`discovery_gate.py`
- 删除废弃模块 `email_discover_dispatch.py`、`nox_contacts_sync.py`

### 🔀 Campaign 合并
- 新增 `merge_campaigns.py` 脚本（+180 行）及测试

### 📚 文档 & 测试
- 多个 feature guide 更新（approvals、campaigns、learning、products、gmail）
- 新增 Gmail reconcile backfill 测试（+271 行）
- 测试覆盖：bridge approval timeout、campaign config completeness、shortlist router

## 提交明细
| 短Hash | 作者 | 时间 | Message |
|--------|------|------|---------|
| 5e2f578ce | Anothorld | 17:45 +0800 | feat(kol): CDP tab ownership, llm_compress learning, and console UX |

## 变更统计
- 📁 文件数：70
- ➕ 新增行：4,007
- ➖ 删除行：622
- 📊 净增：+3,385
