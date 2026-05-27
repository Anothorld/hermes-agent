import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import ApprovalContextCard from '../components/ApprovalContextCard';
import { FactKeyChip } from '../components/inputs/FactKeyChip';
import { TimeAgo } from '../components/inputs/TimeAgo';
import { ErrorAlert } from '../components/feedback/ErrorAlert';
import { useEnvStore, toast } from '../lib/store';
import { useUnreadStore } from '../lib/unread';
import { errorSummary } from '../lib/errors';
import { dialog } from '../components/dialogs/useDialog';
import {
  parseConflictBody,
  startedAtMs,
  useInflightLock,
} from '../useInflightLock';
import { usePollingFallback } from '../hooks/usePollingFallback';
import { useDataChannel } from '../hooks/useDataChannel';

// Cross-cutting approvals page. Renders all pending approval.* facts
// surfaced by the bridge (e.g. compensation_cap_breach, reply_draft)
// with KOL + campaign + namespace path + context, plus 批准 / 驳回 / 优化
// actions. Reply-draft rows expose prior revisions through the
// collapsible 历史版本 block.
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

const STATUS_LABEL: Record<StatusFilter, string> = {
  pending: '待审批',
  approved: '已通过',
  rejected: '已驳回',
  all: '全部',
};

const rowKey = (r: ApprovalRow) =>
  `${r.identity_id}::${r.campaign_id}::${r.fact_path}`;

type PreviousDraft = {
  subject?: string | null;
  body?: string | null;
  to?: string | null;
  [k: string]: unknown;
};

// Match the FastAPI captured_at format (ISO-8601 UTC). Returns null when
// unparseable so callers can fall back instead of treating NaN as "fresh".
function capturedAtMs(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  return Number.isFinite(t) ? t : null;
}

type RefinementHistoryEntry = {
  prompt?: string;
  at?: string;
  by?: string;
};

