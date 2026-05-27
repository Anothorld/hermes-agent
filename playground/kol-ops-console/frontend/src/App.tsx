import { Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { getToken } from './api';
import { LoginPage } from './pages/LoginPage';
import { ProductListPage } from './pages/ProductListPage';
import { ProductDetailPage } from './pages/ProductDetailPage';
import { KolKanbanPage } from './pages/KolKanbanPage';
import { KolArchivePage } from './pages/KolArchivePage';
import { KolDetailPage } from './pages/KolDetailPage';
import { KolRelationshipPage } from './pages/KolRelationshipPage';
import { ReplyMonitorPage } from './pages/ReplyMonitorPage';
import { SettingsPage } from './pages/SettingsPage';
import { EscalationConsolePage } from './pages/EscalationConsolePage';
import { ApprovalsPage } from './pages/ApprovalsPage';
import { PolicyEditorPage } from './pages/PolicyEditorPage';
import { CampaignCandidatesPage } from './pages/CampaignCandidatesPage';
import { AgentTranscriptPage } from './pages/AgentTranscriptPage';
import { GlobalNav } from './components/shell/GlobalNav';
import { EnvBanner } from './components/shell/EnvBanner';
import { BreadcrumbBar } from './components/shell/BreadcrumbBar';
import { DialogHost } from './components/dialogs/DialogHost';
import { ToastHost } from './components/feedback/ToastHost';
import { AgentSessionDock } from './components/agent-dock/AgentSessionDock';

function RequireAuth({ children }: { children: React.ReactNode }) {
  const loc = useLocation();
  if (!getToken()) return <Navigate to="/login" state={{ from: loc }} replace />;
  return <>{children}</>;
}

export function App() {
  return (
    <>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route
          path="*"
          element={
            <RequireAuth>
              <EnvBanner />
              <GlobalNav />
              <BreadcrumbBar />
              <main className="mx-auto max-w-7xl p-4">
                <Routes>
                  <Route path="/" element={<Navigate to="/kols" replace />} />
                  <Route path="/products" element={<ProductListPage />} />
                  <Route path="/products/:sku" element={<ProductDetailPage />} />
                  <Route path="/kols" element={<KolKanbanPage />} />
                  <Route path="/kols/archive" element={<KolArchivePage />} />
                  <Route path="/kols/:id" element={<KolDetailPage />} />
                  <Route path="/kols/:id/relationship" element={<KolRelationshipPage />} />
                  <Route path="/campaigns/new" element={<Navigate to="/products" replace />} />
                  <Route path="/campaigns/:id/candidates" element={<CampaignCandidatesPage />} />
                  <Route path="/campaigns/:cid/transcript" element={<AgentTranscriptPage />} />
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
      <AgentSessionDock />
      <DialogHost />
      <ToastHost />
    </>
  );
}
