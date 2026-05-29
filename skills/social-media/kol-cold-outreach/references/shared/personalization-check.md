## Shared personalization post-check

Run after draft composition and before fact writes.

When `brief_status` is `fresh` or `refreshed`:
- Build substantive token set from creator brief facts.
- Strip HTML tags from body and check token hits in visible text.
- If no hit, regenerate once with explicit instruction to reference one
  concrete brief detail in natural language.
- Re-check; if still zero, abort with
  `{"error":"personalization_check_failed", ...}`.

When `brief_status` is `unavailable`:
- Skip token matching.
- Require envelope flags:
  - `low_personalization: true`
  - `low_personalization_reason: "creator_brief_unavailable"`
