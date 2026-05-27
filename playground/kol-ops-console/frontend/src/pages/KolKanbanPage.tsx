import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api, LaneSnapshot } from '../api';
import { GOAL_COLUMNS } from '../components/GoalProgressBar';
import { RepeatKolBadge } from '../components/RepeatKolBadge';
import { LaneFilterBar, type LaneFilter } from '../components/LaneFilterBar';
import { TimeAgo } from '../components/inputs/TimeAgo';
import { KolSearchBox } from '../components/inputs/KolSearchBox';
import { FactInput } from '../components/inputs/FactInput';
import { ErrorAlert } from '../components/feedback/ErrorAlert';
import { factKeyLabel } from '../components/factKeyLabel';
import { KolArchiveDialog } from '../components/dialogs/KolArchiveDialog';
import { UnreadDot } from '../components/UnreadDot';
import { useCampaignStore, useEnvStore, toast } from '../lib/store';
import { useUnreadStore, isUnread } from '../lib/unread';
import { errorSummary } from '../lib/errors';
import { usePollingFallback } from '../hooks/usePollingFallback';
import { useDataChannel } from '../hooks/useDataChannel';

type LanesResponse = {
  campaign_id: string;
  lanes: LaneSnapshot[];
  counts?: {
    pending_approvals: number;
    open_escalations: number;
    pending_approvals_latest_at?: string | null;
    open_escalations_latest_at?: string | null;
  };
};

type EnrichedSnapshot = LaneSnapshot & {
  candidate_status?: string | null;
  archived?: boolean;
};

type CardStatusKey =
  | 'sent_waiting'
  | 'interested'
  | 'declined'
  | 'progressing'
  | 'blocked'
  | 'draft_pending_approval'
  | 'draft_pending_send'
  | 'idle';

function cardStatus(row: EnrichedSnapshot): CardStatusKey {
  const commerce = row.goals.commerce;
  if (commerce?.state === 'blocked') return 'blocked';
  const signal = (row.interest_signal || '').toLowerCase();
  if (signal === 'declined') return 'declined';
  if (signal === 'confirmed' || signal === 'interested') {
    if (commerce?.goal && commerce.goal !== 'interest_qualification') {
      return 'progressing';
    }
    return 'interested';
  }
  // Draft sub-states sit between idle and sent_waiting. reply_draft_state
  // is server-derived and covers both reply drafts (approval queue) and
  // cold-outreach drafts that route through the same queue. The fallback
  // outreach_draft_created branch catches cold-outreach drafts that the
  // skill writes directly to Gmail without an approval row.
  if (!row.outreach_sent_at) {
    if (row.reply_draft_state === 'pending') return 'draft_pending_approval';
    if (
      row.reply_draft_state === 'approved_unsent'
      || row.outreach_draft_created
    ) {
      return 'draft_pending_send';
    }
  }
  if (row.outreach_sent_at) return 'sent_waiting';
  return 'idle';
}

const STATUS_BADGE: Record<CardStatusKey, { label: string; cls: string; title: string }> = {
  sent_waiting: {
    label: '等回复',
    cls: 'bg-slate-100 text-slate-700 ring-1 ring-slate-200',
    title: '初邀已发出，尚未收到对方回信',
  },
  interested: {
    label: '已回复·意向',
    cls: 'bg-emerald-100 text-emerald-800 ring-1 ring-emerald-200',
    title: '对方回信，确认有意向',
  },
  progressing: {
    label: '推进中',
    cls: 'bg-sky-100 text-sky-800 ring-1 ring-sky-200',
    title: '已进入选品 / 报价 / 合同等后续阶段',
  },
  declined: {
    label: '已拒绝',
    cls: 'bg-rose-100 text-rose-800 ring-1 ring-rose-200',
    title: '对方明确拒绝',
  },
  blocked: {
    label: '阻塞',
    cls: 'bg-amber-100 text-amber-800 ring-1 ring-amber-200',
    title: '存在未解决的升级，需要操作员介入',
  },
  draft_pending_approval: {
    label: 'Draft 待审批',
    cls: 'bg-rose-50 text-rose-700 ring-1 ring-rose-200',
    title: '草稿在 Approvals 队列里等通过',
  },
  draft_pending_send: {
    label: 'Draft 待发送',
    cls: 'bg-sky-50 text-sky-700 ring-1 ring-sky-200',
    title: 'Gmail 草稿已就绪，需手动点 Send',
  },
  idle: {
    label: '待发起',
    cls: 'bg-slate-100 text-slate-500 ring-1 ring-slate-200',
    title: '尚未发出初邀',
  },
};

