# KOL Ops Console

External Web app that drives the Hermes `kol-ops-bridge` plugin. The agent owns
the conversation; this console owns the human approval & audit surface.

Layout:
- `backend/` — FastAPI + SQLite. Own auth (JWT + bcrypt), own RBAC
  (Owner/Operator/Viewer), own approvals / notes / audit log. Talks to the
  Hermes bridge via `X-Bridge-Key` and to the Hermes gateway via
  `Authorization: Bearer <key>` for run spawning.
- `frontend/` — Vite + React 19 + TypeScript + Tailwind v4. SPA, single
  `/ws` connection for live updates.

Two SQLite DBs by design:
- `~/.hermes/kol-ops-bridge/cal.db` — Conversation Audit Layer (Hermes-owned).
- `~/.hermes/kol-ops-console/app.db` — Web app local state (operators,
  approvals, notes, audit log). Never touched by Hermes.

See `backend/README.md` and `frontend/README.md` for setup.
