# ads 数据库表结构与常用查询

> 最后更新: 2026-06-09 | 数据库: ads (阿里云 MySQL RDS)

## 核心宽表（预聚合，数据较及时）

### ads_sensors_event_group — 神策事件聚合表

最常用于 UV/PV/加购/下单等流量指标查询，预计算好的多维度聚合数据。

**⚠️ UV 口径陷阱**：
- `page_view_uv` 是按渠道分别去重的，各渠道 UV 之和远大于汇总行 UV
- 汇总行 `page_view_uv` 包含匿名设备ID，数值约为"登录用户 UV"的 2 倍
- **查 UV 优先从明细表 `dws_wa_sensors_product_view_di` 用 `COUNT(DISTINCT user_id)` 计算**
- user_id 有两套体系：纯数字=登录用户（~50%），负数字符串=匿名访客（~50%），需确认用户口径

**主键维度组合**：calculate_date + time_dimension + platform_name + site + sku_dimension + spu_code + business_division_name + category_name + channel_dimension + channel_source_type + channel_name + detail_channel

**维度字段**：

| 字段 | 说明 | 取值 |
|------|------|------|
| calculate_date | 计算日期 | date 类型，如 '2026-06-08' |
| time_dimension | 时间维度 | 昨日/上周/上月/本周/本月/本季/本年/近7天/近30天 |
| platform_name | 平台 | 独立站 |
| site | 站点 | us/uk/ca/au/de/fr/it/sg/EU |
| sku_dimension | 商品维度 | SPU维度/事业部维度/品类维度/汇总商品维度/新品品类/新品汇总 |
| channel_dimension | 渠道维度 | 渠道汇总维度/渠道平台维度/渠道类型维度 |
| channel_source_type | 渠道来源类型 | 广告/内容/EDM/其他/汇总 |
| channel_name | 渠道名称 | Google/Meta/DSP/Pinterest/Bing/TikTok/SEO/EDM/其他/汇总 |
| detail_channel | 详细渠道 | 1-GG/2-FB/3-BING/4-PINS/5-TT&RD/6-DSP/SEO/edm/其他/汇总 |

**指标字段**：page_view_uv/pv, product_detail_view_uv/pv, ads_detail_view_uv/pv, add_to_cart_uv/pv, detail_add_to_cart_uv/pv, ads_add_to_cart_uv/pv, place_order_uv/pv, checkout_uv/pv, sort_page_view_uv/pv, sort_page_click_uv/pv, first_visit_uv/pv, spot_detail_view_uv/pv

**渠道映射关系**：
- Applovin → DSP 渠道 (detail_channel = '6-DSP')
- Google Ads → Google (1-GG)
- Facebook/Meta Ads → Meta (2-FB)
- Bing Ads → Bing (3-BING)
- Pinterest Ads → Pinterest (4-PINS)
- TikTok Ads → TikTok (5-TT&RD)

---

## 明细表

### dws_wa_sensors_product_view_di — 产品浏览汇总日表（UV 查询首选）

用户级别的产品浏览数据，适合做爬虫识别、用户 PV 分布分析、精确 UV 计算。

| 字段 | 类型 | 说明 |
|------|------|------|
| platform_name | varchar(64) | 平台（独立站） |
| site | varchar(32) | 站点（us/uk/ca 等） |
| user_id | varchar(128) | 用户ID（有索引） |
| spu_code | varchar(64) | SPU编码 |
| business_division_name | varchar(128) | 事业部 |
| category_name | varchar(128) | 品类 |
| pv | bigint | 页面浏览量 |
| dt | varchar(8) | 日期分区（如 '20260608'） |

**user_id 双体系（关键！）**：

| ID 类型 | 示例 | 特征 | 说明 |
|---------|------|------|------|
| 登录用户 | `6318201` | 纯数字，REGEXP `^[0-9]+$` | 注册用户ID，约占总UV 50% |
| 匿名访客 | `-1000270620181847142` | 负数字符串，长度16~20位 | 设备ID，未登录访客，约占总UV 50% |

两套 ID **完全无交叉**，不同工具可能只统计其中一种，导致 UV 数值差异巨大。

