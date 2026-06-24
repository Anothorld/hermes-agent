# Goals shape transform (Step 1.5 — mandatory)

`get-dispatch-context --view agent` returns **`goals` as an array** of rows.
Downstream contracts need **different shapes**. Transform once per reply after
Step 1 (and again after Step 3 re-fetch).

## 1) `goals_map` — for `select-draftable-plan` (Step 4)

From `dispatch_context.goals` (array):

```
goals_map = {}
for row in goals_array:
  goals_map[row["goal"]] = row   # keep status, lane, missing_facts, ...
```

**Never** pass the raw array to `select-draftable-plan`.

## 2) `current_goal_state` — for classifier (Step 2)

Classifier expects one active goal name per lane (hint only):

```
current_goal_state = {
  "commerce": null,
  "fulfillment": null,
  "publish": null,
  "meta": null
}
```

For each row in `goals_array` where `status` is `active`, `wait`, or `blocked`
(not `satisfied` / `skipped`):

- Set `current_goal_state[row["lane"]] = row["goal"]` when that lane slot is
  still `null`, **or** when the new row has higher lane priority within the
  same lane (commerce: outreach < product_selection < … — prefer the row with
  more `missing_facts` or lower `_GOAL_ORDER` index if tied).

Embedded poller `pending_replies[i].dispatch_context` uses the same array shape;
always prefer a fresh CLI `get-dispatch-context --view agent` over the embedded
snapshot when they differ.

## 3) `campaign_config_summary`

Subset for classifier:

```json
{
  "product_pitch": "<from campaign_config>",
  "product_url": "<campaign_config.product_url or product_link>",
  "default_compensation_mode": "<campaign_config>",
  "defer_terms_to_contract": "<campaign_config>"
}
```

## 4) `escalation_rules` (classifier Step 9)

Before Step 2:

```
python3 plugins/kol-ops-bridge/scripts/kol_bridge_tool.py get-parsed-escalation-rules \
  --env <TEST|LIVE>
```

Pass the parsed rules array (or `{rules: [...]}`) as classifier input
`escalation_rules` — required for `escalation_hint.matched_rule_id`.
