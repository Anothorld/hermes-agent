import { useEffect, useState, type FormEvent } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api } from '../api';
import AgentTranscriptPanel from '../components/AgentTranscriptPanel';
import ContractReadinessPanel from '../components/ContractReadinessPanel';
import EditCampaignConfigPanel from '../components/EditCampaignConfigPanel';

type ProductVariant = {
  id: string;
  label?: string | null;
  url?: string | null;
  attributes?: Record<string, string>;
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
  shortlist_ready: boolean;
  shortlist_approved: boolean;
  event_count: number;
  run_state: string | null;
  run_error: string | null;
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
  const cls =
    s === 'running' || s === 'queued' || s === 'waiting_for_approval' || s === 'stopping'
      ? 'bg-sky-100 text-sky-800'
      : s === 'completed'
      ? 'bg-emerald-100 text-emerald-800'
      : s === 'failed'
      ? 'bg-rose-100 text-rose-800'
      : s === 'cancelled'
      ? 'bg-amber-100 text-amber-800'
      : 'bg-slate-100 text-slate-700';
  return <span className={`rounded px-2 py-0.5 text-xs ${cls}`} title="Gateway run_state">agent: {s}</span>;
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

function KolList({
  ids,
  kols,
  emptyText = 'no KOL events yet',
}: {
  ids: number[];
  kols: Record<string, KolIdent>;
  emptyText?: string;
}) {
  if (ids.length === 0) return <span className="text-xs text-slate-400">{emptyText}</span>;
  return (
    <ul className="flex flex-wrap gap-1 text-xs">
      {ids.map((id) => {
        const k = kols[String(id)];
        const label = k?.display_name || k?.primary_handle || `#${id}`;
        return (
          <li key={id} className="inline-flex items-center gap-1 rounded bg-slate-100 px-2 py-0.5 text-slate-700">
            <Link to={`/kols/${id}`} className="hover:text-emerald-700">
              {label}
              {k?.platform && <span className="ml-1 text-slate-400">({k.platform})</span>}
            </Link>
          </li>
        );
      })}
    </ul>
  );
}

type ShortlistCandidate = {
  handle: string;
  platform: string | null;
  identity_id: number | null;
  display_name: string | null;
  audience_fit: number | null;
  brand_safety: number | null;
  engagement_quality: number | null;
  niche_match: number | null;
  reason: string | null;
};

function ScoreBar({ label, value }: { label: string; value: number | null }) {
  const pct = typeof value === 'number' ? Math.max(0, Math.min(100, value)) : 0;
  const tone =
    pct >= 80 ? 'bg-emerald-500' : pct >= 60 ? 'bg-sky-500' : pct >= 40 ? 'bg-amber-500' : 'bg-rose-500';
  return (
    <div className="space-y-0.5">
      <div className="flex justify-between text-[10px] text-slate-500">
        <span>{label}</span>
        <span className="font-mono">{value ?? '—'}</span>
      </div>
      <div className="h-1.5 w-full rounded bg-slate-100">
        {value !== null && <div className={`h-full rounded ${tone}`} style={{ width: `${pct}%` }} />}
      </div>
    </div>
  );
}

function ShortlistReviewPanel({
  campaignId,
  env,
  onSubmit,
}: {
  campaignId: string;
  env: string;
  onSubmit: (selectedHandles: string[]) => Promise<void>;
}) {
  const [candidates, setCandidates] = useState<ShortlistCandidate[] | null>(null);
  const [picked, setPicked] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .get<{ candidates: ShortlistCandidate[] }>(
        `/campaigns/${encodeURIComponent(campaignId)}/shortlist?env=${env}`,
      )
      .then((r) => {
        if (!alive) return;
        setCandidates(r.candidates);
        const init: Record<string, boolean> = {};
        for (const c of r.candidates) init[c.handle] = true;
        setPicked(init);
      })
      .catch((ex) => alive && setErr(String(ex)));
    return () => {
      alive = false;
    };
  }, [campaignId, env]);

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

  const selectedHandles = Object.entries(picked).filter(([, v]) => v).map(([k]) => k);

  return (
    <div className="rounded border border-emerald-200 bg-emerald-50/40 p-3">
      <div className="mb-2 flex items-center justify-between">
        <div className="text-xs font-medium text-emerald-900">
          Shortlist review · {candidates.length} candidate{candidates.length === 1 ? '' : 's'} · select to approve
        </div>
        <div className="flex gap-2 text-xs">
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
      <ul className="space-y-2">
        {candidates.map((c) => (
          <li
            key={c.handle}
            className="rounded border border-emerald-100 bg-white p-2 text-xs"
          >
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
                        to={`/kols/${c.identity_id}`}
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
                  {c.display_name && c.display_name !== c.handle && (
                    <span className="text-slate-500">{c.display_name}</span>
                  )}
                </div>
                <div className="mt-1.5 grid grid-cols-2 gap-x-3 gap-y-1 sm:grid-cols-4">
                  <ScoreBar label="Audience fit" value={c.audience_fit} />
                  <ScoreBar label="Brand safety" value={c.brand_safety} />
                  <ScoreBar label="Engagement" value={c.engagement_quality} />
                  <ScoreBar label="Niche match" value={c.niche_match} />
                </div>
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
              </div>
            </label>
          </li>
        ))}
      </ul>
      <div className="mt-2 flex items-center justify-end gap-2">
        <span className="text-xs text-slate-500">{selectedHandles.length} selected</span>
        <button
          disabled={busy || selectedHandles.length === 0}
          onClick={async () => {
            setBusy(true);
            setErr(null);
            try {
              await onSubmit(selectedHandles);
            } catch (ex) {
              setErr(String(ex));
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

function CampaignCard({
  c,
  kols,
  onClose,
  onApprove,
}: {
  c: CampaignRow;
  kols: Record<string, KolIdent>;
  onClose: (id: string, env: string) => void;
  onApprove: (id: string, env: string, selectedHandles: string[]) => Promise<void>;
}) {
  const [showReview, setShowReview] = useState(false);
  const [retryingDrafts, setRetryingDrafts] = useState(false);
  const approvedHandles = c.kol_identity_ids
    .map((id) => kols[String(id)]?.primary_handle)
    .filter((handle): handle is string => Boolean(handle));
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
        <span className="ml-auto text-xs text-slate-400">
          started {c.started_at.replace('T', ' ').slice(0, 19)}
        </span>
        {c.status === 'running' && (
          <button
            onClick={() => onClose(c.campaign_id, c.env)}
            className="rounded border border-slate-300 px-2 py-0.5 text-xs text-slate-600 hover:bg-slate-50"
            title="Best-effort stop the gateway run, then close this campaign in the console"
          >
            Stop + close
          </button>
        )}
        {(c.shortlist_ready || c.candidate_count > 0) && !c.shortlist_approved && (
          <button
            onClick={() => setShowReview((v) => !v)}
            className="rounded border border-emerald-300 bg-emerald-50 px-2 py-0.5 text-xs text-emerald-800 hover:bg-emerald-100"
            title="Review discovered candidates with scores + reasons, then approve a subset"
          >
            {showReview ? '× Close review' : `✓ Review candidates (${c.candidate_count})`}
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
        {c.shortlist_approved && c.contacted_kol_ids.length === 0 && approvedHandles.length > 0 && (
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
          onSubmit={async (handles) => {
            await onApprove(c.campaign_id, c.env, handles);
            setShowReview(false);
          }}
        />
      )}
      <StageBadge stage={c.stage} />
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
      <div className="grid grid-cols-1 gap-2 text-xs text-slate-600 md:grid-cols-3">
        <div>
          <div className="font-medium text-slate-500">sub_status</div>
          <div>{c.sub_status ?? '—'}</div>
        </div>
        <div>
          <div className="font-medium text-slate-500">last event</div>
          <div>
            {c.last_event_type ?? '—'}
            {c.last_event_ts && (
              <span className="ml-1 text-slate-400">
                @ {c.last_event_ts.replace('T', ' ').slice(0, 19)}
              </span>
            )}
          </div>
        </div>
        <div>
          <div className="font-medium text-slate-500">events</div>
          <div>{c.event_count} · candidates {c.candidate_count}</div>
        </div>
      </div>
      <div>
        <div className="mb-1 text-xs font-medium text-slate-500">
          KOLs contacted ({c.contacted_kol_ids.length})
          <span
            className="ml-2 text-slate-400"
            title="A KOL is 'contacted' once an initial outreach draft has been written for them"
          >
            ⓘ
          </span>
          {c.kol_identity_ids.length > c.contacted_kol_ids.length && (
            <span className="ml-2 font-normal text-slate-400">
              · {c.kol_identity_ids.length - c.contacted_kol_ids.length} discovered, not yet contacted
            </span>
          )}
        </div>
        <KolList
          ids={c.contacted_kol_ids}
          kols={kols}
          emptyText="暂无已建联 KOL — 审批 shortlist 后将生成初邀草稿"
        />
      </div>
      <EditCampaignConfigPanel campaignId={c.campaign_id} env={c.env} />
      {c.contacted_kol_ids.length > 0 && (
        <div className="space-y-1">
          <div className="text-xs font-medium text-slate-500">
            Contract readiness (pre-flight before合同生成)
          </div>
          <ul className="space-y-2">
            {c.contacted_kol_ids.map((iid) => {
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
      {c.run_id && (
        <div className="space-y-1">
          <AgentTranscriptPanel campaignId={c.campaign_id} env={c.env} live={c.status === 'running'} />
          <div className="flex justify-end text-[11px]">
            <Link
              to={`/campaigns/${encodeURIComponent(c.campaign_id)}/transcript?env=${c.env}&live=${c.status === 'running' ? '1' : '0'}`}
              className="text-sky-700 hover:underline"
            >
              open in full screen →
            </Link>
          </div>
        </div>
      )}
    </li>
  );
}

function LaunchCampaignForm({
  sku,
  env,
  product,
  onLaunched,
  onError,
}: {
  sku: string;
  env: 'TEST' | 'LIVE';
  product: Product;
  onLaunched: (runId: string | null, campaignId: string) => void;
  onError: (msg: string) => void;
}) {
  // Sensible defaults so the operator can launch with one click; all
  // fields are still editable for tuning per-campaign.
  const today = new Date().toISOString().slice(0, 10).replace(/-/g, '');
  const defaultCampaignId = `${sku}-${today}`;
  const [campaignId, setCampaignId] = useState(defaultCampaignId);
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
  const [busy, setBusy] = useState(false);

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

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (busy) return;
    if (env === 'TEST' && !testModeTo.trim()) {
      onError('TEST 模式必须填写 test_mode_to（接收测试邮件的地址）');
      return;
    }
    if (!productPitchMd.trim()) {
      onError('product_pitch_md 必填：请粘贴产品的卖点 / 类别 / 受众 / 送样策略，供 KOL discovery 使用');
      return;
    }
    if (product.variants.length > 0 && pickedVariantIds.length === 0) {
      onError('请至少勾选一个 variant — KOL 选品 / 合同模板需要它');
      return;
    }
    const platforms = Object.entries(deliverablePlatforms)
      .filter(([, v]) => v)
      .map(([k]) => k);
    if (platforms.length === 0) {
      onError('请至少勾选一个 deliverable platform — 合同模板要求');
      return;
    }
    if (!Number.isFinite(deliverableCount) || deliverableCount < 1) {
      onError('deliverable_count_per_platform 至少 1');
      return;
    }
    if (!auditStandardsMd.trim() || auditStandardsMd.trim().length < 30) {
      onError('audit_standards_md 必填（≥30 字符）— 内容审核 / 合同模板必须的依据');
      return;
    }
    setBusy(true);
    try {
      const body: Record<string, unknown> = {
        product_sku: sku,
        env,
        budget_per_kol: budgetPerKol,
        absolute_floor: absoluteFloor,
        budget_total: budgetTotal,
        headcount_target: headcountTarget,
        product_pitch_md: productPitchMd,
        brief_extra: briefExtra || null,
      };
      if (testModeTo.trim()) body.test_mode_to = testModeTo.trim();
      if (discoveryTargetOverride !== '') {
        body.discovery_target_count = discoveryTargetOverride;
      }
      if (pickedVariantIds.length > 0) {
        body.product_variant_ids = pickedVariantIds;
      }
      body.deliverable_platforms = platforms;
      body.deliverable_count_per_platform = deliverableCount;
      body.audit_standards_md = auditStandardsMd.trim();
      const r = await api.post<{ run_id?: string }>(
        `/campaigns/${encodeURIComponent(campaignId)}/start`,
        body,
      );
      onLaunched(r.run_id ?? null, campaignId);
    } catch (ex) {
      onError(String(ex));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="space-y-2 text-xs">
      <div className="flex flex-wrap items-end gap-2">
        <label className="flex flex-col">
          <span className="text-slate-500">campaign_id</span>
          <input
            value={campaignId}
            onChange={(e) => setCampaignId(e.target.value)}
            className="rounded border px-2 py-1 font-mono"
            required
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
            required
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
            required
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
            required
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
            required
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
      {product.variants.length > 0 && (
        <div className="rounded border border-slate-200 p-2">
          <div className="mb-1 text-xs font-medium text-slate-700">
            Eligible variants <span className="text-amber-600">*</span>
            <span className="ml-1 font-normal text-slate-400">
              (此 campaign 允许 KOL 选哪些规格 — 合同 PRODUCT_SPECS 会用到)
            </span>
          </div>
          <ul className="grid grid-cols-1 gap-1 md:grid-cols-2">
            {product.variants.map((v) => (
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
          这个商品在 catalog 里还没录入 variant — 合同模板的 PRODUCT_SPECS 会留空。建议先在
          {' '}<Link to="/products" className="underline">产品列表</Link>
          {' '}添加 variant 后再启动 campaign。
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
          required
        />
      </label>
      <div className="rounded border border-slate-200 p-2">
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
              required
            />
          </label>
        </div>
        <label className="mt-2 flex flex-col text-xs">
          <span className="text-slate-500">
            audit_standards_md <span className="text-amber-600">*</span>
            <span className="ml-1 text-slate-400">
              (≥30 字符；合规 / 品牌口径 / 必备 hashtag / 避雷)
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
          disabled={busy}
          className="rounded bg-emerald-600 px-3 py-1 font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
        >
          {busy ? '提交中…' : `Start campaign in ${env}`}
        </button>
      </div>
    </form>
  );
}

export function ProductDetailPage() {
  const { sku } = useParams<{ sku: string }>();
  const [p, setP] = useState<Product | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [campaigns, setCampaigns] = useState<CampaignsPayload>({ campaigns: [], kols: {} });
  const [envFilter, setEnvFilter] = useState<'TEST' | 'LIVE'>('TEST');
  const [msg, setMsg] = useState<string | null>(null);
  const [watcherStatus, setWatcherStatus] = useState<ReplyWatcherStatus | null>(null);
  const [watcherEnv, setWatcherEnv] = useState<'TEST' | 'LIVE'>('TEST');
  const [watcherInterval, setWatcherInterval] = useState(60);
  const [watcherBusy, setWatcherBusy] = useState(false);

  const refreshCampaigns = () => {
    if (!sku) return;
    api
      .get<CampaignsPayload>(`/products/${encodeURIComponent(sku)}/campaigns?env=${envFilter}`)
      .then(setCampaigns)
      .catch((e) => setErr(String(e)));
  };

  const refreshWatcher = () => {
    api
      .get<ReplyWatcherStatus>('/reply-watcher/status')
      .then((status) => {
        setWatcherStatus(status);
        if (status.running && status.env) setWatcherEnv(status.env);
        if (status.interval) setWatcherInterval(status.interval);
      })
      .catch((e) => setErr(String(e)));
  };

  useEffect(() => {
    if (!sku) return;
    api
      .get<Product>(`/products/${encodeURIComponent(sku)}`)
      .then(setP)
      .catch((e) => setErr(String(e)));
  }, [sku]);

  useEffect(() => {
    refreshCampaigns();
    const t = setInterval(refreshCampaigns, 10_000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sku, envFilter]);

  useEffect(() => {
    refreshWatcher();
    const t = setInterval(refreshWatcher, 5_000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const mutateWatcher = async (action: 'start' | 'stop' | 'restart') => {
    setMsg(null);
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
      setMsg(
        status.running
          ? `Reply watcher running in ${status.env} · pid ${status.pid}`
          : 'Reply watcher stopped.',
      );
    } catch (ex) {
      setErr(`Reply watcher ${action} failed: ${String(ex)}`);
    } finally {
      setWatcherBusy(false);
    }
  };

  const syncSent = async () => {
    setMsg(null);
    setErr(null);
    setWatcherBusy(true);
    try {
      const out = await api.post<{ reconciled_count?: number; sent_threads_seen?: number }>(
        '/reply-watcher/reconcile-sent',
        { env: watcherEnv, lookback_days: 7, max_results: 100 },
      );
      setMsg(
        `Sent sync complete · ${out.reconciled_count ?? 0} reconciled · ${out.sent_threads_seen ?? 0} sent threads seen`,
      );
      refreshCampaigns();
    } catch (ex) {
      setErr(`Sent sync failed: ${String(ex)}`);
    } finally {
      setWatcherBusy(false);
    }
  };

  const close = async (cid: string, env: string) => {
    setMsg(null);
    setErr(null);
    try {
      const out = await api.post<CloseCampaignResponse>(
        `/campaigns/${encodeURIComponent(cid)}/close?env=${env}`,
        { status: 'closed' },
      );
      const stop = out.stop_result;
      if (!out.run_id) {
        setMsg(`Campaign ${cid} closed in console.`);
      } else if (stop?.gateway_status === 'stopping') {
        setMsg(`Stop requested for ${out.run_id} and campaign ${cid} was closed.`);
      } else if (stop?.gateway_status === 'not_found') {
        setMsg(`Campaign ${cid} closed. Gateway no longer tracks run ${out.run_id}.`);
      } else if (stop?.error) {
        setMsg(`Campaign ${cid} closed, but stop request failed: ${stop.error}`);
      } else {
        setMsg(`Campaign ${cid} closed in console.`);
      }
      refreshCampaigns();
    } catch (ex) {
      setErr(String(ex));
    }
  };

  const approveShortlist = async (cid: string, env: string, selected: string[]) => {
    setMsg(null);
    setErr(null);
    try {
      const r = await api.post<{ run_id?: string; approved_count?: number }>(
        `/campaigns/${encodeURIComponent(cid)}/approve-shortlist`,
        { env, selected_handles: selected },
      );
      setMsg(
        `Approved ${r.approved_count ?? selected.length} KOLs · drafting run ${r.run_id ?? '(none)'}`,
      );
      refreshCampaigns();
    } catch (ex) {
      setErr(`Approve shortlist failed: ${String(ex)}`);
      throw ex;
    }
  };

  if (err && !p) return <div className="text-red-600">{err}</div>;
  if (!p) return <div>Loading…</div>;

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
          <h2 className="font-medium">Campaigns</h2>
          <select
            value={envFilter}
            onChange={(e) => setEnvFilter(e.target.value as 'TEST' | 'LIVE')}
            className="rounded border px-2 py-0.5 text-xs"
          >
            <option value="TEST">TEST</option>
            <option value="LIVE">LIVE</option>
          </select>
          <button
            onClick={refreshCampaigns}
            className="rounded border border-slate-300 px-2 py-0.5 text-xs text-slate-600 hover:bg-slate-50"
          >
            Refresh
          </button>
        </div>

        <div className="rounded border border-slate-200 bg-white p-3">
          <LaunchCampaignForm
            sku={p.sku}
            env={envFilter}
            product={p}
            onLaunched={(runId, campaignId) => {
              setMsg(
                `Campaign ${campaignId} launched in ${envFilter} · gateway run ${runId ?? '(none)'}`,
              );
              refreshCampaigns();
            }}
            onError={(e) => setErr(e)}
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
            No campaigns triggered for this SKU in {envFilter} yet.
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
              />
            ))}
          </ul>
        )}
      </section>

      {msg && <div className="text-sm text-emerald-700">{msg}</div>}
      {err && <div className="text-sm text-red-600">{err}</div>}
    </div>
  );
}

function StageBadge({ stage }: { stage: string | null }) {
  if (!stage) return null;
  return (
    <span className="rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-700">
      {stage}
    </span>
  );
}
