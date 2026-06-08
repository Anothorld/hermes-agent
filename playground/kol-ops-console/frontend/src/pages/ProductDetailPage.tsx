import { useEffect, useState, type FormEvent } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api, ApiError } from '../api';
import ContractReadinessPanel from '../components/ContractReadinessPanel';
import EditCampaignConfigPanel from '../components/EditCampaignConfigPanel';
import NoxCampaignOpsPanel from '../components/NoxCampaignOpsPanel';
import { TimeAgo } from '../components/inputs/TimeAgo';
import {
  PriorOutreachTouchBadge,
  type PriorOutreachTouch,
} from '../components/PriorOutreachTouchBadge';
import { KolSocialQuickLinks } from '../components/KolSocialQuickLinks';
import type { LinkPreviewPayload } from '../lib/kolProfileSnapshot';
import { resolvePriorOutreachTouch } from '../lib/priorOutreachTouch';
import { ErrorAlert } from '../components/feedback/ErrorAlert';
import { dialog } from '../components/dialogs/useDialog';
import { useEnvStore, toast } from '../lib/store';
import { errorSummary } from '../lib/errors';
import { runStateLabel, isRunStateActive } from '../lib/runStateLabels';
import { usePollingFallback } from '../hooks/usePollingFallback';

type ProductVariant = {
  id: string;
  label?: string | null;
  url?: string | null;
  attributes?: Record<string, string>;
  price?: number | null;
  discounted_price?: number | null;
  sale_price?: number | null;
  price_updated_at?: string | null;
};

type Product = {
  sku: string;
  name: string;
  url: string | null;
  tags: string[];
  notes: string | null;
  pitch_md: string | null;
  selling_points: string | null;
  variants: ProductVariant[];
  default_budget_per_kol: number | null;
  default_budget_total: number | null;
  default_absolute_floor: number | null;
};

type Me = {
  id: number;
  email: string;
  role: 'owner' | 'operator' | 'viewer' | string;
};

const variantFilterStorageKey = (sku: string) => `koc.variantFilter.${sku}`;

type CampaignRow = {
  campaign_id: string;
  env: string;
  run_id: string | null;
  status: 'running' | 'closed' | 'cancelled';
  started_at: string;
  started_by_user_id: number | null;
  stage: string | null;
  sub_status: string | null;
  last_event_type: string | null;
  last_event_ts: string | null;
  kol_identity_ids: number[];
  contacted_kol_ids: number[];
  candidate_count: number;
  pending_candidate_count: number;
  shortlist_ready: boolean;
  shortlist_approved: boolean;
  event_count: number;
  run_state: string | null;
  run_error: string | null;
  // Discovery-quantity-gate state. `gate_active=true` means the backend
  // is still tracking a live rediscover / auto-retry for this campaign;
  // the UI uses this to disable Approve and to gate the Rediscover
  // button (so a second rediscover can't fire while the gate cycle is
  // mid-flight). Both fields are null on legacy / pre-migration rows.
  gate_run_id: string | null;
  gate_state: string | null;
  gate_active: boolean;
  outreach_counts?: {
    discovered: number;
    pending_review: number;
    approved: number;
    draft_ready: number;
    sent: number;
    replied: number;
    needs_attention: number;
  };
  outreach_kols?: {
    needs_attention: { identity_id: number; handle?: string | null; status: string }[];
    replied: { identity_id: number; handle?: string | null; status: string }[];
    sent_waiting: { identity_id: number; handle?: string | null; status: string }[];
    draft_ready: { identity_id: number; handle?: string | null; status: string }[];
    approved_idle: { identity_id: number; handle?: string | null; status: string }[];
  };
  outreach_active_ids?: number[];
  last_activity?: {
    label: string;
    event_type: string | null;
    ts: string | null;
    identity_id: number | null;
  } | null;
};

type KolIdent = {
  id: number;
  display_name: string | null;
  primary_handle: string | null;
  platform: string | null;
};

type CampaignsPayload = {
  campaigns: CampaignRow[];
  kols: Record<string, KolIdent>;
};

type ReplyWatcherStatus = {
  running: boolean;
  pid: number | null;
  env: 'TEST' | 'LIVE' | null;
  interval: number | null;
  lookback_days: number | null;
  max_results: number | null;
  started_at: string | null;
  stopped_at: string | null;
  log_path: string | null;
  command: string[] | null;
  state_path: string;
};

type CloseCampaignResponse = {
  campaign_id: string;
  env: string;
  status: string;
  run_id: string | null;
  stop_result?: {
    requested?: boolean;
    run_id?: string;
    gateway_status?: string | null;
    error?: string;
  } | null;
};

// Mirror of backend gateway_client.RUNNING_STATES — used to gate the
// rediscover button so we don't fire a second agent while one is still
// working on the same campaign.
const RUN_STATE_RUNNING = new Set([
  'queued',
  'running',
  'waiting_for_approval',
  'stopping',
]);

function kolDetailPath(identityId: number, campaignId: string, env: string): string {
  return `/kols/${identityId}?campaign_id=${encodeURIComponent(campaignId)}&env=${encodeURIComponent(env)}`;
}

function StatusPill({ s }: { s: CampaignRow['status'] }) {
  const cls =
    s === 'running'
      ? 'bg-emerald-100 text-emerald-800'
      : s === 'closed'
      ? 'bg-slate-200 text-slate-700'
      : 'bg-amber-100 text-amber-800';
  return <span className={`rounded px-2 py-0.5 text-xs ${cls}`}>{s}</span>;
}

function RunStatePill({ s }: { s: string | null }) {
  if (!s) return null;
  const label = runStateLabel(s);
  const cls =
    isRunStateActive(s)
      ? s === 'waiting_for_approval'
        ? 'bg-amber-100 text-amber-900 ring-1 ring-amber-300'
        : 'bg-sky-100 text-sky-800'
      : s === 'completed'
      ? 'bg-emerald-100 text-emerald-800'
      : s === 'failed'
      ? 'bg-rose-100 text-rose-800'
      : s === 'cancelled'
      ? 'bg-amber-100 text-amber-800'
      : 'bg-slate-100 text-slate-700';
  const title =
    s === 'waiting_for_approval'
      ? 'Agent 被一条终端命令拦住，需要您在右侧「命令待批」浮层点允许/拒绝'
      : `Gateway run_state: ${s}`;
  return (
    <span className={`rounded px-2 py-0.5 text-xs ${cls}`} title={title}>
      agent: {label}
    </span>
  );
}

