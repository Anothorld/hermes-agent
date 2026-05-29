## Shared reply envelope contract

For reply-side skills (non-initial outreach):

- Return a single JSON draft envelope (no prose wrappers).
- Include `thread_id` when replying in-thread.
- Do not set `to` or `subject`; `kol-reply-dispatcher` enriches from inbound.
- Include `facts_written` and branch metadata for audit/debug.
