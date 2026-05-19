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

First boot auto-creates an `owner@local` user with a random password printed
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
    kols.py          /kols -> bridge identities + local notes merged.
    timeline.py      /kols/{id}/timeline -> bridge passthrough.
    drafts.py        /drafts/pending -> bridge passthrough + local approval flag.
    contract.py      stub workflow endpoints.
    logistics.py     stub workflow endpoints.
    content.py       approve/revise verdict + bridge write.
    campaigns.py     start campaign (proxies to gateway /v1/runs).
    events.py        WebSocket /ws and SSE bridge relay.
    admin.py         wipe-test, audit log.
```

## RBAC

| Role | Read | Approve drafts | Push contract/logistics | Start campaigns | Wipe TEST | Manage users |
|---|---|---|---|---|---|---|
| Owner    | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Operator | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ |
| Viewer   | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |

Every mutating request writes one `audit_log` row.
