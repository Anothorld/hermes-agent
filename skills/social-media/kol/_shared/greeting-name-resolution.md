## Shared greeting-name resolution

Use this same resolution order in outreach openers:

1. `identity.display_name` first token.
2. `identity.first_name` from reusable facts.
3. Parse `identity.primary_handle` heuristically:
   - Remove `@` and trailing digits/underscores.
   - Split on `.`, `_`, `-`, or space when present.
   - Else attempt camel/known-name split with confidence checks.
   - Fallback to title-cased single token.
4. If still unresolved, open escalation `kol_name_unresolvable` and abort.

Greeting format:
- First name only: `Hi <FirstName>,`
- Never use full name or raw handle.
