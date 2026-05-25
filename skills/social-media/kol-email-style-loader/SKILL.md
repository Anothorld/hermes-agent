---
name: kol-email-style-loader
description: Side-effect-free helper skill that assembles the email generation prompt header from policy_documents (company_style + user_style) plus the mandatory `humanizer` email polish pass. Pure template — never calls an LLM, never writes CAL. Must be invoked by every outbound-email skill (cold-outreach, reengagement-outreach, interest-qualifier, product-selector, deliverables-clarifier, compensation-negotiator, contract-coordinator, shipping-intake, logistics-tracker, brief-sender, content-reviewer, golive-and-boost, escalation-resumer) at the very top of their prompt build step.
trigger: Any time a downstream skill is about to ask the LLM to draft an email or message that the operator will see / send. The skill is invoked synchronously, returns a single string block, and the caller appends it as the first section of the LLM prompt — before the goal-specific instructions.
tags: ["kol", "email", "style", "policy", "template", "humanizer", "no-llm"]
---

## Goal
Produce the **priority-ordered email generation constraints block** that
every outbound-email KOL skill prepends to its LLM prompt. The block must
make the priority contract (P0 > P1 > P2) visible to the model so style
constraints never override the email's actual goal. The block also requires
the downstream drafting skill to apply `humanizer` in email mode before it
returns or persists any subject/body.

## Runtime Contract
- **No LLM call.** This skill is pure I/O + template substitution.
- **Does not run `humanizer` itself.** It emits the mandatory instruction;
  the downstream drafting skill applies the `humanizer` email pass after
  composing the first draft and before returning the draft envelope.
- **No CAL writes.** Reads only via the deterministic bridge CLI:
  `kol_bridge_tool.py get-policy --scope company_style` and
  `kol_bridge_tool.py get-policy --scope user_style --owner-user-id <current_user_id>`.
- Always returns a fully-formed markdown block — even when one or both
  policies are empty (use the empty-doc fallback below).
- Output goes verbatim into the caller's prompt; no further escaping.

## Inputs
1. `goal_brief` — caller-provided dict:
   - `goal` (str): e.g. `compensation_negotiation`.
   - `missing_facts` (list[str]): facts this email should help collect.
   - `next_action` (str): one-sentence summary of what this email must
     accomplish (e.g. "Counter-offer at $1500 + product bundle").
2. `current_user_id` (int): owner of the personal style doc to load.
3. `bridge_base_url` (str): defaults to
   `http://localhost:<bridge_port>/api/plugins/kol-ops-bridge`.

## Output (one string)
```
## Email Generation Constraints (in priority order)

### [P0] Goal & required information (HIGHEST PRIORITY — never compromise)
- Goal: <goal_brief.goal>
- Email must communicate / collect: <comma-separated goal_brief.missing_facts>
- Specific next action: <goal_brief.next_action>

### [P1] Company style (set by admin, applies to all users)
<company_style.content_md>

### [P2] Personal style (your own preference)
<user_style.content_md>

### [P3] Humanizer email polish (mandatory final pass)
Before returning or persisting the draft envelope, apply `humanizer` in email
mode to the subject and body. Keep P0 facts exact. The polished email should:
- sound like a real person wrote it, not a generated template;
- use a warmer, more feminine voice: gentle, attentive, emotionally aware,
  and considerate without becoming overly sweet or servile;
- make the KOL feel seen and cared for, especially around timing, effort,
  shipping details, revisions, and compensation boundaries;
- remove AI tells such as generic praise, stiff transitions, rule-of-three
  phrasing, excessive bolding, emojis, and canned conclusions;
- preserve all money amounts, SKUs, deadlines, addresses, tracking numbers,
  contract terms, usage rights, hashtags, handles, and requested next actions
  exactly as drafted from P0 / campaign facts.

### Conflict resolution rules
- If P1 conflicts with P2 → follow P1 (company > personal).
- If P0 cannot be satisfied while honoring P1 → prioritize P0 and return a
  structured field `style_deviation_reason` describing the deviation.
- If P2 conflicts with P0 → silently drop the P2 element.
- If P3 would change any P0 fact → keep the original P0 fact and only polish
  the surrounding wording.
- Empty company / personal blocks render as `(no company-wide style configured)`
  / `(no personal style configured)` — do not invent constraints.
```

