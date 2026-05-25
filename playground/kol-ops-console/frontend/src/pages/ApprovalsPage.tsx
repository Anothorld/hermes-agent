import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import ApprovalContextCard from '../components/ApprovalContextCard';
import {
  parseConflictBody,
  startedAtMs,
  useInflightLock,
} from '../useInflightLock';

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
    async (
      row: ApprovalRow,
      acquireLock: (runId: string | null, startedAtMsArg?: number) => void,
    ) => {
      const prompt = refinementText.trim();
      if (!prompt) return;
      const key = rowKey(row);
      setBusy(row.fact_path);
      try {
        const out = await api.post<{ ok: boolean; hint?: string; run_id?: string }>(
          `/approvals/${row.fact_path}/refine`,
          {
            identity_id: row.identity_id,
            campaign_id: row.campaign_id,
            refinement_prompt: prompt,
            env,
            // Optimistic lock — refuse the refine if the row was
            // approved/rejected/refined since we opened it.
            if_captured_at: row.opened_at,
          },
        );
        acquireLock(out?.run_id ?? null);
        setRefining(null);
        setRefinementText('');
        setRefineHint((m) => ({
          ...m,
          [key]: out?.hint ?? 'agent is regenerating… refresh in 30–60s.',
        }));
        await refresh();
      } catch (ex) {
        const conflict = parseConflictBody(ex);
        if (conflict?.error === 'refine_already_in_flight') {
          acquireLock(
            conflict.run_id ?? null,
            startedAtMs(conflict.started_at),
          );
          setRefineHint((m) => ({
            ...m,
            [key]: conflict.message ?? 'A refine is already in progress.',
          }));
          setRefining(null);
          setRefinementText('');
        } else if (conflict?.error === 'stale_draft') {
          setRefineHint((m) => ({
            ...m,
            [key]: conflict.message ?? 'Draft changed — refresh and retry.',
          }));
          await refresh();
        } else {
          setErr(String(ex));
        }
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
              {items.map((r) => (
                <ApprovalRowItem
                  key={r.fact_path}
                  row={r}
                  env={env}
                  busy={busy}
                  status={status}
                  refining={refining}
                  refinementText={refinementText}
                  refineHintText={refineHint[rowKey(r)]}
                  isHistoryOpen={!!historyOpen[rowKey(r)]}
                  onToggleHistory={() =>
                    setHistoryOpen((m) => ({ ...m, [rowKey(r)]: !m[rowKey(r)] }))
                  }
                  onSetRefiningKey={(key) => setRefining(key)}
                  onChangeRefinementText={setRefinementText}
                  onSubmitRefine={submitRefine}
                  onDecide={decide}
                />
              ))}
            </ul>
          </section>
        );
      })}
    </div>
  );
}

type ApprovalRowItemProps = {
  row: ApprovalRow;
  env: 'TEST' | 'LIVE';
  busy: string | null;
  status: StatusFilter;
  refining: string | null;
  refinementText: string;
  refineHintText: string | undefined;
  isHistoryOpen: boolean;
  onToggleHistory: () => void;
  onSetRefiningKey: (key: string | null) => void;
  onChangeRefinementText: (text: string) => void;
  onSubmitRefine: (
    row: ApprovalRow,
    acquireLock: (runId: string | null, startedAtMsArg?: number) => void,
  ) => Promise<void>;
  onDecide: (row: ApprovalRow, decision: 'approve' | 'reject') => Promise<void>;
};

