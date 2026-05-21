# Frontend — KOL Ops Console

Vite + React 19 + TypeScript + Tailwind v4 SPA. Single WebSocket to backend
`/ws` for live updates.

```bash
cd frontend
npm install
echo "VITE_API_BASE=http://localhost:8765" > .env.local
npm run dev
```

Open http://localhost:5173. Log in with the one-time `owner@console.app` password
the backend printed on first boot.

## Pages

| Route | Component | Purpose |
|---|---|---|
| `/login`                    | `LoginPage`        | Email + password. |
| `/`                         | redirect → `/kols` | |
| `/products`                 | `ProductListPage`  | SKU catalog. Root IA. |
| `/products/:sku`            | `ProductDetailPage`| Per-SKU campaigns + start-campaign form. |
| `/kols`                     | `KolKanbanPage`    | 8-stage Kanban (single source of truth). |
| `/kols/:id`                 | `KolDetailPage`    | Timeline + generation-rationale side panel. |
| `/campaigns/new`            | `CampaignWizardPage` | Standalone candidate-pool setup flow. |
| `/campaigns/:id/candidates` | `CampaignCandidatesPage` | Discovery pool review + select. |
| `/replies`                  | `ReplyMonitorPage` | Deep-link-only reply/escalation monitor. |
| `/escalations`              | `EscalationConsolePage` | Escalation queue + decisions. |
| `/approvals`                | `ApprovalsPage`    | Approval queue for guarded facts. |
| `/policies`                 | `PolicyEditorPage` | Company/user/escalation rules docs. |
| `/settings`                 | `SettingsPage`     | Users, env toggle, audit log. |

Top progress bar (`StageProgressBar`) showing the 8 stages is rendered on
every KOL-scoped page so operators always see context.