## Procedure
1. Resolve `bridge_base_url` from caller context (typically env var
   `HERMES_KOL_OPS_BRIDGE_BASE` or hard-coded plugin route).
2. Fetch `GET {bridge}/policies/company_style`.
   - If `policy` is null → use the empty-doc fallback string
     `(no company-wide style configured)`.
3. Fetch `GET {bridge}/policies/user_style?owner_user_id={current_user_id}`.
   - If null → use `(no personal style configured)`.
4. Substitute the five blocks (P0 lines + company body + user body + the
  mandatory humanizer email polish block + the verbatim conflict-rules
  paragraph) into the template above.
5. Return the assembled string. Caller is responsible for prepending it
   to the goal-specific prompt.

## Failure Modes (degrade gracefully)
- Bridge unreachable / 5xx → return a stub block with both P1 and P2
  sections containing the comment `(policy fetch failed — falling back to
  P0 only; please retry once the policy service is reachable)`. **Never**
  block the caller; goal communication is more important than style.
- Policy doc has empty `content_md` → render the empty-doc fallback.
- Malformed JSON response → treat as null (same as missing).

## Examples

### Success
Inputs: `goal=compensation_negotiation`, missing=`["offer.agreed_terms"]`,
next_action=`Counter-offer at $1500 + product bundle`,
company doc=`Sign every email with: Best, POVISON Team.`,
user doc=`I prefer 'Cheers,' as my closer.`

Output:
```
## Email Generation Constraints (in priority order)

### [P0] Goal & required information (HIGHEST PRIORITY — never compromise)
- Goal: compensation_negotiation
- Email must communicate / collect: offer.agreed_terms
- Specific next action: Counter-offer at $1500 + product bundle

### [P1] Company style (set by admin, applies to all users)
Sign every email with: Best, POVISON Team.

### [P2] Personal style (your own preference)
I prefer 'Cheers,' as my closer.

### [P3] Humanizer email polish (mandatory final pass)
Before returning or persisting the draft envelope, apply `humanizer` in email
mode to the subject and body. Keep P0 facts exact. The polished email should:
- sound like a real person wrote it, not a generated template;
- use a warmer, more feminine voice: gentle, attentive, emotionally aware,
  and considerate without becoming overly sweet or servile;
- make the KOL feel seen and cared for, especially around timing, effort,
  shipping details, revisions, and compensation boundaries;
- remove AI tells such as generic praise, stiff transitions, rule-of-three
  phrasing, excessive bolding, emojis, and canned conclusions;
- preserve all money amounts, SKUs, deadlines, addresses, tracking numbers,
  contract terms, usage rights, hashtags, handles, and requested next actions
  exactly as drafted from P0 / campaign facts.

### Conflict resolution rules
- If P1 conflicts with P2 → follow P1 (company > personal).
- If P0 cannot be satisfied while honoring P1 → prioritize P0 and return a
  structured field `style_deviation_reason` describing the deviation.
- If P2 conflicts with P0 → silently drop the P2 element.
- If P3 would change any P0 fact → keep the original P0 fact and only polish
  the surrounding wording.
- Empty company / personal blocks render as `(no company-wide style configured)`
  / `(no personal style configured)` — do not invent constraints.
```

(In this example the LLM should pick P1 "Best, POVISON Team" over P2
"Cheers," because of the conflict rule.)

### Failure (bridge down)
Output P1 and P2 fall back to the failure stub; P0 is still rendered.
P3 is still rendered, so the downstream email skill drafts using the goal
context and still applies the `humanizer` email pass before returning.

## Pitfalls (do NOT)
- Do **NOT** call the LLM here — this is a templating skill.
- Do **NOT** merge or rewrite the company / user content. Render verbatim.
- Do **NOT** add extra sections (footers, disclaimers). Caller may add
  those after the constraints block.
- Do **NOT** let downstream email skills skip the `humanizer` email pass;
  the pass is mandatory for every non-null KOL email subject/body.
- Do **NOT** swallow style policies that disagree with goal facts —
  surface the deviation to the next skill via `style_deviation_reason`.
- Do **NOT** cache: company / personal styles change on operator action;
  always re-fetch.