function ApprovalRowItem({
  row,
  env,
  busy,
  status,
  refining,
  refinementText,
  refineHintText,
  isHistoryOpen,
  onToggleHistory,
  onSetRefiningKey,
  onChangeRefinementText,
  onSubmitRefine,
  onDecide,
}: ApprovalRowItemProps) {
  const k = rowKey(row);
  const isReplyDraft = row.fact_path === 'approval.reply_draft';
  const ctx = (row.context ?? {}) as Record<string, unknown>;
  const previousDrafts = Array.isArray(ctx.previous_drafts)
    ? (ctx.previous_drafts as PreviousDraft[])
    : [];
  const refinementHistory = Array.isArray(ctx.refinement_history)
    ? (ctx.refinement_history as RefinementHistoryEntry[])
    : [];
  // Per-row in-flight lock for the refine button. Mirrors the backend's
  // refine dedup_key (refine:{identity_id}:{campaign_id}) so the disabled
  // state survives page refresh and cross-tab clicks land on the same
  // lock surface.
  const refineLock = useInflightLock(
    `draft.lock.refine:${row.identity_id}:${row.campaign_id}`,
  );
  return (
    <li className="flex flex-wrap items-start gap-3 p-3 text-sm">
      <span
        className={`rounded border px-2 py-0.5 text-xs font-mono ${
          NS_COLOR[row.namespace] ?? 'bg-slate-50 text-slate-600 border-slate-200'
        }`}
      >
        {row.fact_path}
      </span>
      {row.linked_escalation_id != null && (
        <Link
          to={`/escalations/${row.linked_escalation_id}`}
          className="rounded bg-rose-100 px-2 py-0.5 text-xs text-rose-700 hover:bg-rose-200"
        >
          escalation #{row.linked_escalation_id}
        </Link>
      )}
      <div className="flex w-full flex-col gap-2">
        <ApprovalContextCard
          factPath={row.fact_path}
          context={row.context}
          identityId={row.identity_id}
          campaignId={row.campaign_id}
          env={env}
        />
        {refineHintText && (
          <div className="rounded bg-amber-50 px-2 py-1 text-xs text-amber-800">
            {refineHintText}
          </div>
        )}
        {refineLock.locked && (
          <div className="rounded bg-sky-50 px-2 py-1 text-xs text-sky-800">
            Refine 进行中… 约 {refineLock.remainingSeconds}s 后可再次操作；
            刷新或换 tab 也不会重复触发。
          </div>
        )}
        {(previousDrafts.length > 0 || refinementHistory.length > 0) && (
          <div className="rounded border border-slate-200 bg-slate-50 px-2 py-1">
            <button
              type="button"
              onClick={onToggleHistory}
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
              onChange={(e) => onChangeRefinementText(e.target.value)}
              placeholder="Tell the agent what to change: tone, additions, removals, mention specific facts, etc."
              className="w-full rounded border border-sky-300 bg-white p-2 text-xs"
            />
            <div className="mt-1 flex gap-2">
              <button
                disabled={
                  busy === row.fact_path
                  || refineLock.locked
                  || !refinementText.trim()
                }
                onClick={() => onSubmitRefine(row, refineLock.acquire)}
                className="rounded bg-sky-600 px-3 py-1 text-xs text-white hover:bg-sky-700 disabled:opacity-40"
              >
                提交
              </button>
              <button
                disabled={busy === row.fact_path}
                onClick={() => {
                  onSetRefiningKey(null);
                  onChangeRefinementText('');
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
          disabled={busy === row.fact_path || status !== 'pending'}
          onClick={() => onDecide(row, 'approve')}
          className="rounded bg-emerald-600 px-3 py-1 text-xs text-white hover:bg-emerald-700 disabled:opacity-40"
        >
          {isReplyDraft ? '批准并创建 Gmail 草稿' : '批准'}
        </button>
        {isReplyDraft && status === 'pending' && (
          <button
            disabled={busy === row.fact_path || refineLock.locked}
            onClick={() => {
              onSetRefiningKey(refining === k ? null : k);
              onChangeRefinementText('');
            }}
            className="rounded bg-sky-600 px-3 py-1 text-xs text-white hover:bg-sky-700 disabled:opacity-40"
          >
            {refineLock.locked
              ? `生成中… (${refineLock.remainingSeconds}s)`
              : '优化/重新生成'}
          </button>
        )}
        <button
          disabled={busy === row.fact_path || status !== 'pending'}
          onClick={() => onDecide(row, 'reject')}
          className="rounded bg-rose-600 px-3 py-1 text-xs text-white hover:bg-rose-700 disabled:opacity-40"
        >
          驳回
        </button>
      </div>
    </li>
  );
}
