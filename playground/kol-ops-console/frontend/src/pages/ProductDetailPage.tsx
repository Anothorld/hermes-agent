import { FormEvent, useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api, ApiError } from '../api';
import { StageProgressBar } from '../components/StageProgressBar';

type Product = { sku: string; name: string; url: string | null; tags: string[]; notes: string | null };

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

function KolList({
  ids,
  kols,
  onSimulateReply,
  emptyText = 'no KOL events yet',
}: {
  ids: number[];
  kols: Record<string, KolIdent>;
  onSimulateReply?: (kolId: number, kolLabel: string) => void;
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
            {onSimulateReply && (
              <button
                onClick={() => onSimulateReply(id, label)}
                className="rounded border border-slate-300 px-1 text-[10px] text-slate-500 hover:bg-white"
                title="Simulate an inbound Gmail reply from this KOL"
              >
                sim reply
              </button>
            )}
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
  onSimulateReply,
}: {
  c: CampaignRow;
  kols: Record<string, KolIdent>;
  onClose: (id: string, env: string) => void;
  onApprove: (id: string, env: string, selectedHandles: string[]) => Promise<void>;
  onSimulateReply: (campaignId: string, env: string, kolId: number, kolLabel: string) => void;
}) {
  const [showReview, setShowReview] = useState(false);
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
            title="Mark as closed in console; does not stop the agent run"
          >
            Mark closed
          </button>
        )}
        {c.shortlist_ready && !c.shortlist_approved && (
          <button
            onClick={() => setShowReview((v) => !v)}
            className="rounded border border-emerald-300 bg-emerald-50 px-2 py-0.5 text-xs text-emerald-800 hover:bg-emerald-100"
            title="Review the agent's shortlist with scores + reasons, then approve a subset"
          >
            {showReview ? '× Close review' : '✓ Review shortlist'}
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
      <StageProgressBar stage={c.stage} />
      {c.event_count === 0 && c.run_state && c.run_state !== 'running' && c.run_state !== 'queued' && c.run_state !== 'waiting_for_approval' && (
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
          <div>{c.event_count}</div>
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
          onSimulateReply={(id, label) => onSimulateReply(c.campaign_id, c.env, id, label)}
          emptyText="暂无已建联 KOL — 审批 shortlist 后将生成初邀草稿"
        />
      </div>
    </li>
  );
}

const DEFAULT_FORM = {
  campaign_id: '',
  budget_per_kol: '1500',
  absolute_floor: '2200',
  budget_total: '12000',
  headcount_target: '8',
  test_mode_to: 'tester@example.com',
  env: 'TEST',
  brief_extra: '',
};

const MAX_BRIEF_CHARS = 16_000;
const BRIEF_ACCEPT = '.txt,.md,text/plain,text/markdown';

export function ProductDetailPage() {
  const { sku } = useParams<{ sku: string }>();
  const [p, setP] = useState<Product | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [campaigns, setCampaigns] = useState<CampaignsPayload>({ campaigns: [], kols: {} });
  const [envFilter, setEnvFilter] = useState<'TEST' | 'LIVE'>('TEST');
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState(DEFAULT_FORM);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refreshCampaigns = () => {
    if (!sku) return;
    api
      .get<CampaignsPayload>(`/products/${encodeURIComponent(sku)}/campaigns?env=${envFilter}`)
      .then(setCampaigns)
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

  const knownIds = useMemo(
    () => new Set(campaigns.campaigns.map((c) => c.campaign_id)),
    [campaigns.campaigns],
  );
  const hasRunning = campaigns.campaigns.some((c) => c.status === 'running');
  const dupClient = form.campaign_id.trim() !== '' && knownIds.has(form.campaign_id.trim());

  const start = async (e: FormEvent) => {
    e.preventDefault();
    setMsg(null);
    setErr(null);
    if (!sku) return;
    const cid = form.campaign_id.trim();
    if (!cid) return;
    if (dupClient) {
      setErr(
        `campaign_id "${cid}" already exists for this SKU in ${form.env}. ` +
          'Choose a different id, or use Mark closed first.',
      );
      return;
    }
    if (Number(form.budget_per_kol) >= Number(form.absolute_floor)) {
      setErr('budget_per_kol must be < absolute_floor (refusal-zone rule).');
      return;
    }
    setBusy(true);
    try {
      const briefExtra = form.brief_extra.trim();
      const r = await api.post<{ run_id?: string }>(
        `/campaigns/${encodeURIComponent(cid)}/start`,
        {
          product_sku: sku,
          budget_per_kol: Number(form.budget_per_kol),
          absolute_floor: Number(form.absolute_floor),
          budget_total: Number(form.budget_total),
          headcount_target: Number(form.headcount_target),
          test_mode_to: form.test_mode_to,
          env: form.env,
          brief_extra: briefExtra ? briefExtra : null,
        },
      );
      setMsg(`Started: run_id=${r.run_id ?? '(none)'}`);
      setShowForm(false);
      setForm({ ...DEFAULT_FORM, env: form.env });
      refreshCampaigns();
    } catch (ex) {
      if (ex instanceof ApiError && ex.status === 409) {
        setErr(`Server rejected: ${ex.body}`);
      } else {
        setErr(String(ex));
      }
    } finally {
      setBusy(false);
    }
  };

  const close = async (cid: string, env: string) => {
    try {
      await api.post(`/campaigns/${encodeURIComponent(cid)}/close?env=${env}`, { status: 'closed' });
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

  const simulateReply = async (
    cid: string,
    env: string,
    kolId: number,
    kolLabel: string,
  ) => {
    setMsg(null);
    setErr(null);
    const replyBody = window.prompt(
      `Simulate inbound reply from "${kolLabel}":\n\nPaste the reply text below.`,
      "Hi! Thanks for reaching out — I'm interested. What fee are you offering?",
    );
    if (!replyBody) return;
    const hint = window.prompt(
      'Optional intent hint (interested / asking_fee / decline / out_of_office / spam / unknown):',
      'asking_fee',
    );
    try {
      const r = await api.post<{ run_id?: string }>(
        `/campaigns/${encodeURIComponent(cid)}/replies/inbound`,
        {
          kol_identity_id: kolId,
          body: replyBody,
          intent_hint: hint || null,
          env,
        },
      );
      setMsg(`Reply injected for ${kolLabel} · classification run ${r.run_id ?? '(none)'}`);
      refreshCampaigns();
    } catch (ex) {
      setErr(`Inject reply failed: ${String(ex)}`);
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
          <button
            onClick={() => setShowForm((v) => !v)}
            className="ml-auto rounded bg-emerald-600 px-3 py-1 text-sm text-white"
          >
            {showForm ? 'Cancel' : '+ New campaign'}
          </button>
        </div>

        {hasRunning && !showForm && (
          <div className="rounded border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
            A campaign is currently running for this SKU. Starting another one will be rejected
            unless you mark the current one closed.
          </div>
        )}

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
                onSimulateReply={simulateReply}
              />
            ))}
          </ul>
        )}
      </section>

      {showForm && (
        <section className="rounded border bg-white p-3">
          <h2 className="mb-2 font-medium">Start a new campaign</h2>
          <form onSubmit={start} className="grid grid-cols-2 gap-3 text-sm">
            <label>
              campaign_id
              <input
                required
                value={form.campaign_id}
                onChange={(e) => setForm({ ...form, campaign_id: e.target.value })}
                className={
                  'mt-1 block w-full rounded border px-2 py-1 ' +
                  (dupClient ? 'border-red-500' : '')
                }
              />
              {dupClient && (
                <span className="text-xs text-red-600">already exists in {form.env}</span>
              )}
            </label>
            <label>
              env
              <select
                value={form.env}
                onChange={(e) => setForm({ ...form, env: e.target.value })}
                className="mt-1 block w-full rounded border px-2 py-1"
              >
                <option value="TEST">TEST</option>
                <option value="LIVE">LIVE</option>
              </select>
            </label>
            {(
              [
                ['budget_per_kol', 'Budget per KOL (USD)'],
                ['absolute_floor', 'Absolute floor (USD) — must be > per-KOL'],
                ['budget_total', 'Total budget (USD)'],
                ['headcount_target', 'Headcount target'],
                ['test_mode_to', 'TEST inbox'],
              ] as const
            ).map(([k, label]) => (
              <label key={k}>
                {label}
                <input
                  value={form[k as keyof typeof form]}
                  onChange={(e) => setForm({ ...form, [k]: e.target.value })}
                  className="mt-1 block w-full rounded border px-2 py-1"
                />
              </label>
            ))}
            <div className="col-span-2 space-y-1">
              <div className="flex items-center justify-between">
                <label htmlFor="brief_extra" className="font-medium">
                  Product brief (optional)
                </label>
                <div className="flex items-center gap-2 text-xs text-slate-500">
                  <span>
                    {form.brief_extra.length.toLocaleString()} / {MAX_BRIEF_CHARS.toLocaleString()} chars
                  </span>
                  <label className="cursor-pointer rounded border border-slate-300 px-2 py-0.5 hover:bg-slate-50">
                    Upload .txt / .md
                    <input
                      type="file"
                      accept={BRIEF_ACCEPT}
                      className="hidden"
                      onChange={async (e) => {
                        const file = e.target.files?.[0];
                        // Reset so re-selecting the same file fires onChange.
                        e.target.value = '';
                        if (!file) return;
                        const lower = file.name.toLowerCase();
                        if (!lower.endsWith('.txt') && !lower.endsWith('.md')) {
                          setErr('Only .txt or .md files are accepted.');
                          return;
                        }
                        if (file.size > MAX_BRIEF_CHARS * 2) {
                          setErr(`File too large (> ${MAX_BRIEF_CHARS} chars).`);
                          return;
                        }
                        try {
                          const text = await file.text();
                          if (text.length > MAX_BRIEF_CHARS) {
                            setErr(
                              `File content exceeds ${MAX_BRIEF_CHARS} chars (got ${text.length}).`,
                            );
                            return;
                          }
                          setErr(null);
                          setForm((prev) => ({ ...prev, brief_extra: text }));
                        } catch (ex) {
                          setErr(`Failed to read file: ${String(ex)}`);
                        }
                      }}
                    />
                  </label>
                  {form.brief_extra && (
                    <button
                      type="button"
                      onClick={() => setForm({ ...form, brief_extra: '' })}
                      className="rounded border border-slate-300 px-2 py-0.5 hover:bg-slate-50"
                    >
                      Clear
                    </button>
                  )}
                </div>
              </div>
              <textarea
                id="brief_extra"
                value={form.brief_extra}
                onChange={(e) =>
                  setForm({
                    ...form,
                    brief_extra: e.target.value.slice(0, MAX_BRIEF_CHARS),
                  })
                }
                placeholder="Paste a product brief, key selling points, audience notes, etc. Markdown supported."
                rows={8}
                className="block w-full rounded border px-2 py-1 font-mono text-xs"
              />
              <p className="text-xs text-slate-500">
                Appended to the auto-generated brief sent to the orchestrator. Leave blank to use
                only the SKU catalog data.
              </p>
            </div>
            <button
              disabled={busy || dupClient}
              className="col-span-2 rounded bg-emerald-600 px-3 py-1 text-white disabled:bg-slate-300"
            >
              {busy ? 'Starting…' : 'Start'}
            </button>
          </form>
          {msg && <div className="mt-2 text-sm text-emerald-700">{msg}</div>}
        </section>
      )}

      {err && <div className="text-sm text-red-600">{err}</div>}
    </div>
  );
}
