# CDP Usage With the Local Chrome Tab Pool

## How tab ownership works

Every agent run gets exactly **one dedicated tab** in the shared local debug
Chrome, created and tracked by the `local-chrome-tab-pool` plugin. All
`browser_*` tools (navigate/click/snapshot/...) already operate inside that
tab. Cookies and login state are shared across tabs; page state is not.

The raw `browser_cdp` tool talks to the browser-level CDP socket, so the tab
pool enforces an ownership guard on it:

- `browser_cdp` calls with a page-scoped method (`Page.*`, `Runtime.*`,
  `DOM.*`, `Network.*`, `Emulation.*`, ...) and **no `target_id`** are
  automatically pinned to this run's own tab. This is the recommended way to
  use CDP.
- `browser_cdp` calls passing a `target_id` (or `params.targetId`) that
  belongs to **another run's tab are blocked** with an error telling you your
  own tab's target_id. Tabs you see in `Target.getTargets` but do not own
  belong to concurrent runs — never navigate, evaluate in, attach to, or
  close them.
- Browser-level read-only methods (`Target.getTargets`,
  `Storage.getCookies`, ...) still work without `target_id`, but their output
  must NOT be used to pick another tab to operate on.

## Recipe: CDP navigation + extraction in your own tab

### Step 1 — Navigate your own tab

```
browser_cdp(
  method="Page.navigate",
  params={"url": "https://www.jungalow.com/faqs"}
)
```

No `target_id` — the tab pool pins the call to this run's dedicated tab.
`Page.navigate` counts against the 8-page-load budget, same as
`browser_navigate`.

### Step 2 — Verify you landed on the right page

```
browser_cdp(
  method="Runtime.evaluate",
  params={"expression": "JSON.stringify({href: location.href, title: document.title, ready: document.readyState})", "returnByValue": true}
)
```

After `Page.navigate` the browser returns when navigation *starts*, not when
the page finishes loading. If `ready` is not `"complete"` (or the first
`Runtime.evaluate` errors), retry once after a brief pause.

### Step 3 — Batch extract all data in ONE Runtime.evaluate call

```
browser_cdp(
  method="Runtime.evaluate",
  params={
    "awaitPromise": true,
    "expression": "JSON.stringify({text: document.body.innerText.substring(0,4000), emails: document.body.innerText.match(/[a-zA-Z0-9._%+\\-]+@[a-zA-Z0-9.\\-]+\\.[a-zA-Z]{2,}/g)||'NONE', links: [...document.querySelectorAll('a[href]')].map(a=>({t:a.textContent.trim().substring(0,60),h:a.href.substring(0,200)})).slice(0,50)})",
    "returnByValue": true
  },
  timeout=20
)
```

Batching matters because the pool may release and recreate the tab between
long-spaced calls (inactivity cleanup). If a call fails with
"No target with given id found", repeat it without `target_id` — the pool
re-pins it to this run's current tab.

## What NOT to do (causes cross-run contamination)

- ❌ `Target.getTargets` → pick an `about:blank` or "relevant-looking" tab →
  `Page.navigate` with that `target_id`. Those tabs belong to other
  concurrent runs; hijacking them corrupts both runs' results. The guard
  blocks this, so it also wastes iterations.
- ❌ `Target.activateTarget` / `Target.closeTarget` on tabs you don't own.
- ❌ Treating a wrong-page snapshot as valid data — discard and re-navigate
  in your own tab instead.

## Historical note

Older versions of this document recommended repurposing other sessions' tabs
via `Target.getTargets`. That recipe was the primary cause of cross-run tab
contamination ("串台") and is now blocked by the tab-pool ownership guard.
