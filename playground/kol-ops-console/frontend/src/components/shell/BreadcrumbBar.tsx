import { Link, useLocation, useParams } from 'react-router-dom';
import { useCampaignStore } from '../../lib/store';

// Lightweight breadcrumb derived from the route. We avoid async name
// resolution (e.g. handle for KOL id) to keep the bar instant — IDs
// are good enough as the trailing segment.

type Crumb = { label: string; to?: string };

function buildCrumbs(pathname: string, params: Record<string, string | undefined>, campaignId: string): Crumb[] {
  const out: Crumb[] = [{ label: 'KOL Ops', to: '/kols' }];
  const seg = pathname.split('/').filter(Boolean);
  if (seg.length === 0) return out;
  switch (seg[0]) {
    case 'kols':
      out.push({ label: 'KOL 看板', to: '/kols' });
      if (params.id) {
        out.push({ label: `#${params.id}`, to: `/kols/${params.id}` });
      }
      if (seg[2] === 'relationship') {
        out.push({ label: '历史' });
      }
      break;
    case 'products':
      out.push({ label: '产品', to: '/products' });
      if (params.sku) out.push({ label: params.sku });
      break;
    case 'campaigns':
      out.push({ label: 'Campaign' });
      if (params.id || params.cid) out.push({ label: params.id ?? params.cid ?? '' });
      if (seg[2] === 'candidates') out.push({ label: '候选' });
      if (seg[2] === 'transcript') out.push({ label: 'Agent 对话' });
      break;
    case 'escalations':
      out.push({ label: '升级', to: '/escalations' });
      if (params.id) out.push({ label: `#${params.id}` });
      break;
    case 'approvals':
      out.push({ label: '待审批', to: '/approvals' });
      break;
    case 'policies':
      out.push({ label: '策略' });
      break;
    case 'replies':
      out.push({ label: '回信监控' });
      break;
    case 'settings':
      out.push({ label: '设置' });
      break;
  }
  // When inside a campaign-scoped view (kanban, approvals, etc.) and a
  // current campaign is set, surface it as an extra crumb so operators
  // know which campaign they're filtering on.
  if (campaignId && (seg[0] === 'kols' || seg[0] === 'approvals' || seg[0] === 'escalations')) {
    out.splice(1, 0, { label: campaignId, to: `/kols?campaign_id=${encodeURIComponent(campaignId)}` });
  }
  return out;
}

export function BreadcrumbBar() {
  const loc = useLocation();
  const params = useParams() as Record<string, string | undefined>;
  const campaignId = useCampaignStore((s) => s.currentCampaignId);
  const crumbs = buildCrumbs(loc.pathname, params, campaignId);
  if (crumbs.length <= 1) return null;
  return (
    <nav
      aria-label="breadcrumb"
      className="flex flex-wrap items-center gap-1 px-4 py-1 text-xs text-slate-500"
    >
      {crumbs.map((c, i) => {
        const isLast = i === crumbs.length - 1;
        return (
          <span key={`${i}-${c.label}`} className="flex items-center gap-1">
            {c.to && !isLast ? (
              <Link to={c.to} className="hover:text-slate-800 hover:underline">
                {c.label}
              </Link>
            ) : (
              <span className={isLast ? 'text-slate-700' : ''}>{c.label}</span>
            )}
            {!isLast && <span className="text-slate-300">/</span>}
          </span>
        );
      })}
    </nav>
  );
}