**爬虫识别经验值**：日 PV > 100 的用户可视为疑似爬虫（实测 2026-06-08 美站仅 42 个，占比 0.04%）。

**推荐 UV 查询模式**：

```sql
-- 美站 UV（排除爬虫，区分登录/匿名）
SELECT
  COUNT(DISTINCT CASE WHEN total_pv <= 100 AND user_id REGEXP '^[0-9]+$' THEN user_id END) AS uv_logged_no_crawler,
  COUNT(DISTINCT CASE WHEN user_id REGEXP '^[0-9]+$' THEN user_id END) AS uv_logged_all,
  COUNT(DISTINCT CASE WHEN total_pv <= 100 THEN user_id END) AS uv_total_no_crawler,
  COUNT(DISTINCT user_id) AS uv_total
FROM (
  SELECT user_id, SUM(pv) AS total_pv
  FROM ads.dws_wa_sensors_product_view_di
  WHERE site = 'us' AND dt = '20260608'
  GROUP BY user_id
) t
```

### dwd_wa_sensors_user_action_di — 用户行为明细表

⚠️ **数据延迟严重**：实测最新数据仅到 2026-05-13，不可用于近实时查询。

| 字段 | 类型 | 说明 |
|------|------|------|
| action_name | varchar(128) | 事件名称 |
| event_code | varchar(128) | 事件编码 |
| source_sku / source_spu | varchar(256) | 来源 SKU/SPU |
| user_id | varchar(64) | 用户ID |
| utm_source | varchar(128) | 流量来源（google/facebook/applovin 等） |
| distinct_id | varchar(128) | 设备ID |
| is_first_event | varchar(32) | 是否首次事件 |
| event_time | varchar(128) | 事件时间 |
| dt | varchar(32) | 日期分区 |

**注意**：此表有 utm_source 字段可精确过滤 Applovin 等广告来源，但因数据延迟不适用近期查询。

### dwd_sensor_event_di — 传感器事件日表

无 utm_source 字段，但数据较及时。

| 字段 | 说明 |
|------|------|
| site | 站点 |
| pv/uv | 全站页面浏览 |
| product_detail_pv/uv | 商品详情浏览 |
| ads_detail_pv/uv | 广告详情浏览 |
| add_to_cart / product_add_to_cart / ads_add_to_cart | 加购指标 |
| dt | 日期分区（varchar(10)） |

---

## 维度表

| 表名 | 说明 |
|------|------|
| dim_date | 日期维度 |
| dim_product_sku / dim_product_spu | 商品维度 |
| dim_platform_sku | 平台 SKU 映射 |
| dim_source_sku | 来源 SKU 映射 |
| dim_promotion_channel | 促销渠道维度 |
| dim_warehouse_sku | 仓库 SKU 维度 |
| dim_wms_warehouse | WMS 仓库维度 |
| dim_advertisement_source | 广告来源维度 |
| dim_carrier_info | 承运商信息 |
| ads_dim_site | 站点维度 |
| ads_dim_platform | 平台维度 |
| ads_dim_category | 品类维度 |
| ads_dim_business_division | 事业部维度 |

---

## 订单与销售表

### dws_transaction_standard_order_item_info — 标准订单明细（GMV 查询首选）

近实时数据（最新可达当天），含有效/无效标记和清洗后的付款时间。

| 字段 | 类型 | 说明 |
|------|------|------|
| site | varchar(255) | 站点（**大写**：'US'/'UK'/'EU' 等） |
| platform | varchar(255) | 平台 |
| order_id | varchar(255) | 订单ID |
| order_status | varchar(255) | 订单状态：complete/processing/holded/pending/pending_payment/payment_review/canceled/closed/refunded |
| is_valid | varchar(16) | 有效性：'有效'/'无效'/NULL |
| payment_create_date | varchar(255) | 原始付款时间 |
| payment_create_date_clean | varchar(25) | 清洗后付款时间（有索引），格式 '2026-06-08 14:30:00' |
| order_create_date | varchar(255) | 下单时间 |
| total_order_amt | decimal(20,6) | 订单行金额 |
| unit_sale_price | decimal(20,2) | 单价 |
| unit_discount_amount | decimal(20,2) | 折扣 |
| unit_delivery_amount | decimal(10,2) | 运费 |
| qty | int | 数量 |
| currency | varchar(255) | 币种（美站为 USD） |
| source_sku / product_sku | varchar(255) | 来源SKU / 产品SKU |
| spu_code | varchar(255) | SPU编码 |
| business_division_name | varchar(255) | 事业部 |
| lvl1_name / lvl2_name / lvl3_name | varchar(255) | 品类层级 |

