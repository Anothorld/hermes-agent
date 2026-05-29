## Shared router/dispatcher boundaries

- Router/dispatcher skills coordinate and persist state; they do not send mail.
- Initial-outreach drafting is done by outreach draft skills.
- Reply drafting is done by reply-side child skills, then merged/enriched by
  dispatcher before persistence.
- Escalations and approvals are durable operator touchpoints; child-skill
  routing should not bypass them.
