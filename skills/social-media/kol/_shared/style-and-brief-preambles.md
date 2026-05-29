## Shared preambles: style + creator brief

For drafting skills that personalize outreach copy:

1. Invoke `kol-email-style-loader` first.
2. Prepend its returned block verbatim as prompt section `[P0]`.
3. Invoke `kol-creator-brief-loader` next.
4. Prepend that output as `[P0.1]` immediately after `[P0]`.

Prompt section order:
`[P0]` -> `[P0.1]` -> `[P1]` -> `[P2]` -> `[P3]`

Failure behavior:
- Loader failures must not block drafting.
- If creator brief is unavailable, allow generic draft but require
  low-personalization envelope flags.
