OVERRIDE SUBAGENT DEFAULT: Ignore instructions to write a prose summary,
list files, or describe "What I did". Your FINAL message — the only text
returned to the parent as `results[0].summary` — MUST be ONLY the raw
classifier JSON object matching `templates/classifier-output.json` shape.
No markdown fence. No heredoc files. Classify immediately.

Inputs (fill from dispatcher Step 2):
- latest_email: {{LATEST_EMAIL_JSON}}
- thread_history: {{THREAD_HISTORY_JSON}}
- anomaly_signals: {{ANOMALY_SIGNALS_JSON}}
- current_goal_state: {{GOALS_JSON}}
- campaign_facts: {{CAMPAIGN_FACTS_JSON}}
- campaign_config_summary: {{CAMPAIGN_CONFIG_JSON}}

Return **only** the populated classifier JSON as your **final** message.
