import { useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { setToken } from '../../api';
import { dialog } from '../dialogs/useDialog';
import { CampaignPicker } from './CampaignPicker';
import { EnvSwitch } from './EnvSwitch';

const ITEMS = [
  ['/products', '产品'],
  ['/kols', 'KOL'],
  ['/kols/archive', '历史合作'],
  ['/approvals', '待审批'],
  ['/escalations', '升级'],
  ['/policies', '策略'],
  ['/settings', '设置'],
] as const;

function pickActive(pathname: string): string | null {
  let best: string | null = null;
  for (const [to] of ITEMS) {
    if (pathname === to || pathname.startsWith(`${to}/`)) {
      if (!best || to.length > best.length) best = to;
    }
  }
  return best;
}

export function GlobalNav() {
  const loc = useLocation();
  const navigate = useNavigate();
  const [menuOpen, setMenuOpen] = useState(false);

  async function signOut() {
    const ok = await dialog.confirm({
      title: '退出登录？',
      description: '未保存的草稿和填写中的字段会丢失。',
      confirmLabel: '退出',
      cancelLabel: '取消',
      variant: 'danger',
    });
    if (!ok) return;
    setToken(null);
    navigate('/login', { replace: true });
  }

  return (
    <nav className="flex flex-wrap items-center gap-2 border-b border-slate-200 bg-white px-4 py-2">
      <Link to="/kols" className="mr-2 font-semibold text-slate-800">
        KOL Ops
      </Link>
      <div className="flex items-center gap-1">
        {(() => {
          const activeTo = pickActive(loc.pathname);
          return ITEMS.map(([to, label]) => {
            const active = to === activeTo;
            return (
            <Link
              key={to}
              to={to}
              className={
                'rounded px-2 py-1 text-sm ' +
                (active
                  ? 'bg-slate-100 font-medium text-slate-900'
                  : 'text-slate-700 hover:bg-slate-100')
              }
            >
              {label}
            </Link>
          );
          });
        })()}
      </div>
      <div className="mx-2 h-5 w-px bg-slate-200" aria-hidden />
      <CampaignPicker />
      <EnvSwitch />
      <div className="ml-auto flex items-center gap-2">
        <Link
          to="/products"
          className="rounded border border-emerald-300 bg-emerald-50 px-2 py-1 text-xs font-medium text-emerald-800 hover:bg-emerald-100"
          title="从产品页发起新 campaign"
        >
          + 新建 campaign
        </Link>
        <div className="relative">
          <button
            type="button"
            onClick={() => setMenuOpen((v) => !v)}
            className="flex h-7 w-7 items-center justify-center rounded-full bg-slate-200 text-xs font-semibold text-slate-700 hover:bg-slate-300"
            aria-label="account menu"
          >
            👤
          </button>
          {menuOpen && (
            <div
              className="absolute right-0 z-20 mt-1 w-40 rounded border border-slate-200 bg-white py-1 text-sm shadow"
              onMouseLeave={() => setMenuOpen(false)}
            >
              <Link
                to="/settings"
                onClick={() => setMenuOpen(false)}
                className="block px-3 py-1 text-slate-700 hover:bg-slate-100"
              >
                设置
              </Link>
              <Link
                to="/replies"
                onClick={() => setMenuOpen(false)}
                className="block px-3 py-1 text-slate-700 hover:bg-slate-100"
              >
                回信监控
              </Link>
              <button
                type="button"
                onClick={() => {
                  setMenuOpen(false);
                  signOut();
                }}
                className="block w-full px-3 py-1 text-left text-rose-700 hover:bg-rose-50"
              >
                退出登录
              </button>
            </div>
          )}
        </div>
      </div>
    </nav>
  );
}