export function ApprovalsPage() {
  const env = useEnvStore((s) => s.env);
  const [rows, setRows] = useState<ApprovalRow[]>([]);
  const [status, setStatus] = useState<StatusFilter>('pending');
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [refining, setRefining] = useState<string | null>(null);
  const [refinementText, setRefinementText] = useState('');
  const [refineHint, setRefineHint] = useState<Record<string, string>>({});
  const [historyOpen, setHistoryOpen] = useState<Record<string, boolean>>({});

  const markSeen = useUnreadStore((s) => s.markSeen);
  const refresh = useCallback(async () => {
    try {
      const qs = `?status=${status}&env=${env}`;
      const fetched = await api.get<ApprovalRow[]>(`/approvals${qs}`);
      setRows(fetched);
      setErr(null);
      // The operator is looking at the approvals list, so anything in
      // it now counts as "seen" — clear the global red dot. Use the
      // max opened_at so a brand-new item that lands between this
      // refresh and the next legitimately re-fires the dot.
      if (status === 'pending') {
        let latest: number = Date.now();
        for (const r of fetched) {
          if (!r.opened_at) continue;
          const t = new Date(r.opened_at).getTime();
          if (Number.isFinite(t) && t > latest) latest = t;
        }
        markSeen('approvals.global', latest);
      }
    } catch (ex) {
      setErr(ex);
    }
  }, [env, status, markSeen]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Live channel + slower polling fallback (gated on editor focus).
  useDataChannel({ onMatch: refresh });
  usePollingFallback(refresh, 20_000);

  const decide = useCallback(
    async (row: ApprovalRow, decision: 'approve' | 'reject') => {
      const isReplyDraft = row.fact_path === 'approval.reply_draft';
      let note = '';
      if (decision === 'reject') {
        const reason = await dialog.prompt({
          title: '驳回理由',
          description: '请简要说明驳回原因（AI 会基于此理由调整下一版草稿）。',
          placeholder: '例：语气太正式 / 漏掉了优惠条款 / 收件人称呼错误 ...',
          required: true,
          multiline: true,
          confirmLabel: '提交驳回',
          variant: 'danger',
          liveWarning: env === 'LIVE',
        });
        if (reason === null) return;
        note = reason;
      } else {
        // Approve confirms (especially in LIVE) since reply-draft
        // approve immediately creates a Gmail draft.
        const ok = await dialog.confirm({
          title: isReplyDraft ? '批准并创建 Gmail 草稿？' : '批准此审批？',
          description: isReplyDraft
            ? '批准后 AI 会在你 Gmail 草稿箱里创建一份草稿，需要你手动去 Gmail 点 Send。'
            : '批准此项后，AI 会沿着审批通过的路径继续推进。',
          confirmLabel: '批准',
          cancelLabel: '取消',
          variant: 'info',
          liveWarning: env === 'LIVE',
        });
        if (!ok) return;
      }
      setBusy(row.fact_path);
      try {
        await api.post(`/approvals/${row.fact_path}/${decision}`, {
          identity_id: row.identity_id,
          campaign_id: row.campaign_id,
          decided_by: 'console-user',
          env,
          note: note || undefined,
        });
        toast.success(decision === 'approve' ? '已批准' : '已驳回');
        await refresh();
      } catch (ex) {
        setErr(ex);
        toast.error('提交失败', errorSummary(ex));
      } finally {
        setBusy(null);
      }
    },
    [env, refresh],
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
            if_captured_at: row.opened_at,
          },
        );
        acquireLock(out?.run_id ?? null);
        setRefining(null);
        setRefinementText('');
        const hint = out?.hint ?? 'AI 正在重新生成 … 30–60s 后自动刷新。';
        setRefineHint((m) => ({ ...m, [key]: hint }));
        toast.progress('草稿生成中…', hint, { groupKey: `refine-${key}` });
        await refresh();
      } catch (ex) {
        const conflict = parseConflictBody(ex);
        if (conflict?.error === 'refine_already_in_flight') {
          acquireLock(
            conflict.run_id ?? null,
            startedAtMs(conflict.started_at),
          );
          const m = conflict.message ?? '已有一次优化在进行中。';
          setRefineHint((mm) => ({ ...mm, [key]: m }));
          toast.info('优化已在进行', m);
          setRefining(null);
          setRefinementText('');
        } else if (conflict?.error === 'stale_draft') {
          const m = conflict.message ?? '草稿已变化，请刷新后重试。';
          setRefineHint((mm) => ({ ...mm, [key]: m }));
          toast.error('草稿已过期', m);
          await refresh();
        } else {
          setErr(ex);
          toast.error('请求失败', errorSummary(ex));
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
        <h1 className="text-lg font-semibold">待审批</h1>
        <select
          value={status}
          onChange={(e) => setStatus(e.target.value as StatusFilter)}
          className="rounded border border-slate-300 px-2 py-1 text-sm"
        >
          {(Object.keys(STATUS_LABEL) as StatusFilter[]).map((s) => (
            <option key={s} value={s}>{STATUS_LABEL[s]}</option>
          ))}
        </select>
        <button
          onClick={refresh}
          className="rounded border border-slate-300 px-2 py-1 text-sm hover:bg-slate-50"
        >
          刷新
        </button>
      </div>
      {!!err && <ErrorAlert error={err} onRetry={refresh} />}
      {Object.keys(grouped).length === 0 && (
        <div className="rounded border border-dashed border-slate-300 p-6 text-center text-sm text-slate-500">
          没有 {STATUS_LABEL[status]} 的审批。
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
                  {handle ? `@${handle}` : `KOL #${identityId}`}
                </Link>
                <span className="ml-2 text-slate-500">campaign {campaignId}</span>
              </div>
              <span className="text-xs text-slate-500">{items.length} 项待处理</span>
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
  const refineLock = useInflightLock(
    `draft.lock.refine:${row.identity_id}:${row.campaign_id}`,
  );

  // Release the refine lock as soon as a newer draft revision lands —
  // otherwise the "优化生成中…" banner sticks for the full 5-min TTL
  // even though the new draft is already visible above.
  const rowCapturedAtMs = capturedAtMs(row.opened_at);
  const { release: releaseRefineLock } = refineLock;
  useEffect(() => {
    if (!refineLock.locked || refineLock.startedAtMs == null) return;
    if (rowCapturedAtMs == null) return;
    if (rowCapturedAtMs > refineLock.startedAtMs) releaseRefineLock();
  }, [
    refineLock.locked,
    refineLock.startedAtMs,
    rowCapturedAtMs,
    releaseRefineLock,
  ]);

  return (
    <li className="flex flex-wrap items-start gap-3 p-3 text-sm">
      <FactKeyChip factKey={row.fact_path} variant="filled" />
      <TimeAgo iso={row.opened_at} prefix="提交于" className="text-[11px] text-slate-500" />
      {row.linked_escalation_id != null && (
        <Link
          to={`/escalations/${row.linked_escalation_id}`}
          className="rounded bg-rose-100 px-2 py-0.5 text-xs text-rose-700 hover:bg-rose-200"
        >
          升级 #{row.linked_escalation_id}
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
            优化生成中… 约 {refineLock.remainingSeconds}s 后可再次操作；
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
                        {refEntry?.at && <> · <TimeAgo iso={refEntry.at} /></>}
                        {refEntry?.by ? ` · ${refEntry.by}` : ''}
                      </div>
                      {refEntry?.prompt && (
                        <div className="mt-1 text-[11px] italic text-slate-600">
                          优化指令：{refEntry.prompt}
                        </div>
                      )}
                      {d.subject != null && (
                        <div className="mt-1">
                          <span className="text-slate-500">主题：</span>{' '}
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
          <div data-editing className="rounded border border-sky-300 bg-sky-50 p-2">
            <textarea
              rows={4}
              value={refinementText}
              onChange={(e) => onChangeRefinementText(e.target.value)}
              placeholder="告诉 AI 改什么：语气、加什么、删什么、强调哪个事实，等等。"
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
              : '优化 / 重写'}
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
