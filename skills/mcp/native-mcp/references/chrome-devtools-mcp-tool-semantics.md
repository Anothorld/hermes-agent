# chrome-devtools-mcp tool semantics (vs Hermes built-in `browser_*`)

When migrating workflows from Hermes's built-in `browser_*` toolset to
`chrome-devtools-mcp` (registered as `mcp_chrome_devtools_*`), the
high-level capabilities overlap but the exact call shapes don't. Skills,
prompts, or runbooks written against `browser_*` need rewriting at the
call-site level — not just a rename.

## Mental-model shift

`chrome-devtools-mcp` is built around **page snapshots**. Most write
operations (click, fill, hover, etc.) take a `uid` that comes from
`take_snapshot`'s output, not a CSS selector or `ref` token. uids are
**ephemeral**: any navigation, full reload, or significant DOM mutation
invalidates them. The reliability rule is:

> Re-`take_snapshot` after any `navigate_page`, after any click that
> caused a route change, and any time you've waited for new content to
> render. Then look up the new uid by accessible name / role and use
> THAT uid for the next action. Never carry a uid across an action that
> might have caused navigation.

`browser_*` instead exposes `ref` tokens that are stable across many DOM
mutations and sometimes across navigations. Code that assumed that
stability will silently click the wrong element under chrome-devtools.

## Call-site mapping

| `browser_*` (built-in)              | `mcp_chrome_devtools_*` equivalent                                | Notes |
|---|---|---|
| `browser_navigate(url=...)`         | `navigate_page(url=...)`                                          | Behaves equivalently. After this call, any prior uid is dead. |
| `browser_back()`                    | `navigate_page(navigate="back")`                                  | **Pitfall:** unreliable on SPA-heavy sites (Instagram, X, infinite-scroll feeds). Prefer re-`navigate_page(url=<canonical>)` with the full URL captured before you went deeper. |
| `browser_console(expression="...")` | `evaluate_script(function="() => { ... }")`                       | Must be a **function body**, not a bare expression. Use an arrow function or `function () { ... }`. The return value is the JSON-serialized result. |
| `browser_type(ref=..., text=...)`   | `fill(uid=..., value=...)`                                        | uid is from latest snapshot. To clear a field first, look at chrome-devtools's `fill` semantics — it replaces the field's value, not appends. |
| `browser_click(ref=...)`            | `click(uid=...)`                                                  | Re-snapshot if the click navigates. |
| `browser_screenshot()`              | `take_screenshot()`                                               | Largely equivalent. |
| `browser_get_text()` / DOM scrape   | `take_snapshot()` (returns accessibility tree) or `evaluate_script` for raw HTML | Snapshot returns an a11y-flavored view, not raw HTML. For full HTML use `evaluate_script(function="() => document.documentElement.outerHTML")`. |
| `browser_vision(...)`               | **No direct equivalent.**                                         | If you need vision-on-screenshot, take a screenshot then pass it to a vision tool yourself. |
| `browser_get_images(...)`           | **No direct equivalent.**                                         | Same — pull `<img>` URLs via `evaluate_script` and download them with the file/network tools. |
| `browser_wait_for(...)`             | `wait_for(text=...)` / `wait_for(uid=...)` (per chrome-devtools-mcp's tool list) | Confirm exact arg names from `hermes mcp test chrome_devtools` — they evolve. |
| `browser_close()`                   | Not generally needed; the persistent debug Chrome stays open across sessions. | Don't quit Chrome between calls — it's the user's logged-in profile. |

## Workflow-level differences worth knowing

- **Login state** — with `chrome-devtools-mcp` connected to a real Chrome via CDP (`--browserUrl http://127.0.0.1:9222`), you inherit whatever sessions that profile is logged into. `browser_*` cloud providers (Browserbase, Browser Use) hand you a fresh profile each call. Skills that previously assumed "always-fresh, anonymous" need to be rewritten to assume "stateful, possibly-already-logged-in." Login walls become a legitimate code path: take_snapshot → check for login form → bail out and ask the user to log in once in the debug Chrome.
- **Bot detection** — driving the user's real Chrome looks more human than a cloud-browser fingerprint, but it isn't invisible. Heavy clicking on detection-sensitive sites (IG, TikTok feeds, ticketing) still trips rate limits. Throttle and randomize.
- **Virtualized lists / infinite scroll** — `evaluate_script` with `window.scrollTo` works, but the snapshot only includes currently-rendered elements. To process an entire feed you must scroll → wait → snapshot → process → scroll again, in a loop. There is no equivalent to a "give me the whole DOM tree" call.
- **Stop-retrying rule** — if two consecutive identical chrome_devtools calls fail, do not try a third. Re-snapshot or re-navigate first; the world has shifted under you.
- **No per-call `profile_id`** — you cannot switch identity per call the way you could with `browser_*` cloud profiles. The CDP browser is one identity. If you need multiple identities, run multiple debug Chromes on different ports and register multiple MCP servers (`chrome_devtools_a` / `chrome_devtools_b`).

## How to verify the live tool list

The exact arg names and tool count drift between chrome-devtools-mcp
versions. To confirm what's actually registered:

```bash
hermes mcp list
hermes mcp test chrome_devtools
```

The test output enumerates every tool the MCP server reports. Use that
as the authoritative reference if anything in the table above looks off
— upstream changes faster than this skill.

## Migration checklist (skill / runbook author)

When porting a `browser_*`-based skill to `mcp_chrome_devtools_*`:

1. Search the skill for `browser_` references, including in tags and pitfalls — easy to miss if you only scan the body.
2. Replace each call-site per the table. **Don't bulk sed**; the arg-name and arg-shape changes are not 1:1.
3. After every `navigate_page` / `click` that might navigate, insert an explicit re-snapshot step.
4. Replace any "browser_back to return to feed" pattern with "capture canonical URL before going deep, navigate_page(url=...) to return."
5. Add the WSL+Windows-Chrome setup note (or link to native-mcp's main SKILL.md section) if the skill assumes the user has the bridge running.
6. Grep one more time for the deprecated names. They hide in references too — pin a `DEPRECATED` banner on any old reference you don't want to delete.
