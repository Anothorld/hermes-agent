When resolving creator outreach email from public surfaces, two recurring non-hit patterns should be treated as misses, not weak hits.

1. Platform support addresses on creator-branded commerce pages
Stan pages often expose `friends@stanwith.me` in error/help copy. This belongs to Stan support, not the creator, even when the page title is the creator name and handle. Do not persist it as `primary_email`.

2. Creator-owned contact pages with forms only
Creator sites may have `/contact/` or service pages such as `/design-consultations/` that visibly belong to the creator but only expose a form. Record these URLs in `tried` because they are relevant audit evidence, but absence of a visible address still means `found=false` for this skill.

Worked example from session:
- Identity handle: `mysweetsavannah`
- IG bio confirmed creator identity and linked site presence
- Personal site pages checked: homepage, `/about-me/`, `/contact/`, `/design-consultations/`
- Stan page checked: `https://stan.store/mysweetsavannah`
- False positive observed: `friends@stanwith.me`
- Correct outcome: miss + `contact_email_not_found` escalation with full `tried` list
