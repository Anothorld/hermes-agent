import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';

/**
 * Cross-cutting approvals page.
 * Lists all pending approval.* facts surfaced by the bridge
 * (e.g. compensation_cap_breach, identity_drift_review) with
 * KOL + campaign + namespace path + context, and 批准 / 驳回 buttons.
 * A linked-escalation badge appears when the approval was opened by
 * an escalation rule.
 */
export type ApprovalRow = {
  identity_id: number;
  campaign_id: string;
  fact_path: string;
  namespace: 'identity' | 'offer' | 'fulfillment' | 'approval';
  context: Record<string, unknown> | null;
  opened_by: string | null;
  opened_at: string;
  linked_escalation_id?: number | null;
  handle?: string | null;
};

type StatusFilter = 'pending' | 'approved' | 'rejected' | 'all';

const NS_COLOR: Record<string, string> = {
  identity: 'bg-sky-50 text-sky-700 border-sky-200',
  offer: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  fulfillment: 'bg-amber-50 text-amber-800 border-amber-200',
  approval: 'bg-rose-50 text-rose-700 border-rose-200',
};

export function ApprovalsPage() {
  const [rows, setRows] = useState<ApprovalRow[]>([]);
  const [status, setStatus] = useState<StatusFilter>('pending');
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const qs = `?status=${status}`;
      setRows(await api.get<ApprovalRow[]>(`/approvals${qs}`));
      setErr(null);
    } catch (ex) {
      setErr(String(ex));
    }
  }, [status]);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 15_000);
    return () => clearInterval(t);
  }, [refresh]);

  const decide = useCallback(
    async (row: ApprovalRow, decision: 'approve' | 'reject') => {
      const note = decision === 'reject'
        ? window.prompt('Rejection reason (optional)') ?? ''
        : '';
      setBusy(row.fact_path);
      try {
        await api.post(`/approvals/${row.fact_path}/${decision}`, {
          identity_id: row.identity_id,
          campaign_id: row.campaign_id,
          decided_by: 'console-user',
          note: note || undefined,
        });
        await refresh();
      } catch (ex) {
        setErr(String(ex));
      } finally {
        setBusy(null);
      }
    },
    [refresh],
  );

  const grouped = useMemo(() => {
    const out: Record<string, ApprovalRow[]> = {};
    for (const r of rows) {
      const key = `${r.identity_id}::${r.campaign_id}`;
      (out[key] ||= []).push(r);
    }
    return out;
  }, [rows]);

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <h1 className="text-lg font-semibold">Approvals</h1>
        <select
          value={status}
          onChange={(e) => setStatus(e.target.value as StatusFilter)}
          className="rounded border border-slate-300 px-2 py-1 text-sm"
        >
          <option value="pending">pending</option>
          <option value="approved">approved</option>
          <option value="rejected">rejected</option>
          <option value="all">all</option>
        </select>
        <button
          onClick={refresh}
          className="rounded border border-slate-300 px-2 py-1 text-sm hover:bg-slate-50"
        >
          Refresh
        </button>
      </div>
      {err && <div className="text-sm text-red-600">{err}</div>}
      {Object.keys(grouped).length === 0 && (
        <div className="rounded border border-dashed border-slate-300 p-6 text-center text-sm text-slate-500">
          No {status} approvals.
        </div>
      )}
      {Object.entries(grouped).map(([key, items]) => {
        const [identityId, campaignId] = key.split('::');
        const handle = items[0]?.handle;
        return (
          <section key={key} className="rounded border border-slate-200 bg-white">
            <header className="flex items-center justify-between border-b border-slate-100 bg-slate-50 px-3 py-2 text-sm">
              <div>
                <Link
                  to={`/kols/${identityId}?campaign_id=${encodeURIComponent(campaignId)}`}
                  className="font-medium text-sky-700 hover:underline"
                >
                  {handle ? `@${handle}` : `identity #${identityId}`}
                </Link>
                <span className="ml-2 text-slate-500">campaign {campaignId}</span>
              </div>
              <span className="text-xs text-slate-500">{items.length} pending</span>
            </header>
            <ul className="divide-y divide-slate-100">
              {items.map((r) => (
                <li key={r.fact_path} className="flex flex-wrap items-start gap-3 p-3 text-sm">
                  <span
                    className={`rounded border px-2 py-0.5 text-xs font-mono ${
                      NS_COLOR[r.namespace] ?? 'bg-slate-50 text-slate-600 border-slate-200'
                    }`}
                  >
                    {r.fact_path}
                  </span>
                  {r.linked_escalation_id != null && (
                    <Link
                      to={`/escalations/${r.linked_escalation_id}`}
                      className="rounded bg-rose-100 px-2 py-0.5 text-xs text-rose-700 hover:bg-rose-200"
                    >
                      escalation #{r.linked_escalation_id}
                    </Link>
                  )}
                  <pre className="flex-1 whitespace-pre-wrap break-words text-xs text-slate-700">
                    {r.context ? JSON.stringify(r.context, null, 2) : '(no context)'}
                  </pre>
                  <div className="flex flex-shrink-0 gap-2">
                    <button
                      disabled={busy === r.fact_path || status !== 'pending'}
                      onClick={() => decide(r, 'approve')}
                      className="rounded bg-emerald-600 px-3 py-1 text-xs text-white hover:bg-emerald-700 disabled:opacity-40"
                    >
                      批准
                    </button>
                    <button
                      disabled={busy === r.fact_path || status !== 'pending'}
                      onClick={() => decide(r, 'reject')}
                      className="rounded bg-rose-600 px-3 py-1 text-xs text-white hover:bg-rose-700 disabled:opacity-40"
                    >
                      驳回
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          </section>
        );
      })}
    </div>
  );
}