function ReplyWatcherPanel({
  status,
  env,
  interval,
  busy,
  onEnvChange,
  onIntervalChange,
  onStart,
  onStop,
  onRestart,
  onSyncSent,
  onRefresh,
}: {
  status: ReplyWatcherStatus | null;
  env: 'TEST' | 'LIVE';
  interval: number;
  busy: boolean;
  onEnvChange: (env: 'TEST' | 'LIVE') => void;
  onIntervalChange: (interval: number) => void;
  onStart: () => void;
  onStop: () => void;
  onRestart: () => void;
  onSyncSent: () => void;
  onRefresh: () => void;
}) {
  const running = status?.running ?? false;
  const pillCls = running ? 'bg-emerald-100 text-emerald-800' : 'bg-slate-100 text-slate-600';
  return (
    <div className="rounded border border-slate-200 bg-white p-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-sm font-medium">Reply watcher</h3>
            <span className={`rounded px-2 py-0.5 text-xs ${pillCls}`}>
              {running ? `running · ${status?.env}` : 'stopped'}
            </span>
            {status?.pid && <span className="text-xs text-slate-500">pid {status.pid}</span>}
          </div>
          <div className="text-xs text-slate-500">
            Gmail replies → CAL inbound event → reply router → draft approval or escalation.
          </div>
          {status?.log_path && (
            <div className="break-all text-xs text-slate-400">log: {status.log_path}</div>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <select
            value={env}
            onChange={(event) => onEnvChange(event.target.value as 'TEST' | 'LIVE')}
            className="rounded border px-2 py-1"
            disabled={busy}
          >
            <option value="TEST">TEST</option>
            <option value="LIVE">LIVE</option>
          </select>
          <label className="inline-flex items-center gap-1 text-slate-600">
            interval
            <input
              type="number"
              min={15}
              max={3600}
              value={interval}
              onChange={(event) => onIntervalChange(Number(event.target.value) || 60)}
              className="w-20 rounded border px-2 py-1"
              disabled={busy}
            />
            sec
          </label>
          <button
            onClick={onStart}
            disabled={busy || running}
            className="rounded border border-emerald-300 px-2 py-1 text-emerald-700 hover:bg-emerald-50 disabled:opacity-50"
          >
            Start
          </button>
          <button
            onClick={onRestart}
            disabled={busy}
            className="rounded border border-sky-300 px-2 py-1 text-sky-700 hover:bg-sky-50 disabled:opacity-50"
          >
            Restart / switch
          </button>
          <button
            onClick={onStop}
            disabled={busy || !running}
            className="rounded border border-rose-300 px-2 py-1 text-rose-700 hover:bg-rose-50 disabled:opacity-50"
          >
            Stop
          </button>
          <button
            onClick={onRefresh}
            disabled={busy}
            className="rounded border border-slate-300 px-2 py-1 text-slate-600 hover:bg-slate-50 disabled:opacity-50"
          >
            Refresh
          </button>
          <button
            onClick={onSyncSent}
            disabled={busy}
            className="rounded border border-indigo-300 px-2 py-1 text-indigo-700 hover:bg-indigo-50 disabled:opacity-50"
          >
            Sync sent
          </button>
        </div>
      </div>
    </div>
  );
}

type ShortlistCandidate = {
  handle: string;
  platform: string | null;
  profile_url?: string | null;
  preview_facts?: Record<string, unknown> | null;
  link_preview?: LinkPreviewPayload | null;
  social_links?: Array<{
    key?: string;
    label?: string;
    short_label?: string;
    url?: string;
  }> | null;
  link_previews?: Record<string, LinkPreviewPayload> | null;
  identity_id: number | null;
  display_name: string | null;
  reason: string | null;
  candidate_status: string | null;
  updated_at?: string | null;
  selected_at?: string | null;
  is_new_since_last_approval?: boolean;
  nox_diligence_verdict?: string | null;
  nox_cache_month?: string | null;
  nox_creator_id?: string | null;
  prior_outreach_touch?: PriorOutreachTouch | null;
};

type ShortlistPayload = {
  candidates: ShortlistCandidate[];
  snapshot_ts?: string | null;
  counts?: {
    pending: number;
    already_approved: number;
    rejected_or_archived_hidden: number;
  };
};

function ShortlistReviewPanel({
  campaignId,
  env,
  onSubmit,
  approveBlockedReason,
}: {
  campaignId: string;
  env: string;
  onSubmit: (selectedHandles: string[]) => Promise<void>;
  // When non-null, Approve is disabled and the reason is shown as a
  // banner + button tooltip. Used to lock approvals out while the
  // discovery quantity-gate is mid-cycle.
  approveBlockedReason?: string | null;
}) {
  const [candidates, setCandidates] = useState<ShortlistCandidate[] | null>(null);
  const [picked, setPicked] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState(false);
  const [noxBatchBusy, setNoxBatchBusy] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [snapshotTs, setSnapshotTs] = useState<string | null>(null);
  const [counts, setCounts] = useState<{ pending: number; already_approved: number; rejected_or_archived_hidden: number } | null>(null);
  const [showApproved, setShowApproved] = useState(false);
  const [removingHandle, setRemovingHandle] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const fetchShortlist = async (showSpinner: boolean, prefetchOg = false) => {
    if (showSpinner) setRefreshing(true);
    try {
      const qs = new URLSearchParams({ env });
      if (prefetchOg) qs.set('prefetch_og', '1');
      const r = await api.get<ShortlistPayload>(
        `/campaigns/${encodeURIComponent(campaignId)}/shortlist?${qs.toString()}`,
      );
      const nextCandidates = r.candidates ?? [];
      setCandidates(nextCandidates);
      setSnapshotTs(r.snapshot_ts ?? null);
      setCounts(r.counts ?? null);
      setPicked((prev) => {
        const next: Record<string, boolean> = {};
        for (const c of nextCandidates) {
          if (Object.prototype.hasOwnProperty.call(prev, c.handle)) {
            next[c.handle] = !!prev[c.handle];
          } else {
            next[c.handle] = c.candidate_status !== 'selected_for_outreach';
          }
        }
        return next;
      });
      setErr(null);
    } catch (ex) {
      setErr(errorSummary(ex));
    } finally {
      if (showSpinner) setRefreshing(false);
    }
  };

  useEffect(() => {
    void fetchShortlist(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [campaignId, env]);

  usePollingFallback(() => {
    void fetchShortlist(false);
  }, 20_000);

  if (err)
    return (
      <div className="rounded border border-rose-300 bg-rose-50 px-3 py-2 text-xs text-rose-800">
        Failed to load shortlist: {err}
      </div>
    );
  if (candidates === null)
    return <div className="text-xs text-slate-400">Loading candidates…</div>;
  if (candidates.length === 0)
    return <div className="text-xs text-slate-400">Agent published an empty shortlist.</div>;

  const pendingCandidates = candidates.filter((c) => c.candidate_status !== 'selected_for_outreach');
  const approvedCandidates = candidates.filter((c) => c.candidate_status === 'selected_for_outreach');
  const selectedHandles = Object.entries(picked).filter(([, v]) => v).map(([k]) => k);
  const selectedIdentityIds = pendingCandidates
    .filter((c) => picked[c.handle] && typeof c.identity_id === 'number')
    .map((c) => c.identity_id as number);
  const pendingCount = counts?.pending ?? pendingCandidates.length;
  const approvedCount = counts?.already_approved ?? approvedCandidates.length;

  const removeFromShortlist = async (c: ShortlistCandidate) => {
    if (typeof c.identity_id !== 'number') {
      setErr('该候选缺少 identity_id，无法从 shortlist 移除');
      return;
    }
    const ok = await dialog.confirm({
      title: `从 shortlist 移除 @${c.handle}？`,
      description:
        '候选人数据仍会保留在活动库中，只是不再显示在 shortlist，也无法被勾选批准。',
      confirmLabel: '移除',
      cancelLabel: '取消',
    });
    if (!ok) return;
    setRemovingHandle(c.handle);
    setErr(null);
    try {
      await api.post(
        `/campaigns/${encodeURIComponent(campaignId)}/candidates/status`,
        {
          identity_ids: [c.identity_id],
          candidate_status: 'rejected',
          review_reason: 'operator_removed_from_shortlist',
          env,
        },
      );
      toast.success('已从 shortlist 移除', `@${c.handle}`);
      await fetchShortlist(false);
    } catch (ex) {
      setErr(errorSummary(ex));
    } finally {
      setRemovingHandle(null);
    }
  };

  const runNoxDiligenceBatch = async () => {
    if (selectedIdentityIds.length === 0) {
      setErr('所选候选缺少 identity_id，无法批量 Nox 尽调');
      return;
    }
    setNoxBatchBusy(true);
    setErr(null);
    try {
      const r = await api.post<{ run_id?: string }>('/kols/nox-diligence-batch', {
        env,
        campaign_id: campaignId,
        identity_ids: selectedIdentityIds,
      });
      const n = r.processed_count ?? r.processed?.length ?? 0;
      const errN = r.error_count ?? r.errors?.length ?? 0;
      toast.success(
        'Nox 批量尽调完成',
        `成功 ${n} 人${errN ? ` · 失败 ${errN}` : ''}`,
      );
    } catch (ex) {
      setErr(errorSummary(ex));
    } finally {
      setNoxBatchBusy(false);
    }
  };

  return (
    <div className="rounded border border-emerald-200 bg-emerald-50/40 p-3">
      <div className="mb-2 flex items-center justify-between">
        <div className="text-xs font-medium text-emerald-900">
          Shortlist review · 待审批 {pendingCount} · 已批准 {approvedCount}
          {counts && counts.rejected_or_archived_hidden > 0 && (
            <span className="ml-1 font-normal text-slate-500">
              · 隐藏 {counts.rejected_or_archived_hidden}
            </span>
          )}
        </div>
        <div className="flex gap-2 text-xs">
          <button
            onClick={() => void fetchShortlist(true, true)}
            disabled={refreshing}
            title="重新拉取候选并预取主页摘要（较慢）"
            className="rounded border border-slate-300 bg-white px-2 py-0.5 text-slate-600 hover:bg-slate-50 disabled:opacity-50"
          >
            {refreshing ? 'Refreshing…' : 'Refresh'}
          </button>
          <button
            onClick={() => setPicked(Object.fromEntries(candidates.map((c) => [c.handle, true])))}
            className="rounded border border-slate-300 bg-white px-2 py-0.5 text-slate-600 hover:bg-slate-50"
          >
            Select all
          </button>
          <button
            onClick={() => setPicked(Object.fromEntries(candidates.map((c) => [c.handle, false])))}
            className="rounded border border-slate-300 bg-white px-2 py-0.5 text-slate-600 hover:bg-slate-50"
          >
            Clear
          </button>
        </div>
      </div>
      <div className="mb-2 text-[11px] text-slate-500">
        快照时间：
        {snapshotTs ? (
          <TimeAgo iso={snapshotTs} prefix="@" className="ml-1" />
        ) : (
          <span className="ml-1 italic">unknown</span>
        )}
      </div>
      {pendingCandidates.length === 0 && (
        <div className="mb-2 rounded border border-amber-200 bg-amber-50 px-2 py-1 text-[11px] text-amber-900">
          本轮暂无待审批候选，若刚执行 rediscover 请点击 Refresh。
        </div>
      )}
      <div className="mb-1 text-[11px] font-medium text-slate-500">待审批（本轮）</div>
      <ul className="space-y-2">
        {pendingCandidates.map((c) => (
          <li
            key={c.handle}
            className="rounded border border-emerald-100 bg-white p-2 text-xs"
          >
            <div className="mb-1 flex items-start justify-end">
              <button
                type="button"
                disabled={
                  removingHandle === c.handle
                  || typeof c.identity_id !== 'number'
                  || !!approveBlockedReason
                }
                title={
                  typeof c.identity_id !== 'number'
                    ? '缺少 identity_id，无法移除'
                    : '从 shortlist 移除（数据仍保留在活动库）'
                }
                onClick={() => void removeFromShortlist(c)}
                className="rounded border border-slate-300 bg-white px-2 py-0.5 text-[10px] text-slate-600 hover:bg-slate-50 disabled:opacity-50"
              >
                {removingHandle === c.handle ? '移除中…' : '从 shortlist 移除'}
              </button>
            </div>
            <label className="flex cursor-pointer items-start gap-2">
              <input
                type="checkbox"
                className="mt-0.5"
                checked={!!picked[c.handle]}
                onChange={(e) =>
                  setPicked((prev) => ({ ...prev, [c.handle]: e.target.checked }))
                }
              />
              <div className="flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">
                    {c.identity_id ? (
                      <Link
                        to={kolDetailPath(c.identity_id, campaignId, env)}
                        className="text-emerald-800 hover:underline"
                        onClick={(e) => e.stopPropagation()}
                      >
                        @{c.handle}
                      </Link>
                    ) : (
                      <span>@{c.handle}</span>
                    )}
                  </span>
                  {c.platform && (
                    <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500">
                      {c.platform}
                    </span>
                  )}
                  {c.candidate_status === 'selected_for_outreach' ? (
                    <span
                      className="rounded bg-emerald-100 px-1.5 py-0.5 text-[10px] text-emerald-800"
                      title="此 KOL 已在更早一轮被批准 — 重新勾选会再次触发 draft 生成，通常不需要"
                    >
                      already approved
                    </span>
                  ) : (
                    <span
                      className="rounded bg-amber-100 px-1.5 py-0.5 text-[10px] text-amber-800"
                      title="尚未审批的新候选"
                    >
                      pending
                    </span>
                  )}
                  {c.is_new_since_last_approval && (
                    <span className="rounded bg-sky-100 px-1.5 py-0.5 text-[10px] text-sky-800">
                      NEW
                    </span>
                  )}
                  <PriorOutreachTouchBadge
                    touch={resolvePriorOutreachTouch(c.prior_outreach_touch)}
                  />
                  {c.nox_diligence_verdict && (
                    <span
                      className="rounded bg-violet-100 px-1.5 py-0.5 text-[10px] text-violet-800"
                      title={
                        c.nox_cache_month
                          ? `Nox 数据月份 ${c.nox_cache_month}`
                          : 'Nox diligence'
                      }
                    >
                      Nox: {c.nox_diligence_verdict}
                      {c.nox_cache_month ? ` · ${c.nox_cache_month}` : ''}
                    </span>
                  )}
                  {c.display_name && c.display_name !== c.handle && (
                    <span className="text-slate-500">{c.display_name}</span>
                  )}
                </div>
                <div
                  className="mt-1.5"
                  onClick={(e) => e.stopPropagation()}
                  onKeyDown={(e) => e.stopPropagation()}
                >
                  <KolSocialQuickLinks
                    facts={c.preview_facts ?? {}}
                    identityId={c.identity_id}
                    env={env}
                    platform={c.platform}
                    handle={c.handle}
                    profileUrl={c.profile_url}
                    linkPreviewsByUrl={c.link_previews ?? undefined}
                    primaryLinkPreview={c.link_preview ?? undefined}
                    socialLinks={c.social_links ?? undefined}
                    stopPropagation
                  />
                </div>
                {resolvePriorOutreachTouch(c.prior_outreach_touch) && (
                  <div className="mt-1.5 flex flex-wrap items-center gap-1 text-[11px] text-slate-600">
                    <span className="font-medium text-slate-700">历史触达</span>
                    <PriorOutreachTouchBadge
                      touch={resolvePriorOutreachTouch(c.prior_outreach_touch)}
                    />
                  </div>
                )}
                {c.reason ? (
                  <div className="mt-1.5 text-[11px] text-slate-600">
                    <span className="font-medium text-slate-500">Reason: </span>
                    {c.reason}
                  </div>
                ) : (
                  <div className="mt-1.5 text-[11px] italic text-slate-400">
                    Agent did not provide a reason for this candidate.
                  </div>
                )}
                {c.updated_at && (
                  <div className="mt-1 text-[10px] text-slate-400">
                    updated <TimeAgo iso={c.updated_at} prefix="@" className="ml-1" />
                  </div>
                )}
              </div>
            </label>
          </li>
        ))}
      </ul>
      {approvedCandidates.length > 0 && (
        <div className="mt-2">
          <button
            onClick={() => setShowApproved((v) => !v)}
            className="rounded border border-slate-300 bg-white px-2 py-0.5 text-[11px] text-slate-600 hover:bg-slate-50"
          >
            {showApproved ? 'Hide approved' : `Show approved (${approvedCandidates.length})`}
          </button>
          {showApproved && (
            <ul className="mt-2 space-y-2">
              {approvedCandidates.map((c) => (
                <li key={c.handle} className="rounded border border-slate-200 bg-slate-50 p-2 text-xs">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium text-slate-700">@{c.handle}</span>
                    <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-[10px] text-emerald-800">
                      already approved
                    </span>
                    <PriorOutreachTouchBadge
                      touch={resolvePriorOutreachTouch(c.prior_outreach_touch)}
                    />
                    {c.selected_at && (
                      <span className="text-[10px] text-slate-400">
                        selected <TimeAgo iso={c.selected_at} prefix="@" className="ml-1" />
                      </span>
                    )}
                  </div>
                  <KolSocialQuickLinks
                    facts={c.preview_facts ?? {}}
                    identityId={c.identity_id}
                    env={env}
                    platform={c.platform}
                    handle={c.handle}
                    profileUrl={c.profile_url}
                    linkPreviewsByUrl={c.link_previews ?? undefined}
                    primaryLinkPreview={c.link_preview ?? undefined}
                    socialLinks={c.social_links ?? undefined}
                    className="mt-1.5"
                  />
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {approveBlockedReason && (
        <div className="mt-2 rounded border border-amber-300 bg-amber-50 px-2 py-1 text-[11px] text-amber-900">
          {approveBlockedReason}
        </div>
      )}
      <div className="mt-2 flex flex-wrap items-center justify-end gap-2">
        <span className="text-xs text-slate-500">{selectedHandles.length} selected</span>
        <button
          type="button"
          disabled={
            noxBatchBusy
            || selectedIdentityIds.length === 0
            || !!approveBlockedReason
          }
          title="Gate A：对所选待审批候选串行 diligence-pack（同月 cache 免扣费）"
          onClick={() => void runNoxDiligenceBatch()}
          className="rounded border border-indigo-400 bg-white px-3 py-1 text-xs font-medium text-indigo-800 hover:bg-indigo-50 disabled:opacity-50"
        >
          {noxBatchBusy ? 'Nox 尽调…' : `Nox 批量尽调 (${selectedIdentityIds.length})`}
        </button>
        <button
          disabled={
            busy
            || selectedHandles.length === 0
            || !!approveBlockedReason
          }
          title={approveBlockedReason ?? undefined}
          onClick={async () => {
            setBusy(true);
            setErr(null);
            try {
              await onSubmit(selectedHandles);
            } catch (ex) {
              setErr(errorSummary(ex));
            } finally {
              setBusy(false);
            }
          }}
          className="rounded bg-emerald-600 px-3 py-1 text-xs font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
        >
          {busy ? 'Approving…' : `Approve ${selectedHandles.length} KOL${selectedHandles.length === 1 ? '' : 's'}`}
        </button>
      </div>
    </div>
  );
}

function RediscoverControl({
  campaignId,
  alreadyDiscovered,
  blocked,
  onSubmit,
}: {
  campaignId: string;
  alreadyDiscovered: number;
  blocked: boolean;
  onSubmit: (additionalCount: number) => Promise<void>;
}) {
  const [n, setN] = useState<number>(5);
  const [busy, setBusy] = useState(false);
  const disabled = blocked || busy;
  const tooltip = blocked
    ? '当前 agent run 仍在进行 — 等待终态或先 Stop + close 再发现'
    : `再发现 ${n} 个新增 KOL（已发现 ${alreadyDiscovered} 个，不会被改动）`;
  return (
    <div className="inline-flex items-center gap-1" title={tooltip}>
      <input
        type="number"
        min={1}
        max={50}
        value={n}
        onChange={(e) => {
          const v = Number(e.target.value);
          if (Number.isFinite(v)) setN(Math.max(1, Math.min(50, Math.floor(v))));
        }}
        className="w-14 rounded border px-1 py-0.5 text-xs"
        disabled={disabled}
      />
      <button
        type="button"
        disabled={disabled}
        onClick={async () => {
          const ok = await dialog.confirm({
            title: `再发现 ${n} 个 KOL？`,
            description: `${campaignId} · 已发现的 ${alreadyDiscovered} 个候选会被排除，不会被修改。`,
            confirmLabel: '开始',
            cancelLabel: '取消',
          });
          if (!ok) return;
          setBusy(true);
          try {
            await onSubmit(n);
          } finally {
            setBusy(false);
          }
        }}
        className="rounded border border-indigo-300 bg-indigo-50 px-2 py-0.5 text-xs text-indigo-800 hover:bg-indigo-100 disabled:opacity-50"
      >
        {busy ? 'Starting…' : `+ Discover ${n} more`}
      </button>
    </div>
  );
}

function CampaignCard({
  c,
  kols,
  onClose,
  onApprove,
  onRediscover,
}: {
  c: CampaignRow;
  kols: Record<string, KolIdent>;
  onClose: (id: string, env: string) => void;
  onApprove: (id: string, env: string, selectedHandles: string[]) => Promise<void>;
  onRediscover: (id: string, env: string, additionalCount: number) => Promise<void>;
}) {
  const [showReview, setShowReview] = useState(false);
  const [retryingDrafts, setRetryingDrafts] = useState(false);
  const approvedHandles = c.kol_identity_ids
    .map((id) => kols[String(id)]?.primary_handle)
    .filter((handle): handle is string => Boolean(handle));
  const counts = c.outreach_counts ?? {
    discovered: c.candidate_count,
    pending_review: c.pending_candidate_count,
    approved: Math.max(0, c.candidate_count - c.pending_candidate_count),
    draft_ready: c.contacted_kol_ids.length,
    sent: 0,
    replied: 0,
    needs_attention: 0,
  };
  const outreachKols = c.outreach_kols ?? {
    needs_attention: [],
    replied: [],
    sent_waiting: [],
    draft_ready: [],
    approved_idle: [],
  };
  const noOutreachProgress = (counts.draft_ready + counts.sent + counts.replied) === 0;
  const contractReadyIds = Array.from(
    new Set(
      (outreachKols.replied ?? [])
        .map((row) => row.identity_id)
        .filter((iid): iid is number => Number.isInteger(iid)),
    ),
  );
  const renderKolChips = (
    rows: { identity_id: number; handle?: string | null; status: string }[],
    emptyText: string,
  ) => {
    if (rows.length === 0) return <div className="text-xs text-slate-400">{emptyText}</div>;
    return (
      <ul className="flex flex-wrap gap-1 text-xs">
        {rows.map((row) => {
          const k = kols[String(row.identity_id)];
          const label = k?.display_name || k?.primary_handle || row.handle || `#${row.identity_id}`;
          return (
            <li key={`${row.status}:${row.identity_id}`} className="inline-flex items-center gap-1 rounded bg-slate-100 px-2 py-0.5 text-slate-700">
              <Link to={kolDetailPath(row.identity_id, c.campaign_id, c.env)} className="hover:text-emerald-700">
                {String(label).startsWith('@') ? label : `@${label}`}
              </Link>
            </li>
          );
        })}
      </ul>
    );
  };
  return (
    <li className="space-y-2 rounded border bg-white p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-sm font-medium">{c.campaign_id}</span>
        <span className="rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-600">{c.env}</span>
        <StatusPill s={c.status} />
        <RunStatePill s={c.run_state} />
        {c.run_id && (
          <span className="font-mono text-xs text-slate-500">run: {c.run_id}</span>
        )}
        <TimeAgo iso={c.started_at} prefix="启动于" className="ml-auto text-xs text-slate-400" />
        {c.status === 'running' && (
          <button
            onClick={() => onClose(c.campaign_id, c.env)}
            className="rounded border border-slate-300 px-2 py-0.5 text-xs text-slate-600 hover:bg-slate-50"
            title="Best-effort stop the gateway run, then close this campaign in the console"
          >
            Stop + close
          </button>
        )}
        {(c.shortlist_ready || c.candidate_count > 0) && (
          <button
            onClick={() => setShowReview((v) => !v)}
            className="rounded border border-emerald-300 bg-emerald-50 px-2 py-0.5 text-xs text-emerald-800 hover:bg-emerald-100"
            title="Review discovered candidates with scores + reasons, then approve a subset"
          >
            {showReview
              ? '× Close review'
              : c.pending_candidate_count > 0
              ? c.shortlist_approved
                ? `✓ Review new candidates (${c.pending_candidate_count})`
                : `✓ Review candidates (${c.candidate_count})`
              : `✓ View shortlist (${c.candidate_count})`}
          </button>
        )}
        {c.shortlist_ready && c.shortlist_approved && (
          <span
            className="rounded bg-emerald-100 px-2 py-0.5 text-xs text-emerald-800"
            title="Operator already approved this shortlist"
          >
            shortlist approved
          </span>
        )}
        <RediscoverControl
          campaignId={c.campaign_id}
          alreadyDiscovered={c.candidate_count}
          blocked={
            c.gate_active
            || (c.status === 'running'
                && !!c.run_state
                && RUN_STATE_RUNNING.has(c.run_state))
          }
          onSubmit={(n) => onRediscover(c.campaign_id, c.env, n)}
        />
        {c.shortlist_approved && noOutreachProgress && approvedHandles.length > 0 && (
          <button
            onClick={async () => {
              setRetryingDrafts(true);
              try {
                await onApprove(c.campaign_id, c.env, approvedHandles);
              } finally {
                setRetryingDrafts(false);
              }
            }}
            disabled={retryingDrafts}
            className="rounded border border-sky-300 bg-sky-50 px-2 py-0.5 text-xs text-sky-800 hover:bg-sky-100 disabled:opacity-50"
            title="Rerun post-approval outreach draft generation for the approved candidates"
          >
            {retryingDrafts ? 'Retrying…' : 'Retry draft run'}
          </button>
        )}
      </div>
      {showReview && (
        <ShortlistReviewPanel
          campaignId={c.campaign_id}
          env={c.env}
          approveBlockedReason={
            c.gate_active
              ? '当前正在执行 rediscover / 自动补量，请等待发现流程完成后再审批 KOL（避免触发 floor 误判）'
              : c.status === 'running'
              && !!c.run_state
              && RUN_STATE_RUNNING.has(c.run_state)
              ? '当前 agent run 仍在进行 — 等待终态或先 Stop + close 再审批'
              : null
          }
          onSubmit={async (handles) => {
            await onApprove(c.campaign_id, c.env, handles);
            setShowReview(false);
          }}
        />
      )}
      <div className="rounded border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-700">
        <div>
          发现 {counts.discovered} 名 · 待审批 {counts.pending_review} · 已批准 {counts.approved}
          {' '}| 草稿 {counts.draft_ready} · 已发送 {counts.sent} · 已回复 {counts.replied}
          {counts.needs_attention > 0 && <> · 需处理 {counts.needs_attention}</>}
        </div>
        <div className="mt-1 text-slate-500">
          最近动态：
          {c.last_activity?.label ? (
            <>
              <span className="ml-1 text-slate-700">{c.last_activity.label}</span>
              {c.last_activity.ts && (
                <TimeAgo iso={c.last_activity.ts} prefix="@" className="ml-1 text-slate-400" />
              )}
            </>
          ) : (
            <span className="ml-1 italic">暂无事件，等待下一次 bridge 更新。</span>
          )}
        </div>
      </div>
      {c.event_count === 0 && c.candidate_count === 0 && c.run_state && c.run_state !== 'running' && c.run_state !== 'queued' && c.run_state !== 'waiting_for_approval' && (
        <div className="rounded border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900">
          <div className="font-medium">Agent finished without emitting any bridge events.</div>
          <div className="mt-0.5">
            run_state = <span className="font-mono">{c.run_state}</span>
            {c.run_error && <> · error: <span className="font-mono">{c.run_error}</span></>}
            . The orchestrator skill likely was not invoked. Inspect
            {' '}<span className="font-mono">~/.hermes/profiles/kol-orchestrator/logs/agent.log</span>
            {' '}for run_id <span className="font-mono">{c.run_id}</span>.
          </div>
        </div>
      )}
      <div className="space-y-2">
        <div>
          <div className="mb-1 text-xs font-medium text-slate-500">需优先处理 ({counts.needs_attention})</div>
          {renderKolChips(outreachKols.needs_attention, '当前无待处理 KOL')}
        </div>
        <div>
          <div className="mb-1 text-xs font-medium text-slate-500">已回复 / 推进中 ({counts.replied})</div>
          {renderKolChips(outreachKols.replied, '暂无已回复 KOL')}
        </div>
        <div>
          <div className="mb-1 text-xs font-medium text-slate-500">已发送，等待回复 ({counts.sent})</div>
          {renderKolChips(outreachKols.sent_waiting, '暂无已发送 KOL')}
        </div>
        <div className="text-xs text-slate-500">
          候选池：已发现 {counts.discovered} 名，待审批 {counts.pending_review} 名。
          {counts.pending_review > 0 ? ' 请在 shortlist review 中确认本轮候选。' : ' 当前没有新的候选待审批。'}
        </div>
      </div>
      <EditCampaignConfigPanel campaignId={c.campaign_id} env={c.env} />
      <NoxCampaignOpsPanel campaignId={c.campaign_id} env={c.env} />
      {contractReadyIds.length > 0 && (
        <div className="space-y-1">
          <div className="text-xs font-medium text-slate-500">
            Contract readiness (pre-flight before合同生成)
          </div>
          <ul className="space-y-2">
            {contractReadyIds.map((iid) => {
              const k = kols[String(iid)];
              const label = k?.primary_handle ? `@${k.primary_handle}` : k?.display_name || `#${iid}`;
              return (
                <li key={iid} className="space-y-1">
                  <div className="text-[11px] text-slate-500">{label}</div>
                  <ContractReadinessPanel
                    campaignId={c.campaign_id}
                    identityId={iid}
                    env={c.env}
                  />
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </li>
  );
}

function LaunchCampaignForm({
  sku,
  env,
  product,
  canLaunch,
  onLaunched,
  onError,
}: {
  sku: string;
  env: 'TEST' | 'LIVE';
  product: Product;
  canLaunch: boolean;
  onLaunched: (runId: string | null, campaignId: string) => void;
  onError: (msg: string) => void;
}) {
  // Sensible defaults so the operator can launch with one click; all
  // fields are still editable for tuning per-campaign.
  const today = new Date().toISOString().slice(0, 10).replace(/-/g, '');
  const defaultCampaignId = `${sku}-${today}`;
  const [campaignId, setCampaignId] = useState(defaultCampaignId);
  // SKU-shaped catalog names (e.g. "SEB800") get rejected by the backend
  // validator — leave the input blank in that case so the operator is
  // forced to type a human-friendly name.
  const _skuShape = /^[A-Z]{2,5}[\- ]?\d{3,5}[A-Z0-9]*$/;
  const _defaultDisplayName =
    product.name && !_skuShape.test(product.name.trim()) ? product.name : '';
  const [productDisplayName, setProductDisplayName] = useState<string>(_defaultDisplayName);
  const [budgetPerKol, setBudgetPerKol] = useState<number>(product.default_budget_per_kol ?? 500);
  const [absoluteFloor, setAbsoluteFloor] = useState<number>(product.default_absolute_floor ?? 1000);
  const [budgetTotal, setBudgetTotal] = useState<number>(product.default_budget_total ?? 12000);
  const [headcountTarget, setHeadcountTarget] = useState<number>(10);
  const [discoveryTargetOverride, setDiscoveryTargetOverride] = useState<number | ''>('');
  const [testModeTo, setTestModeTo] = useState<string>('');
  const [productPitchMd, setProductPitchMd] = useState<string>(product.pitch_md ?? '');
  const [briefExtra, setBriefExtra] = useState<string>('');
  // All known variants are eligible by default — operator can untick to narrow.
  const [variantPicked, setVariantPicked] = useState<Record<string, boolean>>(() =>
    Object.fromEntries(product.variants.map((v) => [v.id, true])),
  );
  // Contract-readiness fields the operator declares upfront so the
  // post-launch readiness gate has something to check.
  const [deliverablePlatforms, setDeliverablePlatforms] = useState<Record<string, boolean>>({
    instagram: true,
    tiktok: true,
    youtube: false,
    twitter: false,
    blog: false,
  });
  const [deliverableCount, setDeliverableCount] = useState<number>(1);
  const [auditStandardsMd, setAuditStandardsMd] = useState<string>('');
  const [compensationMode, setCompensationMode] = useState<'gifted' | 'paid' | 'commission' | 'hybrid'>('paid');
  const [commissionMinPct, setCommissionMinPct] = useState<number | ''>('');
  const [commissionMaxPct, setCommissionMaxPct] = useState<number | ''>('');
  const [busy, setBusy] = useState(false);
  const [activeStep, setActiveStep] = useState<'A' | 'B' | 'C'>('A');
  const [variantFilterKey, setVariantFilterKey] = useState<string>('');
  const [variantFilterValue, setVariantFilterValue] = useState<string>('');

  // Live preview of the discovery target so the operator can tune the funnel.
  const discoveryDefault = Math.max(headcountTarget * 3, headcountTarget + 5);
  const discoveryEffective = discoveryTargetOverride === '' ? discoveryDefault : discoveryTargetOverride;

  // Sync default campaign_id when sku changes.
  useEffect(() => {
    setCampaignId(`${sku}-${today}`);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sku]);

  // Re-sync defaults when the parent reloads the product (e.g. variants edited
  // in the catalog after this form mounted).
  useEffect(() => {
    setVariantPicked((prev) => {
      const next: Record<string, boolean> = {};
      for (const v of product.variants) {
        next[v.id] = prev[v.id] ?? true;
      }
      return next;
    });
  }, [product.variants]);

  const pickedVariantIds = product.variants
    .filter((v) => variantPicked[v.id])
    .map((v) => v.id);
  const variantAttrKeys = Array.from(
    new Set(
      product.variants.flatMap((v) => Object.keys(v.attributes || {})),
    ),
  ).sort();
  const variantAttrValues = variantFilterKey
    ? Array.from(
        new Set(
          product.variants
            .map((v) => (v.attributes || {})[variantFilterKey])
            .filter((v): v is string => typeof v === 'string' && v.trim().length > 0),
        ),
      ).sort()
    : [];
  const filteredVariants = product.variants.filter((v) => {
    if (!variantFilterKey) return true;
    const val = (v.attributes || {})[variantFilterKey];
    if (!val) return false;
    if (!variantFilterValue) return true;
    return val === variantFilterValue;
  });

  useEffect(() => {
    try {
      const raw = localStorage.getItem(variantFilterStorageKey(sku));
      if (!raw) return;
      const parsed = JSON.parse(raw) as { key?: string; value?: string };
      if (parsed?.key && variantAttrKeys.includes(parsed.key)) {
        const key = parsed.key;
        setVariantFilterKey(key);
        if (
          parsed.value
          && product.variants.some((v) => (v.attributes || {})[key] === parsed.value)
        ) {
          setVariantFilterValue(parsed.value);
        }
      }
    } catch {
      // ignore malformed localStorage payloads
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sku]);

  useEffect(() => {
    if (!variantFilterKey) return;
    if (!variantAttrKeys.includes(variantFilterKey)) {
      setVariantFilterKey('');
      setVariantFilterValue('');
      return;
    }
    if (variantFilterValue && !variantAttrValues.includes(variantFilterValue)) {
      setVariantFilterValue('');
    }
  }, [variantFilterKey, variantFilterValue, variantAttrKeys, variantAttrValues]);

  useEffect(() => {
    if (!variantFilterKey && !variantFilterValue) {
      localStorage.removeItem(variantFilterStorageKey(sku));
      return;
    }
    localStorage.setItem(
      variantFilterStorageKey(sku),
      JSON.stringify({ key: variantFilterKey, value: variantFilterValue }),
    );
  }, [sku, variantFilterKey, variantFilterValue]);
  const isHttpUrl = (raw: string | null): boolean => {
    if (!raw) return false;
    try {
      const u = new URL(raw);
      return u.protocol === 'http:' || u.protocol === 'https:';
    } catch {
      return false;
    }
  };
  const productUrlReady = isHttpUrl(product.url);
  const platforms = Object.entries(deliverablePlatforms)
    .filter(([, v]) => v)
    .map(([k]) => k);
  const trimmedDisplayName = productDisplayName.trim();
  const preflightBlockers: string[] = [];
  const preflightWarnings: string[] = [];
  if (!canLaunch) preflightBlockers.push('viewer 角色只读，不能启动 campaign');
  if (!productUrlReady) preflightBlockers.push('product_url 必填且必须为有效 http(s) 链接');
  if (env === 'TEST' && !testModeTo.trim()) preflightBlockers.push('TEST 模式必须填写 test_mode_to');
  if (!productPitchMd.trim()) preflightBlockers.push('product_pitch_md 必填');
  if (!trimmedDisplayName) preflightBlockers.push('product_display_name 必填');
  if (trimmedDisplayName && _skuShape.test(trimmedDisplayName)) {
    preflightBlockers.push('product_display_name 不能是 SKU/型号代码');
  }
  if (
    trimmedDisplayName
    && (trimmedDisplayName.toLowerCase() === sku.toLowerCase()
      || trimmedDisplayName.toLowerCase() === campaignId.toLowerCase())
  ) {
    preflightBlockers.push('product_display_name 不能与 SKU 或 campaign_id 相同');
  }
  if (product.variants.length > 0 && pickedVariantIds.length === 0) {
    preflightBlockers.push('白名单至少勾选一个 variant');
  }
  if (platforms.length === 0) preflightBlockers.push('deliverable_platforms 至少选择一个');
  if (!Number.isFinite(deliverableCount) || deliverableCount < 1) {
    preflightBlockers.push('deliverable_count_per_platform 至少为 1');
  }
  const needsCommission = compensationMode === 'commission' || compensationMode === 'hybrid';
  if (needsCommission) {
    if (commissionMinPct === '' || commissionMaxPct === '') {
      preflightBlockers.push('commission 模式下必须填写 commission min/max %');
    } else if (commissionMinPct > commissionMaxPct) {
      preflightBlockers.push('commission_min_pct 不能大于 commission_max_pct');
    }
  }
  if (env === 'LIVE') preflightWarnings.push('LIVE 环境会进入真实运营链路');
  if (!auditStandardsMd.trim()) preflightWarnings.push('audit_standards_md 为空，后续审核约束可能不足');

  const parseApiBody = (raw: string): unknown => {
    try {
      return JSON.parse(raw);
    } catch {
      return null;
    }
  };
  const normalize422Details = (detail: unknown): string | null => {
    if (!Array.isArray(detail)) return null;
    const mapped = detail
      .map((item) => {
        if (!item || typeof item !== 'object') return '';
        const it = item as { loc?: unknown; msg?: unknown };
        const loc = Array.isArray(it.loc) ? it.loc.map(String).join('.') : '';
        const msg = typeof it.msg === 'string' ? it.msg : '';
        if (!msg) return '';
        if (loc.includes('test_mode_to')) return `test_mode_to: ${msg}`;
        if (loc.includes('product_display_name')) return `product_display_name: ${msg}`;
        if (loc.includes('product_variant_ids')) return `product_variant_ids: ${msg}`;
        if (loc.includes('deliverable_platforms')) return `deliverable_platforms: ${msg}`;
        if (loc.includes('deliverable_count_per_platform')) return `deliverable_count_per_platform: ${msg}`;
        return loc ? `${loc}: ${msg}` : msg;
      })
      .filter(Boolean);
    return mapped.length > 0 ? mapped.join('；') : null;
  };
  const normalizeLaunchError = (ex: unknown): string => {
    if (ex instanceof ApiError) {
      const parsed = parseApiBody(ex.body) as { detail?: unknown } | null;
      const detail = parsed?.detail;
      if (ex.status === 400 && detail && typeof detail === 'object') {
        const code = String((detail as { code?: unknown }).code || '');
        if (code === 'product_url_required') {
          return '请先在产品页补齐 product_url（有效 http(s) 链接）后再启动 campaign。';
        }
        if (code === 'variant_whitelist_required') {
          return '该产品已有规格候选，必须选择至少一个白名单规格后才能启动。';
        }
      }
      if (ex.status === 409) {
        return '启动冲突：当前环境下 campaign 或 SKU 已有运行记录。可确认后 force 重试。';
      }
      if (ex.status === 422) {
        const d = normalize422Details(detail);
        return d ? `字段校验失败：${d}` : `字段校验失败：${errorSummary(ex)}`;
      }
      if (ex.status === 502 && detail && typeof detail === 'object') {
        const code = String((detail as { code?: unknown }).code || '');
        if (code === 'cal_upsert_failed') {
          return '启动失败：CAL 写入失败（cal_upsert_failed），请稍后重试或联系值班同学。';
        }
      }
    }
    return errorSummary(ex);
  };
  const buildLaunchBody = (): Record<string, unknown> => {
    const body: Record<string, unknown> = {
      product_sku: sku,
      product_display_name: trimmedDisplayName,
      env,
      budget_per_kol: budgetPerKol,
      absolute_floor: absoluteFloor,
      budget_total: budgetTotal,
      headcount_target: headcountTarget,
      product_pitch_md: productPitchMd,
      brief_extra: briefExtra || null,
      compensation_mode: compensationMode,
      deliverable_platforms: platforms,
      deliverable_count_per_platform: deliverableCount,
    };
    if (testModeTo.trim()) body.test_mode_to = testModeTo.trim();
    if (discoveryTargetOverride !== '') body.discovery_target_count = discoveryTargetOverride;
    if (pickedVariantIds.length > 0) body.product_variant_ids = pickedVariantIds;
    if (needsCommission && commissionMinPct !== '' && commissionMaxPct !== '') {
      body.commission_min_pct = commissionMinPct;
      body.commission_max_pct = commissionMaxPct;
    }
    if (auditStandardsMd.trim()) body.audit_standards_md = auditStandardsMd.trim();
    return body;
  };
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (busy) return;
    if (preflightBlockers.length > 0) {
      onError(`启动前预检未通过：${preflightBlockers.join('；')}`);
      return;
    }
    if (env === 'LIVE') {
      const ok = await dialog.confirm({
        title: `确认在 LIVE 启动 ${campaignId}？`,
        description: '将触发真实运营链路，邮件在审批后可真实发送。',
        confirmLabel: '确认启动',
        cancelLabel: '取消',
        variant: 'danger',
        liveWarning: true,
      });
      if (!ok) return;
    }
    const requestStart = async (force: boolean) =>
      api.post<{ run_id?: string }>(
        `/campaigns/${encodeURIComponent(campaignId)}/start${force ? '?force=true' : ''}`,
        buildLaunchBody(),
      );
    setBusy(true);
    try {
      let r: { run_id?: string };
      try {
        r = await requestStart(false);
      } catch (ex) {
        if (ex instanceof ApiError && ex.status === 409) {
          const ok = await dialog.confirm({
            title: '检测到冲突，是否 force 重试？',
            description: '同 SKU 或 campaign 已在运行中，force 会绕过去重守卫再启动一次。',
            confirmLabel: 'force 重试',
            cancelLabel: '取消',
            variant: 'danger',
            liveWarning: env === 'LIVE',
          });
          if (!ok) {
            onError(normalizeLaunchError(ex));
            return;
          }
          r = await requestStart(true);
        } else {
          throw ex;
        }
      }
      onLaunched(r.run_id ?? null, campaignId);
    } catch (ex) {
      onError(normalizeLaunchError(ex));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="space-y-2 text-xs">
      <div className="sticky top-0 z-10 rounded border border-slate-200 bg-slate-50 px-2 py-2">
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => setActiveStep('A')}
            className={`rounded px-2 py-1 ${activeStep === 'A' ? 'bg-emerald-600 text-white' : 'border border-slate-300 bg-white text-slate-700'}`}
          >
            Step A 基础与环境
          </button>
          <button
            type="button"
            onClick={() => setActiveStep('B')}
            className={`rounded px-2 py-1 ${activeStep === 'B' ? 'bg-emerald-600 text-white' : 'border border-slate-300 bg-white text-slate-700'}`}
          >
            Step B 产品与白名单
          </button>
          <button
            type="button"
            onClick={() => setActiveStep('C')}
            className={`rounded px-2 py-1 ${activeStep === 'C' ? 'bg-emerald-600 text-white' : 'border border-slate-300 bg-white text-slate-700'}`}
          >
            Step C 预算交付与预检
          </button>
        </div>
      </div>
      <div className={`${activeStep !== 'A' ? 'hidden' : ''} space-y-2`}>
      <div className="flex flex-wrap items-end gap-2">
        <label className="flex flex-col">
          <span className="text-slate-500">campaign_id</span>
          <input
            value={campaignId}
            onChange={(e) => setCampaignId(e.target.value)}
            className="rounded border px-2 py-1 font-mono"
          />
        </label>
        <label className="flex flex-col">
          <span className="text-slate-500">
            product_display_name <span className="text-amber-600">*</span>
            <span className="ml-1 font-normal text-slate-400">
              (cold-outreach 邮件里给 KOL 看的产品名，必须是人话；不能写成 SKU / campaign_id)
            </span>
          </span>
          <input
            value={productDisplayName}
            onChange={(e) => setProductDisplayName(e.target.value)}
            placeholder='例如 "the new media console" / "POVISON Atlas sofa"'
            className="w-80 rounded border px-2 py-1"
          />
        </label>
        <label className="flex flex-col">
          <span className="text-slate-500">paid_ceiling / KOL (USD)</span>
          <input
            type="number"
            min={0}
            value={budgetPerKol}
            onChange={(e) => setBudgetPerKol(Number(e.target.value))}
            className="w-24 rounded border px-2 py-1"
          />
        </label>
        <label className="flex flex-col">
          <span className="text-slate-500">absolute_floor (USD)</span>
          <input
            type="number"
            min={0}
            value={absoluteFloor}
            onChange={(e) => setAbsoluteFloor(Number(e.target.value))}
            className="w-24 rounded border px-2 py-1"
          />
        </label>
        <label className="flex flex-col">
          <span className="text-slate-500">total budget（仅写入 brief, USD）</span>
          <input
            type="number"
            min={0}
            value={budgetTotal}
            onChange={(e) => setBudgetTotal(Number(e.target.value))}
            className="w-24 rounded border px-2 py-1"
          />
        </label>
        <label className="flex flex-col">
          <span className="text-slate-500">headcount_target</span>
          <input
            type="number"
            min={1}
            value={headcountTarget}
            onChange={(e) => setHeadcountTarget(Number(e.target.value))}
            className="w-20 rounded border px-2 py-1"
          />
        </label>
        <label className="flex flex-col">
          <span className="text-slate-500">
            discovery_target_count{' '}
            <span className="text-slate-400">(默认 {discoveryDefault} ≈3×)</span>
          </span>
          <input
            type="number"
            min={headcountTarget}
            value={discoveryTargetOverride}
            onChange={(e) => {
              const v = e.target.value;
              setDiscoveryTargetOverride(v === '' ? '' : Number(v));
            }}
            placeholder={String(discoveryDefault)}
            className="w-24 rounded border px-2 py-1"
          />
        </label>
        <label className="flex flex-col">
          <span className="text-slate-500">
            test_mode_to {env === 'TEST' && <span className="text-amber-600">*</span>}
          </span>
          <input
            type="email"
            value={testModeTo}
            onChange={(e) => setTestModeTo(e.target.value)}
            placeholder="me@example.com"
            className="w-56 rounded border px-2 py-1"
          />
        </label>
      </div>
      <div className="flex justify-end">
        <button
          type="button"
          onClick={() => setActiveStep('B')}
          className="rounded border border-slate-300 px-3 py-1 text-slate-700 hover:bg-slate-100"
        >
          下一步：产品与白名单
        </button>
      </div>
      </div>
      <div className={`${activeStep !== 'B' ? 'hidden' : ''} space-y-2`}>
      {product.variants.length > 0 && (
        <div className="rounded border border-slate-200 p-2">
          <div className="mb-1 flex flex-wrap items-center gap-2 text-xs font-medium text-slate-700">
            <span>
              campaign variant whitelist <span className="text-amber-600">*</span>
            </span>
            <span className="text-slate-500">{pickedVariantIds.length} / {product.variants.length} selected</span>
            <button
              type="button"
              onClick={() => {
                setVariantPicked(Object.fromEntries(product.variants.map((v) => [v.id, true])));
              }}
              className="rounded border border-slate-300 px-2 py-0.5 text-slate-700 hover:bg-slate-100"
            >
              全选
            </button>
            <button
              type="button"
              onClick={() => {
                setVariantPicked(Object.fromEntries(product.variants.map((v) => [v.id, false])));
              }}
              className="rounded border border-slate-300 px-2 py-0.5 text-slate-700 hover:bg-slate-100"
            >
              清空
            </button>
            <button
              type="button"
              onClick={() => {
                setVariantPicked((prev) => {
                  const next = { ...prev };
                  for (const v of filteredVariants) next[v.id] = true;
                  return next;
                });
              }}
              className="rounded border border-slate-300 px-2 py-0.5 text-slate-700 hover:bg-slate-100"
            >
              勾选筛选结果
            </button>
            <span className="ml-1 font-normal text-slate-400">
              (此 campaign 允许 KOL 选哪些规格 — 合同 PRODUCT_SPECS 会用到)
            </span>
          </div>
          <div className="mb-2 grid grid-cols-1 gap-2 md:grid-cols-3">
            <label className="flex flex-col text-[11px]">
              <span className="text-slate-500">按属性筛选 key</span>
              <select
                value={variantFilterKey}
                onChange={(e) => {
                  setVariantFilterKey(e.target.value);
                  setVariantFilterValue('');
                }}
                className="rounded border px-2 py-1"
              >
                <option value="">全部属性</option>
                {variantAttrKeys.map((k) => (
                  <option key={k} value={k}>{k}</option>
                ))}
              </select>
            </label>
            <label className="flex flex-col text-[11px]">
              <span className="text-slate-500">按属性筛选 value</span>
              <select
                value={variantFilterValue}
                onChange={(e) => setVariantFilterValue(e.target.value)}
                disabled={!variantFilterKey}
                className="rounded border px-2 py-1 disabled:bg-slate-100"
              >
                <option value="">全部值</option>
                {variantAttrValues.map((v) => (
                  <option key={v} value={v}>{v}</option>
                ))}
              </select>
            </label>
            <div className="flex items-end text-[11px] text-slate-500">
              当前筛选命中 {filteredVariants.length} / {product.variants.length}
            </div>
          </div>
          <ul className="grid grid-cols-1 gap-1 md:grid-cols-2">
            {filteredVariants.map((v) => (
              <li key={v.id} className="flex items-start gap-2 text-xs">
                <input
                  type="checkbox"
                  className="mt-0.5"
                  checked={!!variantPicked[v.id]}
                  onChange={(e) =>
                    setVariantPicked((prev) => ({ ...prev, [v.id]: e.target.checked }))
                  }
                />
                <span>
                  <span className="font-mono">{v.id}</span>
                  {v.label && <span className="ml-1 text-slate-600">— {v.label}</span>}
                  {v.url && (
                    <a
                      href={v.url}
                      target="_blank"
                      rel="noreferrer"
                      className="ml-1 text-emerald-700 underline"
                    >
                      url
                    </a>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {product.variants.length === 0 && (
        <div className="rounded border border-amber-200 bg-amber-50 px-2 py-1 text-xs text-amber-800">
          这个商品在 catalog 里还没录入 variant — 合同模板的 PRODUCT_SPECS 会留空。请先在
          {' '}<Link to="/products" className="underline">产品列表</Link>
          {' '}补齐 product_url 并解析 variant 后再启动 campaign。
        </div>
      )}
      <label className="flex flex-col">
        <span className="text-slate-500">
          product_pitch_md <span className="text-amber-600">*</span>
          <span className="ml-1 text-slate-400">
            (markdown，供 KOL discovery 提炼关键词 / 受众 / 送样策略；产品已存的 pitch 会自动预填)
          </span>
        </span>
        <textarea
          value={productPitchMd}
          onChange={(e) => setProductPitchMd(e.target.value)}
          rows={6}
          className="rounded border px-2 py-1 font-mono"
          placeholder={'例如：\n# Povison ABC 桌\n- 实木桃花心材\n- 60”餐桌\n- 适合美式 / 中古类家居博主\n- 送样策略：gifted 优先，避免现金'}
        />
      </label>
      <div className="flex justify-between">
        <button
          type="button"
          onClick={() => setActiveStep('A')}
          className="rounded border border-slate-300 px-3 py-1 text-slate-700 hover:bg-slate-100"
        >
          上一步：基础与环境
        </button>
        <button
          type="button"
          onClick={() => setActiveStep('C')}
          className="rounded border border-slate-300 px-3 py-1 text-slate-700 hover:bg-slate-100"
        >
          下一步：预算交付与预检
        </button>
      </div>
      </div>
      <div className={`${activeStep !== 'C' ? 'hidden' : ''} space-y-2`}>
      <div className="rounded border border-slate-200 p-2">
        <div className="mb-2 text-xs font-medium text-slate-700">补偿策略（Phase 3）</div>
        <div className="mb-2 grid grid-cols-1 gap-2 md:grid-cols-3">
          <label className="flex flex-col text-xs">
            <span className="text-slate-500">compensation_mode</span>
            <select
              value={compensationMode}
              onChange={(e) => setCompensationMode(e.target.value as 'gifted' | 'paid' | 'commission' | 'hybrid')}
              className="rounded border px-2 py-1"
            >
              <option value="gifted">gifted</option>
              <option value="paid">paid</option>
              <option value="commission">commission</option>
              <option value="hybrid">hybrid</option>
            </select>
          </label>
          <label className="flex flex-col text-xs">
            <span className="text-slate-500">commission_min_pct</span>
            <input
              type="number"
              min={0}
              max={100}
              disabled={!needsCommission}
              value={commissionMinPct}
              onChange={(e) => setCommissionMinPct(e.target.value === '' ? '' : Number(e.target.value))}
              className="rounded border px-2 py-1 disabled:bg-slate-100"
              placeholder={needsCommission ? '例如 10' : 'commission/hybrid 时可填'}
            />
          </label>
          <label className="flex flex-col text-xs">
            <span className="text-slate-500">commission_max_pct</span>
            <input
              type="number"
              min={0}
              max={100}
              disabled={!needsCommission}
              value={commissionMaxPct}
              onChange={(e) => setCommissionMaxPct(e.target.value === '' ? '' : Number(e.target.value))}
              className="rounded border px-2 py-1 disabled:bg-slate-100"
              placeholder={needsCommission ? '例如 20' : 'commission/hybrid 时可填'}
            />
          </label>
        </div>
        <div className="mb-1 text-xs font-medium text-slate-700">
          交付要求 <span className="text-amber-600">*</span>
          <span className="ml-1 font-normal text-slate-400">
            (合同模板 deliverables 表 + content 审核会用到)
          </span>
        </div>
        <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
          <div>
            <div className="text-[11px] text-slate-500">Deliverable platforms</div>
            <div className="flex flex-wrap gap-2 text-xs">
              {(['instagram', 'tiktok', 'youtube', 'twitter', 'blog'] as const).map((plat) => (
                <label key={plat} className="inline-flex items-center gap-1">
                  <input
                    type="checkbox"
                    checked={!!deliverablePlatforms[plat]}
                    onChange={(e) =>
                      setDeliverablePlatforms((prev) => ({ ...prev, [plat]: e.target.checked }))
                    }
                  />
                  {plat}
                </label>
              ))}
            </div>
          </div>
          <label className="flex flex-col text-xs">
            <span className="text-slate-500">deliverable_count_per_platform</span>
            <input
              type="number"
              min={1}
              max={20}
              value={deliverableCount}
              onChange={(e) => setDeliverableCount(Number(e.target.value))}
              className="w-24 rounded border px-2 py-1"
            />
          </label>
        </div>
        <label className="mt-2 flex flex-col text-xs">
          <span className="text-slate-500">
            audit_standards_md
            <span className="ml-1 text-slate-400">
              (可选；合规 / 品牌口径 / 必备 hashtag / 避雷)
            </span>
          </span>
          <textarea
            value={auditStandardsMd}
            onChange={(e) => setAuditStandardsMd(e.target.value)}
            rows={3}
            className="rounded border px-2 py-1 font-mono"
            placeholder={'例如：\n- 首句必须出现 #ad / 含 paid partnership 标记\n- 避免医疗 / 政治 / 饮食断言\n- 必须 @povison.official'}
          />
        </label>
      </div>
      <label className="flex flex-col">
        <span className="text-slate-500">brief_extra (额外要求 / 备注)</span>
        <textarea
          value={briefExtra}
          onChange={(e) => setBriefExtra(e.target.value)}
          rows={2}
          className="rounded border px-2 py-1"
          placeholder="例如：仅 US 区 KOL、要求英文邮件、避免与品牌 X 合作过的人..."
        />
      </label>
      <div className="rounded border border-slate-200 bg-slate-50 px-2 py-2 text-xs">
        <div className="font-medium text-slate-700">运行预检</div>
        {preflightBlockers.length === 0 ? (
          <div className="mt-1 text-emerald-700">必填校验通过，可启动。</div>
        ) : (
          <ul className="mt-1 list-disc pl-5 text-rose-700">
            {preflightBlockers.map((it) => <li key={it}>{it}</li>)}
          </ul>
        )}
        {preflightWarnings.length > 0 && (
          <ul className="mt-1 list-disc pl-5 text-amber-700">
            {preflightWarnings.map((it) => <li key={it}>{it}</li>)}
          </ul>
        )}
      </div>
      <div className="flex items-center justify-between">
        <span className="text-slate-400">
          环境：<strong className={env === 'LIVE' ? 'text-red-600' : 'text-emerald-700'}>{env}</strong>
          {env === 'LIVE' && '（会真实发邮件，请谨慎）'}
          <span className="ml-3">
            discovery 目标：<strong>{discoveryEffective}</strong> 名
            · 入池后人工筛选至 {headcountTarget} 名
          </span>
        </span>
        <button
          type="submit"
          disabled={busy || !canLaunch}
          title={!canLaunch ? 'viewer 角色不可启动 campaign' : undefined}
          className="rounded bg-emerald-600 px-3 py-1 font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
        >
          {busy ? '提交中…' : `Start campaign in ${env}`}
        </button>
      </div>
      {!canLaunch && (
        <div className="rounded border border-amber-200 bg-amber-50 px-2 py-1 text-xs text-amber-800">
          当前账号为 viewer，只能查看，不能启动 campaign。
        </div>
      )}
      <div className="flex justify-start">
        <button
          type="button"
          onClick={() => setActiveStep('B')}
          className="rounded border border-slate-300 px-3 py-1 text-slate-700 hover:bg-slate-100"
        >
          上一步：产品与白名单
        </button>
      </div>
      </div>
    </form>
  );
}

export function ProductDetailPage() {
  const { sku } = useParams<{ sku: string }>();
  const [p, setP] = useState<Product | null>(null);
  const [me, setMe] = useState<Me | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [campaigns, setCampaigns] = useState<CampaignsPayload>({ campaigns: [], kols: {} });
  const envFilter = useEnvStore((s) => s.env);
  const [watcherStatus, setWatcherStatus] = useState<ReplyWatcherStatus | null>(null);
  const [watcherEnv, setWatcherEnv] = useState<'TEST' | 'LIVE'>('TEST');
  const [watcherInterval, setWatcherInterval] = useState(60);
  const [watcherBusy, setWatcherBusy] = useState(false);

  const refreshCampaigns = () => {
    if (!sku) return;
    api
      .get<CampaignsPayload>(`/products/${encodeURIComponent(sku)}/campaigns?env=${envFilter}`)
      .then(setCampaigns)
      .catch((e) => setErr(e));
  };

  const refreshWatcher = () => {
    api
      .get<ReplyWatcherStatus>('/reply-watcher/status')
      .then((status) => {
        setWatcherStatus(status);
        if (status.running && status.env) setWatcherEnv(status.env);
        if (status.interval) setWatcherInterval(status.interval);
      })
      .catch((e) => setErr(e));
  };

  useEffect(() => {
    if (!sku) return;
    let cancelled = false;
    void Promise.all([
      api.get<Product>(`/products/${encodeURIComponent(sku)}`),
      api.get<CampaignsPayload>(
        `/products/${encodeURIComponent(sku)}/campaigns?env=${envFilter}`,
      ),
    ])
      .then(([product, payload]) => {
        if (cancelled) return;
        setP(product);
        setCampaigns(payload);
        setErr(null);
      })
      .catch((e) => {
        if (!cancelled) setErr(e);
      });
    return () => {
      cancelled = true;
    };
  }, [sku, envFilter]);

  useEffect(() => {
    refreshWatcher();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    api.get<Me>('/auth/me').then(setMe).catch(() => undefined);
  }, []);

  usePollingFallback(refreshCampaigns, 25_000);
  usePollingFallback(refreshWatcher, 30_000);

  const mutateWatcher = async (action: 'start' | 'stop' | 'restart') => {
    setErr(null);
    setWatcherBusy(true);
    try {
      const body = action === 'stop' ? undefined : {
        env: watcherEnv,
        interval: watcherInterval,
        lookback_days: 3,
        max_results: 50,
      };
      const status = await api.post<ReplyWatcherStatus>(`/reply-watcher/${action}`, body);
      setWatcherStatus(status);
      toast.success(
        status.running
          ? `Reply watcher 已在 ${status.env} 运行 · pid ${status.pid}`
          : 'Reply watcher 已停止',
      );
    } catch (ex) {
      setErr(ex);
      toast.error(`Reply watcher ${action} 失败`, errorSummary(ex));
    } finally {
      setWatcherBusy(false);
    }
  };

  const syncSent = async () => {
    setErr(null);
    setWatcherBusy(true);
    try {
      const out = await api.post<{ reconciled_count?: number; sent_threads_seen?: number }>(
        '/reply-watcher/reconcile-sent',
        { env: watcherEnv, lookback_days: 7, max_results: 100 },
      );
      toast.success(
        'SENT 同步完成',
        `已对账 ${out.reconciled_count ?? 0} 条 · 共扫描 ${out.sent_threads_seen ?? 0} 个 SENT 线程`,
      );
      refreshCampaigns();
    } catch (ex) {
      setErr(ex);
      toast.error('SENT 同步失败', errorSummary(ex));
    } finally {
      setWatcherBusy(false);
    }
  };

  const close = async (cid: string, env: string) => {
    const ok = await dialog.confirm({
      title: `关闭 campaign ${cid}？`,
      description: '会尽力 stop 掉 gateway 上的 run，然后把 campaign 标为 closed。',
      confirmLabel: '关闭',
      cancelLabel: '取消',
      variant: 'danger',
      liveWarning: env === 'LIVE',
    });
    if (!ok) return;
    setErr(null);
    try {
      const out = await api.post<CloseCampaignResponse>(
        `/campaigns/${encodeURIComponent(cid)}/close?env=${env}`,
        { status: 'closed' },
      );
      const stop = out.stop_result;
      let msg = `Campaign ${cid} 已关闭`;
      if (stop?.gateway_status === 'stopping') msg = `已请求停止 ${out.run_id}，campaign ${cid} 已关闭`;
      else if (stop?.gateway_status === 'not_found') msg = `Campaign ${cid} 已关闭，run ${out.run_id} 已不可见`;
      else if (stop?.error) msg = `Campaign ${cid} 已关闭，但 stop 请求失败：${stop.error}`;
      toast.success(msg);
      refreshCampaigns();
    } catch (ex) {
      setErr(ex);
      toast.error('关闭失败', errorSummary(ex));
    }
  };

  const rediscover = async (cid: string, env: string, additionalCount: number) => {
    setErr(null);
    try {
      const r = await api.post<{
        run_id?: string | null;
        additional_count?: number;
        excluded_handle_count?: number;
      }>(`/campaigns/${encodeURIComponent(cid)}/rediscover`, {
        env,
        additional_count: additionalCount,
      });
      toast.success(
        '已开始再发现',
        `run ${r.run_id ?? '(none)'} · 目标 +${r.additional_count ?? additionalCount} · 排除 ${r.excluded_handle_count ?? 0} 个已知 handle`,
      );
      refreshCampaigns();
    } catch (ex) {
      if (ex instanceof ApiError) {
        type RediscoverErr = {
          detail?: { code?: string; message?: string } | string;
        };
        let parsed: RediscoverErr | null = null;
        try {
          parsed = JSON.parse(ex.body) as RediscoverErr;
        } catch {
          parsed = null;
        }
        const detail = parsed?.detail;
        let msg = '';
        if (detail && typeof detail === 'object' && 'message' in detail) {
          const code = detail.code;
          msg = `(${ex.status}${code ? ` · ${code}` : ''}) ${detail.message ?? ex.body}`;
        } else if (typeof detail === 'string') {
          msg = `(${ex.status}) ${detail}`;
        } else {
          msg = `(${ex.status}) ${ex.body}`;
        }
        toast.error('再发现被拒绝', msg);
      } else {
        toast.error('再发现失败', errorSummary(ex));
      }
      setErr(ex);
      throw ex;
    }
  };

  const approveShortlist = async (cid: string, env: string, selected: string[]) => {
    setErr(null);
    try {
      const r = await api.post<{ run_id?: string; approved_count?: number }>(
        `/campaigns/${encodeURIComponent(cid)}/approve-shortlist`,
        { env, selected_handles: selected },
      );
      toast.success(
        `已批准 ${r.approved_count ?? selected.length} 个 KOL`,
        `起草 run ${r.run_id ?? '(none)'}`,
      );
      refreshCampaigns();
    } catch (ex) {
      setErr(ex);
      toast.error('批准 shortlist 失败', errorSummary(ex));
      throw ex;
    }
  };

  if (err && !p) return <ErrorAlert error={err} onRetry={() => sku && api.get<Product>(`/products/${encodeURIComponent(sku)}`).then(setP).catch((e) => setErr(e))} />;
  if (!p) return <div className="text-sm text-slate-500">加载中…</div>;

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold">
        {p.sku} — {p.name}
      </h1>
      {p.url && (
        <a href={p.url} target="_blank" rel="noreferrer" className="text-sm text-emerald-700 underline">
          {p.url}
        </a>
      )}
      {p.notes && <p className="text-sm text-slate-600">{p.notes}</p>}

      <div className="rounded border bg-white p-3 text-xs">
        <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
          <div>
            <div className="font-medium text-slate-500">Selling points</div>
            <div className="whitespace-pre-wrap text-slate-700">
              {p.selling_points || <span className="italic text-slate-400">(none recorded)</span>}
            </div>
          </div>
          <div>
            <div className="font-medium text-slate-500">Launch defaults</div>
            <div className="text-slate-700">
              paid ceiling / KOL: {p.default_budget_per_kol ?? '—'} ·
              total budget (brief only): {p.default_budget_total ?? '—'} ·
              floor: {p.default_absolute_floor ?? '—'}
            </div>
          </div>
        </div>
        <div className="mt-2">
          <div className="font-medium text-slate-500">
            Variants ({p.variants.length})
            <Link to="/products" className="ml-2 text-sky-700 hover:underline">
              edit in catalog →
            </Link>
          </div>
          {p.variants.length === 0 ? (
            <div className="italic text-slate-400">No variants on record.</div>
          ) : (
            <ul className="flex flex-wrap gap-1">
              {p.variants.map((v) => (
                <li
                  key={v.id}
                  className="inline-flex items-center gap-1 rounded bg-indigo-50 px-2 py-0.5 text-indigo-800"
                >
                  <span className="font-mono">{v.id}</span>
                  {v.label && <span>· {v.label}</span>}
                  {v.price != null && (
                    <span className="ml-1 text-slate-600">${v.price}</span>
                  )}
                  {v.url && (
                    <a
                      href={v.url}
                      target="_blank"
                      rel="noreferrer"
                      className="ml-1 text-emerald-700 hover:underline"
                    >
                      ↗
                    </a>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      <section className="space-y-2">
        <div className="flex items-center gap-2">
          <h2 className="font-medium">Campaigns（env={envFilter}，可在顶部切换）</h2>
          <button
            onClick={refreshCampaigns}
            className="rounded border border-slate-300 px-2 py-0.5 text-xs text-slate-600 hover:bg-slate-50"
          >
            刷新
          </button>
        </div>

        <div data-editing className="rounded border border-slate-200 bg-white p-3">
          <LaunchCampaignForm
            sku={p.sku}
            env={envFilter}
            product={p}
            canLaunch={me?.role !== 'viewer'}
            onLaunched={(runId, campaignId) => {
              toast.success(
                `Campaign ${campaignId} 已在 ${envFilter} 启动`,
                `gateway run ${runId ?? '(none)'}`,
              );
              refreshCampaigns();
            }}
            onError={(e) => {
              setErr(new Error(e));
              toast.error('启动失败', e);
            }}
          />
        </div>

        <ReplyWatcherPanel
          status={watcherStatus}
          env={watcherEnv}
          interval={watcherInterval}
          busy={watcherBusy}
          onEnvChange={setWatcherEnv}
          onIntervalChange={setWatcherInterval}
          onStart={() => mutateWatcher('start')}
          onStop={() => mutateWatcher('stop')}
          onRestart={() => mutateWatcher('restart')}
          onSyncSent={syncSent}
          onRefresh={refreshWatcher}
        />

        {campaigns.campaigns.length === 0 ? (
          <div className="rounded border bg-white p-4 text-sm text-slate-500">
            {envFilter} 环境下还没有这个 SKU 的 campaign。
          </div>
        ) : (
          <ul className="space-y-2">
            {campaigns.campaigns.map((c) => (
              <CampaignCard
                key={`${c.campaign_id}:${c.env}`}
                c={c}
                kols={campaigns.kols}
                onClose={close}
                onApprove={approveShortlist}
                onRediscover={rediscover}
              />
            ))}
          </ul>
        )}
      </section>

      {!!err && <ErrorAlert error={err} onRetry={refreshCampaigns} />}
    </div>
  );
}

