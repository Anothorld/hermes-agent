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

## 关联模块

- [campaigns](../campaigns/GUIDE.md) — 在产品详情页启动/管理活动
- [nox](../nox/GUIDE.md) — 产品页 shortlist 批量尽调
- `ContractReadinessPanel.tsx` — 合约就绪块

## 数据归属

SQLite 为 **权威**；活动运行态在 Bridge CAL。
