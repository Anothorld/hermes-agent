# Classifier handoff checklist (Step 2 → 2.5 → 3)

Load once per reply:
- `skill_view(name="kol-email-stage-classifier", file_path="templates/classifier-output.json")`
- `skill_view(name="kol-reply-dispatcher", file_path="templates/write-facts-multi-body.json")`

## Step 2 — Classify

- [ ] Load `templates/goals-shape-transform.md`
- [ ] Build `goals_map` + `current_goal_state` from `goals[]`
- [ ] Run `get-parsed-escalation-rules` → `escalation_rules` for classifier
- [ ] **Path B:** `delegate_task` with `templates/delegate-task-context.md` override block filled in
- [ ] Parse: inline assistant JSON **or** `results[0].summary` from delegate_task
- [ ] Validate: `facts_extracted` + `signals` present → store full object as `classifier_result`
- [ ] On parse fail: internal retry (≤3 total); **no** operator escalation

## Step 2.5 — Validate (deterministic)

- [ ] Map `classifier_result.facts_extracted.*` → `namespaces` (all 5 keys)
- [ ] Run `kol_bridge_tool.py sanitize-classifier-facts` with mapped namespaces + signals
- [ ] Use sanitized `namespaces` for Step 3

## Step 3 — Persist

- [ ] `write-facts-multi` immediately (same turn), using write-facts-multi-body shape
- [ ] Replace template `"signals": []` with `classifier_result.signals` verbatim (array)
- [ ] `source` = `email:<message_id>`
- [ ] Re-fetch `get-dispatch-context --view agent`
- [ ] Steps 3.25 / 3.5 use `classifier_result.risk_controls` and `escalation_hint`

## Never

- Read `/tmp/classification_result.json`
- Re-run Step 2 after successful parse
- Open escalation for JSON format / parse failures
