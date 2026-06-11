# Ops scripts

一次性 / 低频运维操作的确定性工具（替代 ad-hoc SQL，见 guardrails §4）。

| 脚本 | 用途 |
|------|------|
| `merge_campaigns.py` | 把同一产品的两个 campaign 合并为一个（one-product-one-campaign 迁移）。自动备份 `app.db` + `cal.db`；Bridge 侧调 `POST /campaigns/{target}/merge-from`（target 行优先，保留操作员审批）；Console 侧迁移 `product_campaign_runs`、删除源 `product_campaigns` 行、写 `campaign.merge` audit。 |

```bash
# dry-run 预览
python3 merge_campaigns.py --source SEB8010-20260610 --target SEB8010-20260608 --env LIVE --dry-run
# 执行（要求 bridge 在运行，.env 提供 KOC_BRIDGE_BASE/KOC_BRIDGE_KEY）
python3 merge_campaigns.py --source SEB8010-20260610 --target SEB8010-20260608 --env LIVE
```

合并后重启 Console 后端；当 `product_campaigns` 不再有同 SKU 重复行时，`init_db`
会自动创建 `UNIQUE(sku, env)` 索引。
