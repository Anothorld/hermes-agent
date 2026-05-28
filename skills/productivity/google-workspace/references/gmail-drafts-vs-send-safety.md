# Gmail drafts vs send — safety note

Use this reference when a workflow requires draft-only Gmail behavior.

Key lesson:
- A wrapper advertising Gmail support does not automatically imply draft support.
- Before any side-effecting action, inspect the exact command surface you will call.
- If the available wrapper only exposes send/reply flows, that is not an acceptable substitute for draft creation.

Safety checklist:
1. Verify auth with the setup script.
2. Inspect the wrapper help/docs or source for explicit draft support (`drafts.create`, `drafts.update`, or equivalent).
3. If missing, escalate instead of probing with a real send.
4. Remember that TEST MODE inboxes are still real recipients; sending there is still a send.

Common failure mode:
- The agent sees Gmail auth working and a `gmail send` helper available, then uses it to "simulate" a draft flow to the test inbox. This violates any draft-only workflow and can create unintended sent-mail state.