export function KolKanbanPage() {
  const [search, setSearch] = useSearchParams();
  const env = useEnvStore((s) => s.env);
  const campaignId = useCampaignStore((s) => s.currentCampaignId);
  const setCampaignId = useCampaignStore((s) => s.setCampaignId);

  // Deep-link sync: URL ?campaign_id= wins on first load (so a copied
  // link still works), then the store takes over.
  useEffect(() => {
    const fromUrl = search.get('campaign_id');
    if (fromUrl && fromUrl !== campaignId) setCampaignId(fromUrl);
    else if (!fromUrl && campaignId) {
      setSearch({ campaign_id: campaignId }, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const [data, setData] = useState<EnrichedSnapshot[]>([]);
  const [counts, setCounts] = useState<{
    pending_approvals: number;
    open_escalations: number;
    pending_approvals_latest_at?: string | null;
    open_escalations_latest_at?: string | null;
  }>({ pending_approvals: 0, open_escalations: 0 });
  const seenApprovalsGlobal = useUnreadStore((s) => s.seen['approvals.global']);
  const seenEscalationsGlobal = useUnreadStore(
    (s) => s.seen[`escalations.global.${campaignId}`],
  );
  const seenByScope = useUnreadStore((s) => s.seen);
  const [err, setErr] = useState<unknown>(null);
  const [laneFilter, setLaneFilter] = useState<LaneFilter>('all');
  const [repeatOnly, setRepeatOnly] = useState(false);
  const [showDone, setShowDone] = useState(false);
  const [query, setQuery] = useState('');
  const [lastRefreshedAt, setLastRefreshedAt] = useState<number>(0);
  const [openMissing, setOpenMissing] = useState<number | null>(null);

  const refresh = useCallback(async () => {
    if (!campaignId) {
      setData([]);
      setErr(null);
      return;
    }
    try {
      const r = await api.get<LanesResponse>(
        `/campaigns/${encodeURIComponent(campaignId)}/lanes?env=${env}`,
      );
      setData(r.lanes as EnrichedSnapshot[]);
      setCounts(
        r.counts ?? {
          pending_approvals: 0,
          open_escalations: 0,
          pending_approvals_latest_at: null,
          open_escalations_latest_at: null,
        },
      );
      setErr(null);
      setLastRefreshedAt(Date.now());
    } catch (ex) {
      setErr(ex);
    }
  }, [campaignId, env]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Live channel + slower polling fallback (gated on data-editing focus
  // so the popover form doesn't fight with refreshes).
  useDataChannel({ onMatch: refresh });
  usePollingFallback(refresh, 20_000);

  const visibleColumns = useMemo(
    () =>
      laneFilter === 'all'
        ? GOAL_COLUMNS
        : GOAL_COLUMNS.filter((c) => c.lane === laneFilter),
    [laneFilter],
  );

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return data.filter((row) => {
      if (repeatOnly && (row.repeat_count || 0) <= 0) return false;
      if (q) {
        const hay = `${row.handle || ''}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }, [data, repeatOnly, query]);

  const liveItems = filtered.filter((r) => !r.archived);
  const doneItems = filtered.filter((r) => r.archived);

  const grouped: Record<string, EnrichedSnapshot[]> = Object.fromEntries(
    visibleColumns.map((c) => [c.goal, [] as EnrichedSnapshot[]]),
  );
  for (const row of liveItems) {
    const goalNames = [
      row.goals.commerce?.goal,
      row.goals.fulfillment?.goal,
      row.goals.publish?.goal,
    ].filter(Boolean) as string[];
    const primary = goalNames[goalNames.length - 1] || 'outreach';
    if (grouped[primary]) grouped[primary].push(row);
    else if (visibleColumns.length > 0) {
      const firstGoal = visibleColumns[0].goal;
      grouped[firstGoal].push(row);
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="text-lg font-semibold">
          KOL 看板
          <span className="ml-2 text-xs text-slate-400">
            ({liveItems.length} 进行中 · {doneItems.length} 完成)
          </span>
        </h1>
        <KolSearchBox value={query} onChange={setQuery} />
        <Link
          to="/approvals"
          className="rounded bg-rose-100 px-2 py-0.5 text-xs font-medium text-rose-800 hover:bg-rose-200"
          title="全部 campaign 的待审批"
        >
          ◷ {counts.pending_approvals} 待审批
          <UnreadDot
            show={isUnread(counts.pending_approvals_latest_at, seenApprovalsGlobal)}
            title="有新的待审批条目"
          />
        </Link>
        <Link
          to="/escalations"
          className="rounded bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800 hover:bg-amber-200"
          title="本 campaign 的未解决升级"
        >
          ! {counts.open_escalations} 升级
          <UnreadDot
            show={isUnread(counts.open_escalations_latest_at, seenEscalationsGlobal)}
            title="有新的升级条目"
          />
        </Link>
        <div className="ml-auto flex items-center gap-2 text-xs">
          {lastRefreshedAt > 0 && (
            <TimeAgo
              iso={lastRefreshedAt}
              prefix="刷新于"
              className="text-[10px] text-slate-400"
            />
          )}
          <button
            onClick={() => setShowDone((v) => !v)}
            className="rounded border border-slate-300 bg-white px-2 py-0.5 text-slate-600 hover:bg-slate-50"
          >
            {showDone ? '隐藏已完成' : `已完成 (${doneItems.length})`}
          </button>
          <button
            onClick={() => refresh()}
            className="rounded border border-slate-300 bg-white px-2 py-0.5 text-slate-600 hover:bg-slate-50"
            title="手动刷新"
          >
            ↻
          </button>
        </div>
      </div>

      <LaneFilterBar
        lane={laneFilter}
        onLaneChange={setLaneFilter}
        repeatOnly={repeatOnly}
        onRepeatOnlyChange={setRepeatOnly}
      />

      <div className="flex flex-wrap items-center gap-3 rounded border border-slate-200 bg-white px-3 py-2 text-[11px] text-slate-500">
        <span className="font-medium text-slate-600">Handle 字色</span>
        <span className="inline-flex items-center gap-1.5">
          <span className="font-medium text-slate-800">@handle</span>
          <span>正常推进</span>
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="font-medium text-amber-700">@handle</span>
          <span>当前目标阻塞</span>
        </span>
      </div>

      {!!err && <ErrorAlert error={err} onRetry={refresh} />}

      {!campaignId && (
        <div className="rounded border border-slate-200 bg-slate-50 p-4 text-sm text-slate-600">
          请在导航栏选择一个 campaign 来加载看板。
          <Link to="/products" className="ml-2 font-medium text-emerald-700 hover:underline">
            + 新建 campaign →
          </Link>
        </div>
      )}

      <div className="flex gap-2">
        <div className="min-w-0 flex-1 overflow-x-auto pb-2">
          <div
            className="grid gap-2"
            style={{
              gridTemplateColumns: `repeat(${Math.max(visibleColumns.length, 1)}, minmax(15rem, 1fr))`,
            }}
          >
            {visibleColumns.map(({ goal, label, lane }) => (
              <div key={goal} className="rounded border border-slate-200 bg-white p-2">
                <div className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-500">
                  {label}{' '}
                  <span className="text-slate-400">
                    ({grouped[goal]?.length ?? 0} · {lane})
                  </span>
                </div>
                <ul className="space-y-1">
                  {(grouped[goal] ?? []).map((k) => (
                    <KanbanCard
                      key={k.identity_id}
                      row={k}
                      campaignId={campaignId}
                      env={env}
                      open={openMissing === k.identity_id}
                      onToggleMissing={() =>
                        setOpenMissing((cur) =>
                          cur === k.identity_id ? null : k.identity_id,
                        )
                      }
                      onRefreshed={refresh}
                      seenApproval={seenByScope[`approvals.kol.${k.identity_id}`]}
                      seenEscalation={seenByScope[`escalations.kol.${k.identity_id}`]}
                    />
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </div>

        {showDone && (
          <div className="w-64 flex-shrink-0 rounded border border-slate-200 bg-white p-2">
            <div className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-500">
              已完成 <span className="text-slate-400">({doneItems.length})</span>
            </div>
            <ul className="space-y-1">
              {doneItems.map((k) => (
                <li key={k.identity_id}>
                  <Link
                    to={`/kols/${k.identity_id}?campaign_id=${encodeURIComponent(campaignId)}`}
                    className="block break-words rounded bg-slate-100 px-2 py-1 text-sm text-slate-600 hover:bg-slate-200"
                    title={k.candidate_status || 'archived'}
                  >
                    @{k.handle}
                    <span className="ml-1 text-[10px] text-slate-500">
                      ({k.last_outcome || k.candidate_status || 'archived'})
                    </span>
                  </Link>
                </li>
              ))}
              {doneItems.length === 0 && (
                <li className="text-xs text-slate-400">没有归档的 KOL。</li>
              )}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}

function KanbanCard({
  row,
  campaignId,
  env,
  open,
  onToggleMissing,
  onRefreshed,
  seenApproval,
  seenEscalation,
}: {
  row: EnrichedSnapshot;
  campaignId: string;
  env: 'TEST' | 'LIVE';
  open: boolean;
  onToggleMissing: () => void;
  onRefreshed: () => void;
  seenApproval: number | undefined;
  seenEscalation: number | undefined;
}) {
  const lane = row.goals.commerce?.goal
    ? 'commerce'
    : row.goals.fulfillment?.goal
    ? 'fulfillment'
    : row.goals.publish?.goal
    ? 'publish'
    : 'meta';
  const goalState = row.goals[lane as keyof typeof row.goals];
  const blocked = !!goalState?.blocked_reason;
  const missing = goalState?.missing_facts ?? [];
  const status = cardStatus(row);
  const badge = STATUS_BADGE[status];
  const approvalUnread = isUnread(row.pending_approval_latest_at, seenApproval);
  const escalationUnread = isUnread(row.open_escalation_latest_at, seenEscalation);
  const [archiveOpen, setArchiveOpen] = useState(false);
  return (
    <li className="rounded border border-slate-100 bg-slate-50 p-2 text-sm">
      <div className="flex items-start justify-between gap-1">
        <Link
          to={`/kols/${row.identity_id}?campaign_id=${encodeURIComponent(campaignId)}&env=${env}`}
          title={`@${row.handle}`}
          className={
            'min-w-0 flex-1 break-words font-medium leading-snug hover:text-emerald-700 ' +
            (blocked ? 'text-amber-700' : 'text-slate-800')
          }
        >
          @{row.handle}
          <UnreadDot
            show={approvalUnread || escalationUnread}
            title={
              approvalUnread && escalationUnread
                ? '有新的待审批和升级'
                : approvalUnread
                ? '有新的待审批'
                : '有新的升级'
            }
          />
          <RepeatKolBadge
            count={row.repeat_count || 0}
            lastOutcome={row.last_outcome ?? null}
          />
        </Link>
        <span
          className={`flex-shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium ${badge.cls}`}
          title={badge.title}
        >
          {badge.label}
        </span>
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[10px] text-slate-500">
        {row.outreach_sent_at && (
          <TimeAgo iso={row.outreach_sent_at} prefix="初邀" />
        )}
        {missing.length > 0 && (
          <button
            type="button"
            onClick={onToggleMissing}
            className="rounded border border-emerald-300 bg-emerald-50 px-1.5 py-0.5 font-medium text-emerald-800 hover:bg-emerald-100"
            title={missing.map((f) => factKeyLabel(f).short).join('、')}
          >
            {open ? '收起' : `补 ${missing.length} 项`}
          </button>
        )}
        <Link
          to={`/escalations?campaign_id=${encodeURIComponent(campaignId)}&identity_id=${row.identity_id}&env=${env}`}
          className="rounded border border-amber-300 bg-amber-50 px-1.5 py-0.5 font-medium text-amber-800 hover:bg-amber-100"
        >
          升级
          <UnreadDot show={escalationUnread} title="有新的升级" />
        </Link>
        <button
          type="button"
          onClick={() => setArchiveOpen(true)}
          className="ml-auto rounded border border-slate-200 bg-white px-1.5 py-0.5 font-medium text-slate-500 hover:border-rose-300 hover:bg-rose-50 hover:text-rose-700"
          title="归档此 KOL（含『竞品-不合作』等原因）"
        >
          归档
        </button>
      </div>
      <KolArchiveDialog
        open={archiveOpen}
        identityId={row.identity_id}
        campaignId={campaignId}
        displayName={row.handle ? `@${row.handle}` : `kol#${row.identity_id}`}
        env={env}
        onClose={() => setArchiveOpen(false)}
        onArchived={onRefreshed}
      />
      {open && missing.length > 0 && (
        <MissingFactsForm
          identityId={row.identity_id}
          campaignId={campaignId}
          env={env}
          factKeys={missing}
          onSaved={() => {
            onToggleMissing();
            onRefreshed();
          }}
        />
      )}
    </li>
  );
}

function MissingFactsForm({
  identityId,
  campaignId,
  env,
  factKeys,
  onSaved,
}: {
  identityId: number;
  campaignId: string;
  env: 'TEST' | 'LIVE';
  factKeys: string[];
  onSaved: () => void;
}) {
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);

  async function submit() {
    setBusy(true);
    setErr(null);
    try {
      const namespaces: Record<string, Record<string, unknown>> = {};
      for (const k of factKeys) {
        const v = values[k];
        if (v === undefined || v === '' || v === null) continue;
        const ns = k.split('.', 1)[0];
        namespaces[ns] = namespaces[ns] || {};
        namespaces[ns][k] = v;
      }
      if (Object.keys(namespaces).length === 0) {
        setErr(new Error('请至少填一项'));
        setBusy(false);
        return;
      }
      await api.post(`/facts/${identityId}/multi`, {
        campaign_id: campaignId,
        env,
        source: 'console.kanban-popover',
        namespaces,
      });
      toast.success('字段已保存');
      onSaved();
    } catch (ex) {
      setErr(ex);
      toast.error('保存失败', errorSummary(ex));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      data-editing
      className="mt-2 space-y-1.5 rounded border border-emerald-200 bg-white p-2"
    >
      {factKeys.map((k) => (
        <FactInput
          key={k}
          factKey={k}
          value={values[k] ?? ''}
          onChange={(v) => setValues((m) => ({ ...m, [k]: v }))}
        />
      ))}
      {!!err && <ErrorAlert error={err} compact />}
      <div className="flex justify-end gap-1.5">
        <button
          type="button"
          onClick={onSaved}
          className="rounded border border-slate-300 bg-white px-2 py-0.5 text-[11px] text-slate-600 hover:bg-slate-50"
          disabled={busy}
        >
          取消
        </button>
        <button
          type="button"
          onClick={submit}
          disabled={busy}
          className="rounded bg-emerald-600 px-2 py-0.5 text-[11px] font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
        >
          {busy ? '保存中…' : '保存'}
        </button>
      </div>
    </div>
  );
}
