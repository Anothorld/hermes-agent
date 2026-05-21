import { Link, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { getToken, setToken } from './api';
import { LoginPage } from './pages/LoginPage';
import { ProductListPage } from './pages/ProductListPage';
import { ProductDetailPage } from './pages/ProductDetailPage';
import { KolKanbanPage } from './pages/KolKanbanPage';
import { KolDetailPage } from './pages/KolDetailPage';
import { KolRelationshipPage } from './pages/KolRelationshipPage';
import { ReplyMonitorPage } from './pages/ReplyMonitorPage';
import { SettingsPage } from './pages/SettingsPage';
import { EscalationConsolePage } from './pages/EscalationConsolePage';
import { ApprovalsPage } from './pages/ApprovalsPage';
import { PolicyEditorPage } from './pages/PolicyEditorPage';
import { CampaignWizardPage } from './pages/CampaignWizardPage';
import { CampaignCandidatesPage } from './pages/CampaignCandidatesPage';

function RequireAuth({ children }: { children: React.ReactNode }) {
  const loc = useLocation();
  if (!getToken()) return <Navigate to="/login" state={{ from: loc }} replace />;
  return <>{children}</>;
}

function Nav() {
  // Note: New Campaign / Replies entries intentionally omitted from the
  // main nav (Replies is reachable via deep link for ops review only;
  // New Campaign launches from a per-product page).  Deprecated
  // Budget / Reports / Drafts pages were removed in Phase B cleanup.
  const items = [
    ['/products', 'Products'],
    ['/kols', 'KOLs'],
    ['/escalations', 'Escalations'],
    ['/approvals', 'Approvals'],
    ['/policies', 'Policies'],
    ['/settings', 'Settings'],
  ] as const;
  return (
    <nav className="flex flex-wrap items-center gap-1 border-b border-slate-200 bg-white px-4 py-2">
      <span className="mr-4 font-semibold">KOL Ops</span>
      {items.map(([to, label]) => (
        <Link
          key={to}
          to={to}
          className="rounded px-3 py-1 text-sm text-slate-700 hover:bg-slate-100"
        >
          {label}
        </Link>
      ))}
      <button
        className="ml-auto rounded px-3 py-1 text-sm text-slate-500 hover:bg-slate-100"
        onClick={() => {
          setToken(null);
          window.location.href = '/login';
        }}
      >
        Sign out
      </button>
    </nav>
  );
}

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        path="*"
        element={
          <RequireAuth>
            <Nav />
            <main className="mx-auto max-w-7xl p-4">
              <Routes>
                <Route path="/" element={<Navigate to="/kols" replace />} />
                <Route path="/products" element={<ProductListPage />} />
                <Route path="/products/:sku" element={<ProductDetailPage />} />
                <Route path="/kols" element={<KolKanbanPage />} />
                <Route path="/kols/:id" element={<KolDetailPage />} />
                <Route path="/kols/:id/relationship" element={<KolRelationshipPage />} />
                <Route path="/campaigns/new" element={<CampaignWizardPage />} />
                <Route path="/campaigns/:id/candidates" element={<CampaignCandidatesPage />} />
                <Route path="/escalations" element={<EscalationConsolePage />} />
                <Route path="/escalations/:id" element={<EscalationConsolePage />} />
                <Route path="/approvals" element={<ApprovalsPage />} />
                <Route path="/policies" element={<PolicyEditorPage />} />
                <Route path="/replies" element={<ReplyMonitorPage />} />
                <Route path="/settings" element={<SettingsPage />} />
              </Routes>
            </main>
          </RequireAuth>
        }
      />
    </Routes>
  );
}