**GMV 标准查询模式**：

```sql
-- 美站昨日 GMV（排除 price_1 和 TPVIP1）
SELECT '2026-06-08' AS pay_date,
  SUM(total_order_amt) AS gmv,
  COUNT(DISTINCT order_id) AS order_cnt,
  COUNT(*) AS sku_cnt
FROM ads.dws_transaction_standard_order_item_info
WHERE site = 'US'
  AND is_valid = '有效'
  AND DATE(payment_create_date_clean) = '2026-06-08'
  AND source_sku NOT IN ('price_1', 'TPVIP1')
```

**常见需排除的 SKU**：
- `price_1`：差价补款，金额小，非真实商品交易
- `TPVIP1`：VIP 会员费，非商品订单

---

### ads_fact_order_sku — 订单 SKU 明细

字段更丰富（含 utm_source、退款信息、成本等），site 值为**小写**（'us'）。数据更新略滞后于 dws_transaction_standard_order_item_info。

| 关键字段 | 说明 |
|---------|------|
| site | 站点（**小写**：'us'/'uk' 等） |
| is_valid | '有效'/'无效' |
| payment_create_date | 付款时间 |
| 15_utm_source / first_utm_source / last_utm_source | 流量来源归因 |
| refund_status / refund_type | 退款状态 |
| unit_total_cost / cost_price / unit_cost_price | 成本相关 |
| sensors_user_id | 神策用户ID（可关联流量表） |

---

| 表名 | 说明 |
|------|------|
| ads_fact_goal_sales / ads_fact_goal_sales_type | 销售目标实绩（目标表，非实绩） |
| ads_transaction_merchandise_info | 交易商品信息 |
| ads_refund_product_sku_detail | 退款明细 |

---

## 库存表

| 表名 | 说明 |
|------|------|
| ads_inventory_detail | 库存明细 |
| ads_inventory_age_info_df | 库龄信息 |
| ads_product_sku_inventory_info_df | SKU 库存信息 |
| ads_order_onhand_inventory_info_df | 在途+在手库存 |

---

## site 字段大小写问题

不同表 site 字段大小写不一致，查询前务必确认：

| 表 | site 值 | 示例 |
|----|---------|------|
| dws_transaction_standard_order_item_info | **大写** | 'US', 'UK', 'EU' |
| ads_fact_order_sku | 小写 | 'us', 'uk', 'eu' |
| dws_wa_sensors_product_view_di | 小写 | 'us', 'uk', 'eu' |
| ads_sensors_event_group | 小写 | 'us', 'uk', 'eu' |

**建议**：对不熟悉的表，先执行 `SELECT DISTINCT site FROM <table>` 确认取值。

---

## 时区注意事项

- RDS 实例时区：**+08:00（北京时间）**
- dt 分区按北京时间切日
- 美站数据：dt='20260608' 并非美东时间的6月8日，需考虑 ~12 小时时差
- 查询前务必与用户确认时间口径（北京时间 vs 目标站点当地时间）

---

## 查询优先级建议

1. **UV/PV 查询**：优先用 `dws_wa_sensors_product_view_di` 明细表做 COUNT(DISTINCT user_id)，注意 user_id 双体系口径
2. **GMV 查询**：优先用 `dws_transaction_standard_order_item_info`（近实时），排除 price_1/TPVIP1 等非商品 SKU
3. **渠道维度流量**：用 `ads_sensors_event_group` 查看各渠道分布趋势，但注意 page_view_uv 不可跨渠道直接相加
4. **销售目标**：用 `ads_fact_goal_sales_type` 等预聚合表
5. **避免用延迟表**：`dwd_wa_sensors_user_action_di` 数据严重滞后，仅在需要历史 utm_source 数据时使用
6. **先查最新分区**：对任何表先执行 `SELECT MAX(dt) FROM <table>` 确认数据可用性
