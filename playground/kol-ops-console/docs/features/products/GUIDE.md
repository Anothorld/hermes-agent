# 产品（SKU）

## 功能说明

本地 **产品目录**：SKU、标题、变体矩阵等。活动（Campaign）挂在 SKU 下；是操作员 IA 的根入口（「产品」导航）。

## 操作员路径

| 路径 | 页面 |
|------|------|
| `/products` | `ProductListPage.tsx` |
| `/products/:sku` | `ProductDetailPage.tsx`（含下属活动列表） |

## 关键文件

| 层 | 文件 |
|----|------|
| FE | `pages/ProductListPage.tsx`, `pages/ProductDetailPage.tsx` |
| BE | `routers/products.py` |
| 核心 | `product_variants.py`, `variant_candidates.py`, `db.py`（`products`, `product_campaigns`） |

## 主要 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET/POST | `/products` | 列表/创建 SKU |
| GET/PATCH | `/products/{sku}` | 详情/更新 |
| GET | `/products/{sku}/campaigns` | SKU 下活动 |
| GET/PUT | `/learning/product-categories[/{sku}]` | 产品页「品类」字段（`ProductCategoryField.tsx`，桥代理；用于发现学习按品类泛化，人工修正优先于 LLM 建议） |

## 关联模块

- [campaigns](../campaigns/GUIDE.md) — 在产品详情页启动/管理活动；Shortlist review（`candidate_count` / `pending_candidate_count` 来自 Bridge `list-candidate-handles`，含 `discovered` 池；**不是** `get_lanes`；未批准候选持久显示，操作员可「从 shortlist 移除」，移除/批准/转移均需打原因标签，见 [learning](../learning/GUIDE.md)）
- **一个产品只允许一个 campaign**（见 [campaigns GUIDE「概念」](../campaigns/GUIDE.md#概念)）：`/start` 对同 SKU 第二个 campaign_id 返回 409；启动表单默认回填已有 campaign_id；历史多 campaign 数据用 `scripts/ops/merge_campaigns.py` 合并。合并前的遗留双活动场景仍有兜底：已在同 SKU 其他活动批准过的 KOL 不会出现在「待审批（本轮）」（`prior_sku_approved_in_pending` / `prior_sku_approved_hidden_count`），discovery brief 也会排除这些 handle。
- [nox](../nox/GUIDE.md) — 产品页 shortlist 批量尽调
- `ContractReadinessPanel.tsx` — 合约就绪块

## 数据归属

SQLite 为 **权威**；活动运行态在 Bridge CAL；品类映射在 Bridge `product_category_map`。
