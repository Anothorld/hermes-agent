# Deprecated KOL outreach docs (v1.1, Kanban + review-gate)

> **⚠️ DEPRECATED — DO NOT FOLLOW.**
>
> Superseded by the lightweight v2 design:
> [`hermes-agent/skills/social-media/kol-outreach-orchestrator-flow/SKILL.md`](../../skills/social-media/kol-outreach-orchestrator-flow/SKILL.md)
> plus its [`SETUP.md`](../../skills/social-media/kol-outreach-orchestrator-flow/SETUP.md).
>
> The files in this folder describe the v1.1 Kanban pipeline with
> per-step "review gate" tasks (`campaign anchor`, `review campaign brief
> and assumptions`, `review creator shortlist`, `safety / mode config`,
> `kol-scout` assignee, etc.). That pattern is **abandoned** because the
> per-KOL unblock-and-complete overhead made it unusable.
>
> **Do not recreate any task whose title appears in these files.**
> Cards on the `kol-outreach` board now serve only as per-KOL indexes
> (`title: kol:<handle>`); the orchestrator skill owns the flow and Gmail
> drafts are the review surface.
>
> Kept here for historical reference only.
