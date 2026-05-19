import { Link, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { getToken, setToken } from './api';
import { LoginPage } from './pages/LoginPage';
import { ProductListPage } from './pages/ProductListPage';
import { ProductDetailPage } from './pages/ProductDetailPage';
import { KolKanbanPage } from './pages/KolKanbanPage';
import { KolDetailPage } from './pages/KolDetailPage';
import { DraftQueuePage } from './pages/DraftQueuePage';
import { BudgetBoardPage } from './pages/BudgetBoardPage';
import { ReplyMonitorPage } from './pages/ReplyMonitorPage';
import { ContractStubPage } from './pages/ContractStubPage';
import { LogisticsStubPage } from './pages/LogisticsStubPage';
import { FunnelReportPage } from './pages/FunnelReportPage';
import { SettingsPage } from './pages/SettingsPage';

function RequireAuth({ children }: { children: React.ReactNode }) {
  const loc = useLocation();
  if (!getToken()) return <Navigate to="/login" state={{ from: loc }} replace />;
  return <>{children}</>;
}

function Nav() {
  const items = [
    ['/products', 'Products'],
    ['/kols', 'KOLs'],
    ['/drafts', 'Drafts'],
    ['/replies', 'Replies'],
    ['/budget', 'Budget'],
    ['/reports', 'Reports'],
    ['/settings', 'Settings'],
  ] as const;
  return (
    <nav className="flex items-center gap-1 border-b border-slate-200 bg-white px-4 py-2">
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
                <Route path="/" element={<Navigate to="/products" replace />} />
                <Route path="/products" element={<ProductListPage />} />
                <Route path="/products/:sku" element={<ProductDetailPage />} />
                <Route path="/kols" element={<KolKanbanPage />} />
                <Route path="/kols/:id" element={<KolDetailPage />} />
                <Route path="/kols/:id/contract" element={<ContractStubPage />} />
                <Route path="/kols/:id/logistics" element={<LogisticsStubPage />} />
                <Route path="/drafts" element={<DraftQueuePage />} />
                <Route path="/replies" element={<ReplyMonitorPage />} />
                <Route path="/budget" element={<BudgetBoardPage />} />
                <Route path="/reports" element={<FunnelReportPage />} />
                <Route path="/settings" element={<SettingsPage />} />
              </Routes>
            </main>
          </RequireAuth>
        }
      />
    </Routes>
  );
}
