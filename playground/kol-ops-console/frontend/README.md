# Frontend — KOL Ops Console

Vite + React 19 + TypeScript + Tailwind v4 SPA. Single WebSocket to backend
`/ws` for live updates.

```bash
cd frontend
npm install
echo "VITE_API_BASE=http://localhost:8765" > .env.local
npm run dev
```

Open http://localhost:5173. Log in with the one-time `owner@local` password
the backend printed on first boot.

## Pages

| Route | Component | Purpose |
|---|---|---|
| `/login`                    | `LoginPage`        | Email + password. |
| `/`                         | redirect → `/products` | |
| `/products`                 | `ProductListPage`  | SKU catalog. Root IA. |
| `/products/:sku`            | `ProductDetailPage`| Per-SKU campaigns + start-campaign form. |
| `/kols`                     | `KolKanbanPage`    | 8-stage Kanban (single source of truth). |
| `/kols/:id`                 | `KolDetailPage`    | Timeline + generation-rationale side panel. |
| `/drafts`                   | `DraftQueuePage`   | Pending Gmail drafts. |
| `/budget`                   | `BudgetBoardPage`  | Per-campaign budget burndown. |
| `/replies`                  | `ReplyMonitorPage` | Live reply feed + escalations. |
| `/kols/:id/contract`        | `ContractStubPage` | Operator-driven sub-status transitions. |
| `/kols/:id/logistics`       | `LogisticsStubPage`| Address / carrier / tracking input. |
| `/reports`                  | `FunnelReportPage` | Stage funnel + cycle time. |
| `/settings`                 | `SettingsPage`     | Users, env toggle, audit log. |

Top progress bar (`StageProgressBar`) showing the 8 stages is rendered on
every KOL-scoped page so operators always see context.
