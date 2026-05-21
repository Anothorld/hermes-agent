# Backend — KOL Ops Console

FastAPI app, JWT auth, SQLite local state, proxies / merges data from the
Hermes `kol-ops-bridge` plugin.

## Quick start

```bash
cd backend
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt

export KOC_BRIDGE_BASE=http://127.0.0.1:8080/api/plugins/kol-ops-bridge
export KOC_BRIDGE_KEY=<same value as HERMES_KOL_OPS_BRIDGE_KEY>
export KOC_GATEWAY_BASE=http://127.0.0.1:8642
export KOC_GATEWAY_KEY=<API_SERVER_KEY>
export KOC_JWT_SECRET=$(python3 -c "import secrets;print(secrets.token_urlsafe(32))")
export KOC_DB_PATH=$HOME/.hermes/kol-ops-console/app.db

uvicorn app.main:app --reload --port 8765
```

First boot auto-creates an `owner@console.app` user with a random password printed
to stdout. Rotate immediately.

## Layout

```
app/
  main.py            FastAPI factory + WebSocket multiplex.
  config.py          pydantic-settings.
  deps.py            DI for db / bridge / current_user.
  db.py              SQLite init + connection helper.
  security.py        password hashing + JWT.
  bridge_client.py   thin httpx wrapper to the Hermes plugin API.
  gateway_client.py  thin httpx wrapper to /v1/runs.
  models/            ORM-free row helpers (sqlite3.Row dicts).
  routers/
    auth.py          login / refresh / me.
    products.py      SKU catalog (local table).
    kols.py          /kols + /kols/{id} detail payloads.
    campaigns.py     launch / close / shortlist approval.
    candidates.py    campaign discovery-pool CRUD + select.
    facts.py         fact reads / writes.
    goals.py         goal-state + dispatch-context reads.
    relationships.py relationship + reusable-facts + archive helpers.
    escalations.py   list/open/resolve escalations.
    approvals.py     pending approval.* facts.
    policies.py      company/user/escalation policy docs.
    events.py        reply-monitor APIs + WebSocket /ws.
    admin.py         wipe-test, audit log.
```

## RBAC

| Role | Read | Approvals / escalations | Start campaigns | Wipe TEST | Manage users |
|---|---|---|---|---|---|
| Owner    | ✅ | ✅ | ✅ | ✅ | ✅ |
| Operator | ✅ | ✅ | ✅ | ❌ | ❌ |
| Viewer   | ✅ | ❌ | ❌ | ❌ | ❌ |

Every mutating request writes one `audit_log` row.
