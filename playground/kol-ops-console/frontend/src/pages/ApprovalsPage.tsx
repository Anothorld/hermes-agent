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

const rowKey = (r: ApprovalRow) =>
  `${r.identity_id}::${r.campaign_id}::${r.fact_path}`;

type PreviousDraft = {
  subject?: string | null;
  body?: string | null;
  to?: string | null;
  [k: string]: unknown;
};

type RefinementHistoryEntry = {
  prompt?: string;
  at?: string;
  by?: string;
};

export function ApprovalsPage() {
  const [rows, setRows] = useState<ApprovalRow[]>([]);
  const [env, setEnv] = useState<'TEST' | 'LIVE'>(() => {
    const saved = localStorage.getItem('approvalsEnv') || localStorage.getItem('kolEnv');
    return saved === 'LIVE' ? 'LIVE' : 'TEST';
  });
  const [status, setStatus] = useState<StatusFilter>('pending');
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [refining, setRefining] = useState<string | null>(null);
  const [refinementText, setRefinementText] = useState('');
  const [refineHint, setRefineHint] = useState<Record<string, string>>({});
  const [historyOpen, setHistoryOpen] = useState<Record<string, boolean>>({});

  const refresh = useCallback(async () => {
    try {
      const qs = `?status=${status}&env=${env}`;
      setRows(await api.get<ApprovalRow[]>(`/approvals${qs}`));
      setErr(null);
    } catch (ex) {
      setErr(String(ex));
    }
  }, [env, status]);

  useEffect(() => {
    localStorage.setItem('approvalsEnv', env);
    refresh();
    const t = setInterval(refresh, 15_000);
    return () => clearInterval(t);
  }, [env, refresh]);

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
          env,
          note: note || undefined,
        });
        await refresh();
      } catch (ex) {
        setErr(String(ex));
      } finally {
        setBusy(null);
      }
    },
    [refresh, env],
  );

  const submitRefine = useCallback(
    async (row: ApprovalRow) => {
      const prompt = refinementText.trim();
      if (!prompt) return;
      const key = rowKey(row);
      setBusy(row.fact_path);
      try {
        const out = await api.post<{ ok: boolean; hint?: string }>(
          `/approvals/${row.fact_path}/refine`,
          {
            identity_id: row.identity_id,
            campaign_id: row.campaign_id,
            refinement_prompt: prompt,
            env,
          },
        );
        setRefining(null);
        setRefinementText('');
        setRefineHint((m) => ({
          ...m,
          [key]: out?.hint ?? 'agent is regenerating… refresh in 30–60s.',
        }));
        await refresh();
      } catch (ex) {
        setErr(String(ex));
      } finally {
        setBusy(null);
      }
    },
    [refresh, env, refinementText],
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
          value={env}
          onChange={(e) => setEnv(e.target.value as typeof env)}
          className="rounded border border-slate-300 px-2 py-1 text-sm"
        >
          <option value="TEST">TEST</option>
          <option value="LIVE">LIVE</option>
        </select>
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
              {items.map((r) => {
                const k = rowKey(r);
                const isReplyDraft = r.fact_path === 'approval.reply_draft';
                const ctx = (r.context ?? {}) as Record<string, unknown>;
                const previousDrafts = Array.isArray(ctx.previous_drafts)
                  ? (ctx.previous_drafts as PreviousDraft[])
                  : [];
                const refinementHistory = Array.isArray(ctx.refinement_history)
                  ? (ctx.refinement_history as RefinementHistoryEntry[])
                  : [];
                const hint = refineHint[k];
                const isHistoryOpen = !!historyOpen[k];
                return (
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
                    <div className="flex w-full flex-col gap-2">
                      <pre className="whitespace-pre-wrap break-words text-xs text-slate-700">
                        {r.context ? JSON.stringify(r.context, null, 2) : '(no context)'}
                      </pre>
                      {hint && (
                        <div className="rounded bg-amber-50 px-2 py-1 text-xs text-amber-800">
                          {hint}
                        </div>
                      )}
                      {(previousDrafts.length > 0 || refinementHistory.length > 0) && (
                        <div className="rounded border border-slate-200 bg-slate-50 px-2 py-1">
                          <button
                            type="button"
                            onClick={() =>
                              setHistoryOpen((m) => ({ ...m, [k]: !m[k] }))
                            }
                            className="text-xs text-slate-700 hover:underline"
                          >
                            {isHistoryOpen ? '▼' : '▶'} 历史版本 ({previousDrafts.length})
                          </button>
                          {isHistoryOpen && (
                            <ol className="mt-1 space-y-2 text-xs text-slate-700">
                              {previousDrafts.map((d, i) => {
                                const refEntry = refinementHistory[i];
                                return (
                                  <li
                                    key={i}
                                    className="rounded border border-slate-200 bg-white p-2"
                                  >
                                    <div className="font-mono text-[11px] text-slate-500">
                                      v-{previousDrafts.length - i}
                                      {refEntry?.at ? ` · ${refEntry.at}` : ''}
                                      {refEntry?.by ? ` · ${refEntry.by}` : ''}
                                    </div>
                                    {refEntry?.prompt && (
                                      <div className="mt-1 text-[11px] italic text-slate-600">
                                        prompt: {refEntry.prompt}
                                      </div>
                                    )}
                                    {d.subject != null && (
                                      <div className="mt-1">
                                        <span className="text-slate-500">subject:</span>{' '}
                                        {String(d.subject)}
                                      </div>
                                    )}
                                    {d.body != null && (
                                      <pre className="mt-1 whitespace-pre-wrap break-words">
                                        {String(d.body).length > 600
                                          ? `${String(d.body).slice(0, 600)}…`
                                          : String(d.body)}
                                      </pre>
                                    )}
                                  </li>
                                );
                              })}
                            </ol>
                          )}
                        </div>
                      )}
                      {refining === k && (
                        <div className="rounded border border-sky-300 bg-sky-50 p-2">
                          <textarea
                            rows={4}
                            value={refinementText}
                            onChange={(e) => setRefinementText(e.target.value)}
                            placeholder="Tell the agent what to change: tone, additions, removals, mention specific facts, etc."
                            className="w-full rounded border border-sky-300 bg-white p-2 text-xs"
                          />
                          <div className="mt-1 flex gap-2">
                            <button
                              disabled={busy === r.fact_path || !refinementText.trim()}
                              onClick={() => submitRefine(r)}
                              className="rounded bg-sky-600 px-3 py-1 text-xs text-white hover:bg-sky-700 disabled:opacity-40"
                            >
                              提交
                            </button>
                            <button
                              disabled={busy === r.fact_path}
                              onClick={() => {
                                setRefining(null);
                                setRefinementText('');
                              }}
                              className="rounded border border-slate-300 bg-white px-3 py-1 text-xs hover:bg-slate-50"
                            >
                              取消
                            </button>
                          </div>
                        </div>
                      )}
                    </div>
                    <div className="flex flex-shrink-0 gap-2">
                      <button
                        disabled={busy === r.fact_path || status !== 'pending'}
                        onClick={() => decide(r, 'approve')}
                        className="rounded bg-emerald-600 px-3 py-1 text-xs text-white hover:bg-emerald-700 disabled:opacity-40"
                      >
                        {isReplyDraft ? '批准并创建 Gmail 草稿' : '批准'}
                      </button>
                      {isReplyDraft && status === 'pending' && (
                        <button
                          disabled={busy === r.fact_path}
                          onClick={() => {
                            setRefining(refining === k ? null : k);
                            setRefinementText('');
                          }}
                          className="rounded bg-sky-600 px-3 py-1 text-xs text-white hover:bg-sky-700 disabled:opacity-40"
                        >
                          优化/重新生成
                        </button>
                      )}
                      <button
                        disabled={busy === r.fact_path || status !== 'pending'}
                        onClick={() => decide(r, 'reject')}
                        className="rounded bg-rose-600 px-3 py-1 text-xs text-white hover:bg-rose-700 disabled:opacity-40"
                      >
                        驳回
                      </button>
                    </div>
                  </li>
                );
              })}
            </ul>
          </section>
        );
      })}
    </div>
  );
}
