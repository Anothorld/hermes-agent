import { ReactNode, useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { api, ApiError, Lane } from '../api';
import { GoalProgressBar } from '../components/GoalProgressBar';
import { FactsEditor } from '../components/FactsEditor';
import { RepeatKolBadge } from '../components/RepeatKolBadge';
import { FactKeyChip } from '../components/inputs/FactKeyChip';
import { TimeAgo } from '../components/inputs/TimeAgo';
import { ErrorAlert } from '../components/feedback/ErrorAlert';
import { factKeyLabel } from '../components/factKeyLabel';
import { KolArchiveDialog } from '../components/dialogs/KolArchiveDialog';
import { UnreadDot } from '../components/UnreadDot';
import { isRealCampaignId } from '../lib/campaignId';
import { useEnvStore } from '../lib/store';
import { useUnreadStore, isUnread } from '../lib/unread';
import { outcomeChipClass, outcomeLabel } from '../lib/kolOutcomes';
import { usePollingFallback } from '../hooks/usePollingFallback';
import { useDataChannel } from '../hooks/useDataChannel';

const GMAIL_DRAFTS_URL = 'https://mail.google.com/mail/u/0/#drafts';

type GoalStatus = 'inactive' | 'active' | 'satisfied' | 'blocked' | 'skipped' | 'aborted';

type GoalEntry = {
  goal: string;
  status: GoalStatus;
  lane: Lane;
  missing_facts: string[];
  blocking_escalation_id?: number | null;
  updated_at?: string;
};

type GoalsResponse = {
  goals: GoalEntry[];
};

type IdentityResponse = {
  id: number;
  primary_handle: string;
  display_name?: string | null;
  primary_email: string | null;
  creator_type?: string | null;
  env: string;
  repeat_count?: number;
  last_outcome?: string | null;
};

type EscalationLite = {
  id: number;
  rule_id: string | null;
  reason: string;
  state: string;
  created_at: string;
};

function statusChip(status: GoalStatus): string {
  switch (status) {
    case 'active': return 'bg-emerald-100 text-emerald-800';
    case 'satisfied': return 'bg-sky-100 text-sky-800';
    case 'blocked': return 'bg-amber-100 text-amber-800';
    case 'skipped': return 'bg-slate-100 text-slate-600';
    case 'aborted': return 'bg-rose-100 text-rose-800';
    default: return 'bg-slate-100 text-slate-500';
  }
}

const STATUS_LABEL: Record<GoalStatus, string> = {
  active: '进行中',
  satisfied: '已完成',
  blocked: '阻塞',
  skipped: '跳过',
  aborted: '终止',
  inactive: '未启动',
};

type LoadStatus = 'ok' | 'pending' | 'error';
type SectionState<T> = { status: LoadStatus; data: T | null; error: unknown | null };

const initialSection = <T,>(): SectionState<T> => ({ status: 'pending', data: null, error: null });

function pickSettled<T>(r: PromiseSettledResult<T>): SectionState<T> {
  if (r.status === 'fulfilled') {
    return { status: 'ok', data: r.value, error: null };
  }
  return { status: 'error', data: null, error: r.reason };
}

export function KolDetailPage() {
  const { id } = useParams();
  const [search] = useSearchParams();
  // ``isRealCampaignId`` filters out the historical "null"/"undefined"
  // sentinels that could arrive via a buggy link upstream (e.g. an
  // identity-scoped escalation row whose JSON-null ``campaign_id`` got
  // ``encodeURIComponent``-ed into the literal string ``"null"``).
  // Treat those exactly like a missing param so the page shows the
  // ``需要 ?campaign_id=<>`` error instead of forwarding the sentinel
  // to every downstream API call.
  const rawCampaignId = search.get('campaign_id');
  const campaignId = isRealCampaignId(rawCampaignId) ? rawCampaignId : '';
  const env = useEnvStore((s) => s.env);
  const identityId = Number(id);
  const [identity, setIdentity] = useState<SectionState<IdentityResponse>>(initialSection);
  const [goals, setGoals] = useState<SectionState<GoalsResponse>>(initialSection);
  const [escalations, setEscalations] = useState<SectionState<EscalationLite[]>>(initialSection);
  const [pendingApprovals, setPendingApprovals] = useState<number>(0);
  const [approvalLatestAt, setApprovalLatestAt] = useState<string | null>(null);
  const [escalationLatestAt, setEscalationLatestAt] = useState<string | null>(null);
  const [facts, setFacts] = useState<SectionState<Record<string, unknown>>>(initialSection);
  const [lastRefreshedAt, setLastRefreshedAt] = useState<number>(0);
  const [archiveOpen, setArchiveOpen] = useState(false);
  const markSeen = useUnreadStore((s) => s.markSeen);
  const seenApproval = useUnreadStore(
    (s) => s.seen[`approvals.kol.${identityId}`],
  );
  const seenEscalation = useUnreadStore(
    (s) => s.seen[`escalations.kol.${identityId}`],
  );

  const refresh = useCallback(async () => {
    if (!identityId || !campaignId) {
      setIdentity({ status: 'error', data: null, error: new Error('需要 identity id 和 ?campaign_id=<>') });
      return;
    }
    const [idR, goalsR, escR, apprR, factsR] = await Promise.allSettled([
      api.get<IdentityResponse>(`/kols/${identityId}?env=${env}`),
      api.get<GoalsResponse>(
        `/identities/${identityId}/goals?campaign_id=${encodeURIComponent(campaignId)}&env=${env}`,
      ),
      api.get<EscalationLite[]>(`/escalations?state=awaiting_answer&env=${env}`),
      api.get<Array<{ identity_id?: number; campaign_id?: string; opened_at?: string | null }>>(
        `/approvals?env=${env}`,
      ),
      api.get<{ facts: Record<string, unknown> }>(
        `/facts/${identityId}?campaign_id=${encodeURIComponent(campaignId)}&env=${env}`,
      ),
    ]);
    setIdentity(pickSettled(idR));
    setGoals(pickSettled(goalsR));
    let escLatest: string | null = null;
    if (escR.status === 'fulfilled') {
      const mine = (escR.value || []).filter(
        (e) => (e as unknown as { identity_id?: number }).identity_id === identityId,
      );
      setEscalations({ status: 'ok', data: mine, error: null });
      for (const e of mine) {
        if (e.created_at && (!escLatest || e.created_at > escLatest)) {
          escLatest = e.created_at;
        }
      }
    } else {
      setEscalations({ status: 'error', data: null, error: escR.reason });
    }
    setEscalationLatestAt(escLatest);

    let apprLatest: string | null = null;
    let apprCount = 0;
    if (apprR.status === 'fulfilled') {
      for (const a of apprR.value || []) {
        if (a.identity_id !== identityId || a.campaign_id !== campaignId) continue;
        apprCount += 1;
        if (a.opened_at && (!apprLatest || a.opened_at > apprLatest)) {
          apprLatest = a.opened_at;
        }
      }
    }
    setPendingApprovals(apprCount);
    setApprovalLatestAt(apprLatest);

    setFacts(
      factsR.status === 'fulfilled'
        ? { status: 'ok', data: factsR.value?.facts ?? {}, error: null }
        : { status: 'error', data: null, error: factsR.reason },
    );
    setLastRefreshedAt(Date.now());
  }, [identityId, campaignId, env]);

  // Mark approvals/escalations seen ONCE per KOL visit. Doing it inside
  // refresh() would mark the latest fetched timestamps as seen on every
  // 20s poll, so a fresh approval landing while the operator stares at
  // this page would never produce a dot. Marking on mount instead lets
  // subsequent refreshes legitimately re-fire the dot for new items;
  // navigating away and back resets the baseline.
  useEffect(() => {
    if (!identityId) return;
    const nowMs = Date.now();
    markSeen(`approvals.kol.${identityId}`, nowMs);
    markSeen(`escalations.kol.${identityId}`, nowMs);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [identityId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useDataChannel({ onMatch: refresh, identityId });
  usePollingFallback(refresh, 20_000);

  const lanes: Lane[] = ['commerce', 'fulfillment', 'publish', 'meta'];
  const goalsByLane = useMemo(() => {
    const out: Record<Lane, GoalEntry[]> = {
      commerce: [], fulfillment: [], publish: [], meta: [],
    };
    for (const g of goals.data?.goals ?? []) {
      if (g.lane in out) out[g.lane].push(g);
    }
    return out;
  }, [goals]);

  if (!campaignId || !identityId) {
    // Distinguish "no campaign_id passed" from "campaign_id passed
    // but it's a sentinel value (?campaign_id=null)" so the operator
    // knows the link they followed is broken vs. a missing route.
    const sentinelInUrl =
      rawCampaignId !== null && rawCampaignId !== '' && !isRealCampaignId(rawCampaignId);
    const msg = sentinelInUrl
      ? `链接里的 campaign_id="${rawCampaignId}" 不是合法值（来自上游一个 null/undefined 链接）。请从 Kanban 或 Candidates 页选这个 KOL 进入。`
      : '需要 identity id 和 ?campaign_id=<>';
    return <ErrorAlert error={new Error(msg)} />;
  }
  if (identity.status === 'pending' || goals.status === 'pending') {
    return <div className="text-sm text-slate-500">加载中…</div>;
  }
  if (identity.status === 'error' && !identity.data) {
    return <ErrorAlert error={identity.error} onRetry={refresh} />;
  }
  if (!identity.data || !goals.data) {
    return <div className="text-sm text-slate-500">加载中…</div>;
  }

  const identityVal = identity.data;
  const goalsVal = goals.data;
  const escalationsList = escalations.data ?? [];
  const factsVal = facts.data ?? {};

  const commerceActive = goalsByLane.commerce.find(
    (g) => g.status === 'active' || g.status === 'blocked',
  );
  const commerceCompleted = goalsByLane.commerce
    .filter((g) => g.status === 'satisfied')
    .map((g) => g.goal);
  const allMissing = goalsVal.goals.flatMap((g) => g.missing_facts ?? []);
  const displayHandle = identityVal.primary_handle || identityVal.display_name || `kol#${identityVal.id}`;

  const partialErrors: string[] = [];
  if (escalations.status === 'error' && escalations.error) partialErrors.push('escalations');
  if (facts.status === 'error' && facts.error) partialErrors.push('facts');

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-lg font-semibold">
          @{displayHandle}
          <RepeatKolBadge
            count={identityVal.repeat_count || 0}
            lastOutcome={identityVal.last_outcome ?? null}
          />
        </h1>
        <div className="text-xs text-slate-500">
          {identityVal.primary_email || <span className="italic">无邮箱</span>} · {identityVal.env}
        </div>
        {lastRefreshedAt > 0 && (
          <TimeAgo
            iso={lastRefreshedAt}
            prefix="刷新于"
            className="text-[10px] text-slate-400"
          />
        )}
        {identityVal.last_outcome && (
          <span
            className={`rounded border px-1.5 py-0.5 text-[11px] ${outcomeChipClass(identityVal.last_outcome)}`}
            title={`上次归档结果：${identityVal.last_outcome}`}
          >
            {outcomeLabel(identityVal.last_outcome)}
          </span>
        )}
        <div className="ml-auto flex items-center gap-3">
          <button
            type="button"
            onClick={() => setArchiveOpen(true)}
            className="rounded border border-slate-300 bg-white px-2 py-0.5 text-xs text-slate-700 hover:border-rose-300 hover:bg-rose-50 hover:text-rose-700"
            title="归档此 KOL（含『竞品-不合作』等原因）"
          >
            归档此 KOL
          </button>
          <Link
            to={`/kols/${identityVal.id}/relationship`}
            className="text-xs text-sky-700 hover:underline"
          >
            历史 & 复用事实 →
          </Link>
        </div>
      </div>

      <KolArchiveDialog
        open={archiveOpen}
        identityId={identityVal.id}
        campaignId={campaignId}
        displayName={identityVal.primary_handle || identityVal.display_name || `kol#${identityVal.id}`}
        env={(env as 'TEST' | 'LIVE')}
        onClose={() => setArchiveOpen(false)}
        onArchived={() => refresh()}
      />

      {partialErrors.length > 0 && (
        <div className="rounded border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900">
          <div className="font-medium">部分子接口加载失败，已展示能拿到的数据：</div>
          <ul className="mt-1 list-disc pl-4">
            {partialErrors.map((name) => (
              <li key={name}>
                <span className="font-mono">{name}</span> 接口暂时不可用
              </li>
            ))}
          </ul>
        </div>
      )}

      <SocialLinksBar facts={factsVal} />

      <OutreachTimelinePanel facts={factsVal} />

      <EmailPanel
        identityId={identityVal.id}
        handle={identityVal.primary_handle}
        primaryEmail={identityVal.primary_email}
        campaignId={campaignId}
        env={env}
        onChanged={refresh}
      />

      <SocialLinksPanel
        identityId={identityVal.id}
        facts={factsVal}
        campaignId={campaignId}
        env={env}
        onChanged={refresh}
      />

      <RedraftPanel
        campaignId={campaignId}
        identityId={identityVal.id}
        env={env}
        facts={factsVal}
        primaryEmail={identityVal.primary_email}
        onTriggered={refresh}
      />

      <div className="flex flex-wrap gap-2 text-xs">
        <Link
          to={`/approvals?campaign_id=${encodeURIComponent(campaignId)}&identity_id=${identityVal.id}&env=${env}`}
          className={`rounded px-2 py-1 ${
            pendingApprovals > 0
              ? 'bg-rose-100 text-rose-800 hover:bg-rose-200'
              : 'bg-slate-100 text-slate-600'
          }`}
        >
          待审批：{pendingApprovals}
          <UnreadDot
            show={isUnread(approvalLatestAt, seenApproval)}
            title="有新的待审批"
          />
        </Link>
        <Link
          to={`/escalations?campaign_id=${encodeURIComponent(campaignId)}&identity_id=${identityVal.id}&env=${env}`}
          className={`rounded px-2 py-1 ${
            escalationsList.length > 0
              ? 'bg-amber-100 text-amber-800 hover:bg-amber-200'
              : 'bg-slate-100 text-slate-600'
          }`}
        >
          升级中：{escalationsList.length}
          <UnreadDot
            show={isUnread(escalationLatestAt, seenEscalation)}
            title="有新的升级"
          />
        </Link>
      </div>

      <GoalProgressBar
        active={commerceActive?.goal ?? null}
        completed={commerceCompleted}
        blocked={commerceActive?.status === 'blocked'}
      />

      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-4">
        {lanes.map((lane) => {
          const entries = goalsByLane[lane];
          const visible = entries.filter((g) => g.status !== 'inactive');
          return (
            <div
              key={lane}
              className="rounded border border-slate-200 bg-white p-3 text-sm"
            >
              <div className="mb-1 text-xs uppercase tracking-wide text-slate-500">
                {lane}
              </div>
              {visible.length === 0 ? (
                <div className="text-xs italic text-slate-400">空闲</div>
              ) : (
                <ul className="space-y-2">
                  {visible.map((g) => (
                    <li key={g.goal}>
                      <div className="flex items-center gap-2">
                        <span className="font-medium">{g.goal}</span>
                        <span className={`rounded px-1.5 py-0.5 text-[10px] ${statusChip(g.status)}`}>
                          {STATUS_LABEL[g.status]}
                        </span>
                      </div>
                      {g.blocking_escalation_id && (
                        <div className="mt-1 rounded bg-amber-100 px-2 py-1 text-xs text-amber-900">
                          被升级 #{g.blocking_escalation_id} 阻塞
                        </div>
                      )}
                      {!!g.missing_facts?.length && (
                        <div className="mt-1 flex flex-wrap gap-1">
                          {g.missing_facts.map((f) => (
                            <FactKeyChip key={f} factKey={f} variant="missing" prefix="缺：" />
                          ))}
                        </div>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          );
        })}
      </div>

      {!!escalationsList.length && (
        <div className="rounded border border-amber-300 bg-amber-50 p-3">
          <div className="mb-1 text-xs font-medium uppercase tracking-wide text-amber-800">
            未解决的升级 ({escalationsList.length})
          </div>
          <ul className="space-y-1 text-sm">
            {escalationsList.map((e) => (
              <li key={e.id}>
                <Link
                  to={`/escalations/${e.id}`}
                  className="text-amber-900 underline-offset-2 hover:underline"
                >
                  #{e.id} · {e.rule_id || 'manual'} · {e.reason}
                </Link>
              </li>
            ))}
          </ul>
        </div>
      )}

      <ConfirmedFactsPanel facts={factsVal} />

      <FactsEditor
        identityId={identityId}
        campaignId={campaignId}
        env={env}
        factKeys={Array.from(new Set(allMissing))}
        onSubmitted={refresh}
      />
    </div>
  );
}

// Top-of-page outreach summary — pulls the few timestamps that answer
// "have we sent? did they reply?" without forcing the operator to dig
// through the Confirmed Facts panel below.
function OutreachTimelinePanel({ facts }: { facts: Record<string, unknown> }) {
  const outreachSent = facts['offer.outreach_sent'];
  const outreachSentAt = facts['offer.outreach_sent_at'];
  const interestSignal = facts['offer.interest_signal'];
  const contractSent = facts['offer.contract_sent'];
  const contractSigned = facts['offer.contract_signed'];

  type Step = { label: string; status: 'done' | 'waiting' | 'idle' | 'declined'; at?: string };
  const steps: Step[] = [];

  if (outreachSent) {
    const at = typeof outreachSentAt === 'string' ? outreachSentAt : undefined;
    steps.push({ label: '初邀发出', status: 'done', at });
  } else {
    steps.push({ label: '初邀发出', status: 'idle' });
  }

  const sig = typeof interestSignal === 'string' ? interestSignal.toLowerCase() : '';
  if (sig === 'confirmed' || sig === 'interested') {
    steps.push({ label: '对方意向', status: 'done' });
  } else if (sig === 'declined') {
    steps.push({ label: '对方意向', status: 'declined' });
  } else if (outreachSent) {
    steps.push({ label: '对方意向', status: 'waiting' });
  } else {
    steps.push({ label: '对方意向', status: 'idle' });
  }

  if (contractSent) {
    steps.push({
      label: '合同',
      status: contractSigned ? 'done' : 'waiting',
    });
  }

  return (
    <div className="rounded border border-slate-200 bg-white p-3">
      <div className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-500">
        触达进度
      </div>
      <ol className="flex flex-wrap items-center gap-2 text-xs">
        {steps.map((s, i) => (
          <li key={i} className="flex items-center gap-1">
            <span
              className={
                'rounded px-2 py-1 ' +
                (s.status === 'done'
                  ? 'bg-emerald-100 text-emerald-800'
                  : s.status === 'waiting'
                  ? 'bg-slate-100 text-slate-700 ring-1 ring-slate-200'
                  : s.status === 'declined'
                  ? 'bg-rose-100 text-rose-800'
                  : 'bg-slate-50 text-slate-400')
              }
            >
              {s.label}
              {s.status === 'waiting' && ' · 等待'}
              {s.status === 'declined' && ' · 拒'}
            </span>
            {s.at && <TimeAgo iso={s.at} className="text-[10px] text-slate-500" />}
            {i < steps.length - 1 && <span className="text-slate-300">→</span>}
          </li>
        ))}
      </ol>
    </div>
  );
}

const NS_PANEL_COLOR: Record<string, string> = {
  identity: 'border-sky-200 bg-sky-50',
  offer_us: 'border-slate-200 bg-slate-50',
  offer_them: 'border-emerald-200 bg-emerald-50',
  fulfillment: 'border-amber-200 bg-amber-50',
  approval: 'border-rose-200 bg-rose-50',
  other: 'border-slate-200 bg-slate-50',
};

const NS_PANEL_TITLE: Record<string, string> = {
  identity: '身份 (KOL 档案)',
  offer_us: '合作 · 我方动作',
  offer_them: '合作 · 对方反馈',
  fulfillment: '物流 / 交付',
  approval: '审批',
  other: '其他',
};

const NS_PANEL_ORDER: Array<'identity' | 'offer_us' | 'offer_them' | 'fulfillment' | 'approval' | 'other'> = [
  'identity', 'offer_us', 'offer_them', 'fulfillment', 'approval', 'other',
];

const OFFER_THEM_KEYS: ReadonlySet<string> = new Set<string>([
  'offer.interest_signal',
  'offer.kol_paid_quote',
  'offer.agreed_terms',
  'offer.fit_confirmed',
  'offer.contract_signed',
  'offer.contract_declined_reason',
  'offer.draft_submitted',
  'offer.posted_url',
]);

function ConfirmedFactsPanel({ facts }: { facts: Record<string, unknown> }) {
  const groups = useMemo(() => {
    const out: Record<string, Array<[string, unknown]>> = {
      identity: [], offer_us: [], offer_them: [], fulfillment: [], approval: [], other: [],
    };
    for (const [k, v] of Object.entries(facts)) {
      const ns = k.split('.', 1)[0];
      if (ns === 'offer') {
        if (OFFER_THEM_KEYS.has(k)) out.offer_them.push([k, v]);
        else out.offer_us.push([k, v]);
        continue;
      }
      (out[ns] ?? out.other).push([k, v]);
    }
    for (const arr of Object.values(out)) arr.sort((a, b) => a[0].localeCompare(b[0]));
    return out;
  }, [facts]);

  const total = Object.values(groups).reduce((n, arr) => n + arr.length, 0);
  if (total === 0) {
    return (
      <div className="rounded border border-slate-200 bg-white p-3 text-xs text-slate-500">
        本 campaign 还没有任何已确认的事实。
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <div className="text-xs font-medium uppercase tracking-wide text-slate-500">
        已确认事实 ({total})
      </div>
      <div className="grid grid-cols-1 gap-2 lg:grid-cols-2">
        {NS_PANEL_ORDER.map((ns) => {
          const entries = groups[ns];
          if (!entries.length) return null;
          return (
            <details key={ns} open className={`rounded border p-2 ${NS_PANEL_COLOR[ns]}`}>
              <summary className="cursor-pointer text-xs font-semibold uppercase tracking-wide">
                {NS_PANEL_TITLE[ns]} ({entries.length})
              </summary>
              <ul className="mt-2 space-y-1.5">
                {entries.map(([k, v]) => (
                  <li key={k} className="rounded bg-white/60 p-1.5">
                    <FactKeyChip factKey={k} variant="filled" />
                    <div className="mt-0.5 break-all text-xs leading-snug text-slate-900">
                      {renderFactValue(k, v)}
                    </div>
                  </li>
                ))}
              </ul>
            </details>
          );
        })}
      </div>
    </div>
  );
}

function renderFactValue(key: string, v: unknown): ReactNode {
  if (v === null || v === undefined) return <span className="italic text-slate-400">空</span>;
  const meta = factKeyLabel(key);
  if (meta.kind === 'bool') {
    return v ? <span className="text-emerald-700">✓ 是</span> : <span className="text-slate-500">— 否</span>;
  }
  if (meta.kind === 'datetime' && typeof v === 'string') {
    return <TimeAgo iso={v} />;
  }
  if (meta.kind === 'enum' && typeof v === 'string') {
    const opt = meta.enumOptions?.find((o) => o.value === v);
    return opt ? opt.label : v;
  }
  if (meta.kind === 'url' && typeof v === 'string') {
    return (
      <a href={v} target="_blank" rel="noreferrer" className="text-sky-700 hover:underline">
        {v}
      </a>
    );
  }
  if (typeof v === 'string') return v;
  if (typeof v === 'number' || typeof v === 'boolean') return String(v);
  try {
    return <span className="font-mono text-[11px]">{JSON.stringify(v)}</span>;
  } catch {
    return String(v);
  }
}

// One-click "rebuild the Gmail draft for this KOL" panel. Visible
// whenever the initial outreach hasn't been sent yet. The button
// delegates to the backend's redraft-outreach endpoint which enforces
// per-identity TTL dedup AND campaign-level run-in-flight locking.
//
// Confirm dialog: if an approved draft is already in Gmail but not
// yet sent-reconciled, redrafting orphans it. We gate that with an
// explicit ack instead of failing silently.
//
// Cooldown window: 120s. kol-cold-outreach single-identity typically
// completes in 30–60s; 120s is a comfortable upper bound that's still
// well under the backend's 300s INFLIGHT_TTL so a stalled run can be
// retried from the same UI without a page reload.
type RedraftPhase =
  | { kind: 'idle' }
  | { kind: 'submitting' }
  | {
      kind: 'running';
      runId: string | null;
      startedAt: number;
      cooldownUntil: number;
    }
  | { kind: 'confirm_discard'; previousDraftId: string | null }
  | { kind: 'error'; code: string | null; message: string; payload?: Record<string, unknown> };

const REDRAFT_INFLIGHT_DISPLAY_MS = 120_000;

function redraftCooldownStorageKey(campaignId: string, identityId: number, env: string) {
  return `koc.redraft_cooldown:${env}:${campaignId}:${identityId}`;
}

function readRedraftCooldown(
  campaignId: string,
  identityId: number,
  env: string,
): { runId: string | null; startedAt: number; cooldownUntil: number } | null {
  try {
    const key = redraftCooldownStorageKey(campaignId, identityId, env);
    const raw = sessionStorage.getItem(key);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as {
      runId?: string | null;
      startedAt?: number;
      cooldownUntil?: number;
    };
    if (
      typeof parsed.cooldownUntil !== 'number'
      || parsed.cooldownUntil <= Date.now()
    ) {
      sessionStorage.removeItem(key);
      return null;
    }
    return {
      runId: parsed.runId ?? null,
      startedAt: parsed.startedAt ?? Date.now(),
      cooldownUntil: parsed.cooldownUntil,
    };
  } catch {
    return null;
  }
}

function writeRedraftCooldown(
  campaignId: string,
  identityId: number,
  env: string,
  payload: { runId: string | null; startedAt: number; cooldownUntil: number },
) {
  try {
    sessionStorage.setItem(
      redraftCooldownStorageKey(campaignId, identityId, env),
      JSON.stringify(payload),
    );
  } catch {
    // best-effort
  }
}

function clearRedraftCooldown(campaignId: string, identityId: number, env: string) {
  try {
    sessionStorage.removeItem(redraftCooldownStorageKey(campaignId, identityId, env));
  } catch {
    // ignore
  }
}

type ApiErrorDetail = {
  code?: string;
  message?: string;
  run_id?: string;
  started_at?: string;
  outreach_sent_at?: string;
  gmail_draft_id?: string;
  gmail_thread_id?: string;
};

function parseApiErrorDetail(err: unknown): ApiErrorDetail | null {
  if (!(err instanceof ApiError)) return null;
  try {
    const parsed = JSON.parse(err.body);
    if (parsed && typeof parsed === 'object' && 'detail' in parsed) {
      const detail = (parsed as { detail: unknown }).detail;
      if (detail && typeof detail === 'object') return detail as ApiErrorDetail;
      if (typeof detail === 'string') return { message: detail };
    }
  } catch {
    // Body wasn't JSON.
  }
  return { message: err.body || err.message };
}

function RedraftPanel({
  campaignId,
  identityId,
  env,
  facts,
  primaryEmail,
  onTriggered,
}: {
  campaignId: string;
  identityId: number;
  env: 'TEST' | 'LIVE';
  facts: Record<string, unknown>;
  primaryEmail: string | null;
  onTriggered: () => void;
}) {
  const sent = Boolean(facts['offer.outreach_sent']);
  const draftCreated = Boolean(facts['offer.outreach_draft_created']);
  const hasEmail = Boolean(primaryEmail && primaryEmail.trim());
  const replyDraft = facts['approval.reply_draft'];
  const decision =
    replyDraft && typeof replyDraft === 'object'
      ? (replyDraft as { decision?: string }).decision || null
      : null;
  const gmailDraftId =
    typeof facts['offer.gmail_draft_id'] === 'string'
      ? (facts['offer.gmail_draft_id'] as string)
      : null;

  // On mount: pick up an in-flight redraft from sessionStorage so a
  // page reload mid-skill doesn't reset the button to "clickable"
  // while the backend's gateway run is still working.
  const [phase, setPhase] = useState<RedraftPhase>(() => {
    const cd = readRedraftCooldown(campaignId, identityId, env);
    if (cd) {
      return {
        kind: 'running',
        runId: cd.runId,
        startedAt: cd.startedAt,
        cooldownUntil: cd.cooldownUntil,
      };
    }
    return { kind: 'idle' };
  });
  // Per-second tick so the "已等待 Xs" counter updates without
  // recreating timers on every render.
  const [, forceTick] = useState(0);
  useEffect(() => {
    if (phase.kind !== 'running') return;
    const tick = setInterval(() => forceTick((n) => (n + 1) & 0xffff), 1000);
    return () => clearInterval(tick);
  }, [phase.kind]);

  // When the cooldown expires, drop back to idle so the operator can
  // retry. Backend dedup TTL is 300s; the UI window is shorter to
  // recover from a stalled / failed run without a page reload.
  useEffect(() => {
    if (phase.kind !== 'running') return;
    const remaining = phase.cooldownUntil - Date.now();
    if (remaining <= 0) {
      clearRedraftCooldown(campaignId, identityId, env);
      setPhase({ kind: 'idle' });
      return;
    }
    const t = setTimeout(() => {
      clearRedraftCooldown(campaignId, identityId, env);
      setPhase((p) =>
        p.kind === 'running' && p.startedAt === phase.startedAt
          ? { kind: 'idle' }
          : p,
      );
    }, remaining);
    return () => clearTimeout(t);
  }, [phase, campaignId, identityId, env]);

  // Outreach actually sent → clear any leftover cooldown so the next
  // unrelated state (e.g., the operator wipes outreach_sent and tries
  // again) starts fresh. The early-return below handles the visual.
  useEffect(() => {
    if (sent) {
      clearRedraftCooldown(campaignId, identityId, env);
    }
  }, [sent, campaignId, identityId, env]);

  const signal = typeof facts['offer.interest_signal'] === 'string'
    ? (facts['offer.interest_signal'] as string).toLowerCase()
    : '';
  if (sent) return null;
  if (signal === 'declined') return null;

  const submit = async (discardApprovedDraft: boolean) => {
    setPhase({ kind: 'submitting' });
    try {
      const r = await api.post<{ run_id: string | null; started_at: string }>(
        `/campaigns/${encodeURIComponent(campaignId)}/identities/${identityId}/redraft-outreach`,
        { env, discard_existing_approved_draft: discardApprovedDraft },
      );
      const startedAt = Date.now();
      const cooldownUntil = startedAt + REDRAFT_INFLIGHT_DISPLAY_MS;
      writeRedraftCooldown(campaignId, identityId, env, {
        runId: r.run_id,
        startedAt,
        cooldownUntil,
      });
      setPhase({ kind: 'running', runId: r.run_id, startedAt, cooldownUntil });
      onTriggered();
    } catch (ex) {
      const detail = parseApiErrorDetail(ex);
      const code = detail?.code ?? null;
      // Backend says a previous redraft is still in flight — sync our
      // local UI to that run's started_at so the "已等待 Xs" counter
      // is accurate even across tabs / reloads.
      if (code === 'redraft_inflight') {
        const startedAt = detail?.started_at
          ? Date.parse(detail.started_at)
          : Date.now();
        const safeStart = Number.isNaN(startedAt) ? Date.now() : startedAt;
        const cooldownUntil = safeStart + REDRAFT_INFLIGHT_DISPLAY_MS;
        writeRedraftCooldown(campaignId, identityId, env, {
          runId: detail?.run_id ?? null,
          startedAt: safeStart,
          cooldownUntil,
        });
        setPhase({
          kind: 'running',
          runId: detail?.run_id ?? null,
          startedAt: safeStart,
          cooldownUntil,
        });
        return;
      }
      if (code === 'approved_draft_exists') {
        setPhase({
          kind: 'confirm_discard',
          previousDraftId: detail?.gmail_draft_id ?? null,
        });
        return;
      }
      setPhase({
        kind: 'error',
        code,
        message: detail?.message ?? String(ex),
        payload: detail as Record<string, unknown> | undefined,
      });
    }
  };

  const ctaLabel = draftCreated ? '重新生成草稿' : '生成 Gmail 草稿';
  const ctaHint = draftCreated
    ? '会写一份新的待审批草稿覆盖当前草稿；老的 Gmail 草稿如果已经发了就保留在 Gmail SENT 里，否则会被本次新草稿替换'
    : 'kol-cold-outreach 会重新跑一次，把新草稿放进你 Gmail 草稿箱';

  let stageHint: ReactNode = null;
  if (decision === 'approved' && !sent) {
    stageHint = (
      <p className="text-xs text-slate-600">
        Gmail 草稿已审批，正在等你去 <a href={GMAIL_DRAFTS_URL} target="_blank"
          rel="noreferrer noopener" className="text-amber-700 underline-offset-2 hover:underline">
          Gmail 草稿箱
        </a> 点 Send。
        {' '}sent-reconcile 每 5 分钟扫一次 SENT 标签，发出后自动更新状态。
      </p>
    );
  } else if (draftCreated) {
    stageHint = (
      <p className="text-xs text-slate-600">
        草稿已生成，但状态仍是 <span className="font-mono">pending</span>，需要先去
        {' '}<Link to="/approvals" className="text-sky-700 underline-offset-2 hover:underline">
          待审批
        </Link>
        {' '}审批，审批通过后才会在 Gmail 草稿箱里出现 Gmail 草稿。
      </p>
    );
  } else {
    stageHint = (
      <p className="text-xs text-slate-600">
        候选已经被选中进入 outreach，但 kol-cold-outreach 还没产出草稿。点下面的按钮重新跑一次，30–60s 后新草稿会出现在审批队列。
      </p>
    );
  }

  return (
    <div className="rounded border border-slate-200 bg-white p-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <div className="text-xs font-medium uppercase tracking-wide text-slate-500">
          下一步动作
        </div>
        {gmailDraftId && (
          <span className="text-[10px] text-slate-400" title={`offer.gmail_draft_id = ${gmailDraftId}`}>
            draft={gmailDraftId.slice(0, 12)}…
          </span>
        )}
      </div>

      {stageHint}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        {(() => {
          const isSubmitting = phase.kind === 'submitting';
          const isRunning = phase.kind === 'running';
          const isBusy = isSubmitting || isRunning;
          // A pending draft already in the approval queue means there's
          // nothing operationally useful to gain by redrafting first —
          // approving (or rejecting) the existing draft moves the
          // pipeline forward, and an accidental redraft just churns
          // through skill cost + makes the operator review the same
          // KOL twice. Hard-block the action and steer to Approvals.
          const isPendingApproval = decision === 'pending';
          const elapsedSec = isRunning
            ? Math.max(0, Math.floor((Date.now() - phase.startedAt) / 1000))
            : 0;
          const remainSec = isRunning
            ? Math.max(0, Math.ceil((phase.cooldownUntil - Date.now()) / 1000))
            : 0;
          const label = isSubmitting
            ? '派单中…'
            : isRunning
            ? `生成中… 已等待 ${elapsedSec}s`
            : ctaLabel;
          const title = !hasEmail
            ? '需要先填入 KOL 邮箱才能生成 outreach 草稿（在上方的"邮箱缺失"面板中搜索或手动填入）'
            : isPendingApproval
            ? '当前已有一份草稿在审批队列里待审，先去 Approvals 通过或拒绝它，再决定是否要重新生成'
            : isRunning
            ? (
              `Gmail 草稿生成 agent 正在跑，run_id=${phase.runId ?? '?'}。`
              + ` skill 预计 30–60s，本地最长锁定 ${Math.ceil(REDRAFT_INFLIGHT_DISPLAY_MS / 1000)}s（约 ${remainSec}s 后超时可重试）。`
              + ` 即使你现在再点也不会真发起第二次 — 后端 dedup TTL 300s 兜底。`
            )
            : ctaHint;
          const disabled = isBusy || !hasEmail || isPendingApproval;
          return (
            <button
              type="button"
              onClick={() => submit(false)}
              disabled={disabled}
              title={title}
              className={
                'flex items-center gap-1.5 rounded px-3 py-1.5 text-sm font-medium transition '
                + (!hasEmail || isPendingApproval
                  ? 'cursor-not-allowed bg-slate-200 text-slate-500'
                  : isBusy
                  ? 'cursor-not-allowed bg-emerald-200 text-emerald-800'
                  : 'bg-emerald-600 text-white hover:bg-emerald-700')
              }
            >
              {isRunning && (
                <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-emerald-600 border-r-transparent" />
              )}
              <span>{label}</span>
            </button>
          );
        })()}

        {!hasEmail && (
          <span className="text-xs text-rose-600">
            ⓘ 没有 KOL 邮箱，cold-outreach 拒绝起草。先在上方面板填入或搜索。
          </span>
        )}

        {hasEmail && decision === 'pending' && (
          <Link
            to={`/approvals?campaign_id=${encodeURIComponent(campaignId)}&identity_id=${identityId}&env=${env}`}
            className="inline-flex items-center gap-1 rounded border border-sky-300 bg-sky-50 px-3 py-1.5 text-sm font-medium text-sky-800 hover:bg-sky-100"
            title="跳到 Approvals 审批已有草稿"
          >
            去审批待处理草稿 →
          </Link>
        )}

        {decision === 'approved' && !sent && (
          <a
            href={GMAIL_DRAFTS_URL}
            target="_blank"
            rel="noreferrer noopener"
            className="rounded border border-amber-300 bg-amber-50 px-3 py-1.5 text-sm font-medium text-amber-800 hover:bg-amber-100"
          >
            打开 Gmail 草稿箱 →
          </a>
        )}

        {phase.kind === 'running' && (
          <div
            className="rounded bg-emerald-50 px-2 py-1 text-xs text-emerald-800"
            role="status"
          >
            ✓ 已请求生成新草稿（run_id={phase.runId ? phase.runId.slice(0, 8) : '?'}…），
            {' '}写回 CAL 后审批队列会自动出现新条目；如果 {Math.ceil(REDRAFT_INFLIGHT_DISPLAY_MS / 1000)}s 还没结果按钮会重新可点。
          </div>
        )}
      </div>

      {hasEmail && decision === 'pending' && phase.kind !== 'running' && phase.kind !== 'submitting' && (
        <p className="mt-2 text-xs text-slate-600">
          ⓘ 已经有一份草稿待审批，建议先去
          {' '}<Link
            to={`/approvals?campaign_id=${encodeURIComponent(campaignId)}&identity_id=${identityId}&env=${env}`}
            className="text-sky-700 underline-offset-2 hover:underline"
          >
            Approvals
          </Link>
          {' '}通过 / 拒绝。审批通过后会自动进 Gmail 草稿箱；拒绝后这个按钮会重新可点。
        </p>
      )}

      {phase.kind === 'confirm_discard' && (
        <div className="mt-3 rounded border border-amber-300 bg-amber-50 p-3 text-xs text-amber-900">
          <div className="font-medium">这个 KOL 的草稿已经审批通过了，再生成会发生什么？</div>
          <ul className="mt-1 list-disc pl-5 space-y-0.5">
            <li>会写一份新的待审批草稿（decision=pending），原来的审批结果会被覆盖。</li>
            <li>
              你 Gmail 草稿箱里现有的那封草稿
              {phase.previousDraftId ? (
                <> (<span className="font-mono">{phase.previousDraftId}</span>)</>
              ) : null}
              {' '}不会自动删除 — 之后再审批新草稿时会再建一封，记得手动清理旧那封免得发重了。
            </li>
            <li>如果旧草稿其实已经发出去了但 SENT 同步还没跑（≤5 分钟），就应该等一下再决定。</li>
          </ul>
          <div className="mt-2 flex gap-2">
            <button
              type="button"
              onClick={() => submit(true)}
              className="rounded bg-rose-600 px-3 py-1 text-xs font-medium text-white hover:bg-rose-700"
            >
              我知道，照样重新生成
            </button>
            <button
              type="button"
              onClick={() => setPhase({ kind: 'idle' })}
              className="rounded border border-slate-300 bg-white px-3 py-1 text-xs text-slate-700 hover:bg-slate-50"
            >
              取消
            </button>
          </div>
        </div>
      )}

      {phase.kind === 'error' && (
        <div className="mt-3 rounded border border-rose-300 bg-rose-50 p-2 text-xs text-rose-900">
          <div className="font-medium">
            {phase.code === 'redraft_inflight'
              ? '另一次生成正在进行中'
              : phase.code === 'campaign_run_in_flight'
              ? 'campaign 还有别的 agent run 在跑'
              : phase.code === 'already_sent'
              ? '邮件已经发出，无需再生成'
              : phase.code === 'approved_draft_exists'
              ? '存在已审批的旧草稿（请确认）'
              : '生成失败'}
          </div>
          <div className="mt-0.5 break-words">{phase.message}</div>
          {phase.code !== 'redraft_inflight' && phase.code !== 'campaign_run_in_flight' && (
            <button
              type="button"
              onClick={() => setPhase({ kind: 'idle' })}
              className="mt-1 rounded border border-rose-300 bg-white px-2 py-0.5 text-[10px] text-rose-700 hover:bg-rose-100"
            >
              关闭
            </button>
          )}
        </div>
      )}
    </div>
  );
}

// "邮箱缺失" 面板。仅在 primaryEmail 为空时展示，给操作员两条路：
//   1. "🔍 全网搜索"——派一个 kol-email-discovery 的 gateway run，
//      30–120s 内把 primary_email + 来源 facts 写回 CAL。
//   2. "✏️ 手动填入"——展开内联表单，写一份 manual 邮箱。
//
// 这两条路在 cold-outreach 真正生成 draft 之前是必须的（kol-cold-
// outreach 的 Step 4 明确：没有 primary_email 就 escalation 而不
// drafting）。把按钮挂在这里，操作员不用跳到其他页面去填。
//
// Race conditions:
// * 同一 (identity, env) 在 300s 内只允许触发一次 discover-email —
//   后端 dedup_key 兜底，前端 submitting / running 状态防双击。
// * 手动填入 + 搜索同时进行：skill 自己会在写之前再 read 一次
//   identity.primary_email，非空则 skip 写入（SKILL.md 明确规定）。
// * 已有邮箱时：手动填入要 override_existing=true，搜索直接 409
//   (already_has_email)。两条路都会暴露给操作员明确选项。
// * 搜索进行中页面刷新：本地 state 丢失但后端 dedup 还在；操作员
//   再点会得到 409 inflight，UI 会显示原 run 的 started_at + run_id。
//
// 时间预算：``DISCOVER_INFLIGHT_DISPLAY_MS`` = 180s。略小于后端
// INFLIGHT_TTL_SECONDS=300s。skill 实测 30–120s，到 180s 还没回的
// 大概率是失败，让操作员能重试比死等更友好。
type EmailPhase =
  | { kind: 'idle' }
  | { kind: 'manual_open' }
  | { kind: 'submitting'; action: 'manual' | 'discover' }
  | {
      kind: 'discover_running';
      runId: string | null;
      startedAt: number;
      // sessionStorage key so a page reload mid-search still shows the
      // running state instead of inviting the operator to re-click.
      // Cleared when the panel unmounts on email arrival or when the
      // window expires.
      cooldownUntil: number;
    }
  | { kind: 'error'; action: 'manual' | 'discover'; code: string | null; message: string };

const DISCOVER_INFLIGHT_DISPLAY_MS = 180_000;

function discoverCooldownStorageKey(identityId: number, env: string) {
  return `koc.discover_email_cooldown:${env}:${identityId}`;
}

function readDiscoverCooldown(
  identityId: number,
  env: string,
): { runId: string | null; startedAt: number; cooldownUntil: number } | null {
  try {
    const raw = sessionStorage.getItem(discoverCooldownStorageKey(identityId, env));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as {
      runId?: string | null;
      startedAt?: number;
      cooldownUntil?: number;
    };
    if (
      typeof parsed.cooldownUntil !== 'number'
      || parsed.cooldownUntil <= Date.now()
    ) {
      sessionStorage.removeItem(discoverCooldownStorageKey(identityId, env));
      return null;
    }
    return {
      runId: parsed.runId ?? null,
      startedAt: parsed.startedAt ?? Date.now(),
      cooldownUntil: parsed.cooldownUntil,
    };
  } catch {
    return null;
  }
}

function writeDiscoverCooldown(
  identityId: number,
  env: string,
  payload: { runId: string | null; startedAt: number; cooldownUntil: number },
) {
  try {
    sessionStorage.setItem(
      discoverCooldownStorageKey(identityId, env),
      JSON.stringify(payload),
    );
  } catch {
    // sessionStorage quota / disabled in private mode — best-effort.
  }
}

function clearDiscoverCooldown(identityId: number, env: string) {
  try {
    sessionStorage.removeItem(discoverCooldownStorageKey(identityId, env));
  } catch {
    // ignore
  }
}

function EmailPanel({
  identityId,
  handle,
  primaryEmail,
  campaignId,
  env,
  onChanged,
}: {
  identityId: number;
  handle: string;
  primaryEmail: string | null;
  campaignId: string;
  env: 'TEST' | 'LIVE';
  onChanged: () => void;
}) {
  // On mount: pick up any in-flight discover from sessionStorage so a
  // page reload doesn't reset the button to "clickable" while the
  // backend is still working on the same run.
  const [phase, setPhase] = useState<EmailPhase>(() => {
    const cd = readDiscoverCooldown(identityId, env);
    if (cd) {
      return {
        kind: 'discover_running',
        runId: cd.runId,
        startedAt: cd.startedAt,
        cooldownUntil: cd.cooldownUntil,
      };
    }
    return { kind: 'idle' };
  });
  const [draft, setDraft] = useState<string>('');
  // Tick once per second while a discover run is "running" so the
  // "已等待 Xs" countdown updates without re-creating the timer on
  // every render.
  const [, forceTick] = useState(0);
  useEffect(() => {
    if (phase.kind !== 'discover_running') return;
    const tick = setInterval(() => forceTick((n) => (n + 1) & 0xffff), 1000);
    return () => clearInterval(tick);
  }, [phase.kind]);

  // When the cooldown window expires, drop back to idle so the
  // operator can retry. Backend TTL is 300s; we time out the UI at
  // 180s so a failed/slow run is recoverable without page reload.
  useEffect(() => {
    if (phase.kind !== 'discover_running') return;
    const remaining = phase.cooldownUntil - Date.now();
    if (remaining <= 0) {
      clearDiscoverCooldown(identityId, env);
      setPhase({ kind: 'idle' });
      return;
    }
    const t = setTimeout(() => {
      clearDiscoverCooldown(identityId, env);
      setPhase((p) =>
        p.kind === 'discover_running' && p.startedAt === phase.startedAt
          ? { kind: 'idle' }
          : p,
      );
    }, remaining);
    return () => clearTimeout(t);
  }, [phase, identityId, env]);

  const hasEmail = Boolean(primaryEmail && primaryEmail.trim());

  // Email arrived — clear the cooldown so a future "no email" state
  // on the same identity (rare but possible if the operator manually
  // wipes it) starts fresh.
  useEffect(() => {
    if (hasEmail) {
      clearDiscoverCooldown(identityId, env);
    }
  }, [hasEmail, identityId, env]);

  // Don't render at all when the email is already there — the header
  // already shows it, and the detail page is dense enough that
  // dead-weight panels muddy the operator's scan.
  if (hasEmail) return null;

  const submitting = phase.kind === 'submitting';

  const discover = async () => {
    setPhase({ kind: 'submitting', action: 'discover' });
    try {
      const r = await api.post<{ run_id: string | null; started_at: string }>(
        `/kols/${identityId}/discover-email`,
        { env, campaign_id: campaignId },
      );
      const startedAt = Date.now();
      const cooldownUntil = startedAt + DISCOVER_INFLIGHT_DISPLAY_MS;
      writeDiscoverCooldown(identityId, env, {
        runId: r.run_id,
        startedAt,
        cooldownUntil,
      });
      setPhase({
        kind: 'discover_running',
        runId: r.run_id,
        startedAt,
        cooldownUntil,
      });
      onChanged();
    } catch (ex) {
      const detail = parseApiErrorDetail(ex);
      const code = detail?.code ?? null;
      // Backend says a previous run is still going — sync our local
      // cooldown to whatever it told us about the original dispatch
      // (best-effort; if backend didn't send started_at, fall back
      // to "now" and just show the standard window).
      if (code === 'discover_email_inflight') {
        const startedAt = detail?.started_at
          ? Date.parse(detail.started_at)
          : Date.now();
        const safeStart = Number.isNaN(startedAt) ? Date.now() : startedAt;
        const cooldownUntil = safeStart + DISCOVER_INFLIGHT_DISPLAY_MS;
        writeDiscoverCooldown(identityId, env, {
          runId: detail?.run_id ?? null,
          startedAt: safeStart,
          cooldownUntil,
        });
        setPhase({
          kind: 'discover_running',
          runId: detail?.run_id ?? null,
          startedAt: safeStart,
          cooldownUntil,
        });
        return;
      }
      setPhase({
        kind: 'error',
        action: 'discover',
        code,
        message: detail?.message ?? String(ex),
      });
    }
  };

  const submitManual = async () => {
    const email = draft.trim();
    if (!email) {
      setPhase({
        kind: 'error',
        action: 'manual',
        code: 'empty_email',
        message: '邮箱不能为空',
      });
      return;
    }
    // Very loose client-side check — server runs proper validation.
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      setPhase({
        kind: 'error',
        action: 'manual',
        code: 'invalid_email',
        message: '邮箱格式看起来不对：' + email,
      });
      return;
    }
    setPhase({ kind: 'submitting', action: 'manual' });
    try {
      await api.post(`/kols/${identityId}/email`, {
        email,
        env,
        campaign_id: campaignId,
        override_existing: false,
      });
      setDraft('');
      setPhase({ kind: 'idle' });
      onChanged();
    } catch (ex) {
      const detail = parseApiErrorDetail(ex);
      setPhase({
        kind: 'error',
        action: 'manual',
        code: detail?.code ?? null,
        message: detail?.message ?? String(ex),
      });
    }
  };

  return (
    <div className="rounded border border-rose-300 bg-rose-50 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <div className="text-xs font-medium uppercase tracking-wide text-rose-800">
          邮箱缺失
        </div>
        <span className="text-xs text-rose-700">
          @{handle} 没有 <span className="font-mono">primary_email</span>，
          {' '}cold-outreach 不会为他生成草稿。
        </span>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        {(() => {
          const isSubmitting = phase.kind === 'submitting' && phase.action === 'discover';
          const isRunning = phase.kind === 'discover_running';
          const isBusy = isSubmitting || isRunning;
          const elapsedSec = isRunning
            ? Math.max(0, Math.floor((Date.now() - phase.startedAt) / 1000))
            : 0;
          const remainSec = isRunning
            ? Math.max(0, Math.ceil((phase.cooldownUntil - Date.now()) / 1000))
            : 0;
          const label = isSubmitting
            ? '派单中…'
            : isRunning
            ? `搜索中… 已等待 ${elapsedSec}s`
            : '🔍 全网搜索邮箱';
          const title = isRunning
            ? (
              `Gmail 搜索 agent 正在跑，run_id=${phase.runId ?? '?'}。`
              + ` skill 预计 30–120s，本地最长锁定 ${Math.ceil(DISCOVER_INFLIGHT_DISPLAY_MS / 1000)}s（约 ${remainSec}s 后超时可重试）。`
              + ` 即使你现在再点也不会真发起第二次——后端 dedup TTL 300s 会兜底。`
            )
            : '派一个 agent run 去公网搜索 KOL 邮箱（link-in-bio / 个人站 / 媒体包），30–120s 后结果写回 CAL';
          return (
            <button
              type="button"
              onClick={discover}
              disabled={isBusy}
              title={title}
              className={
                'flex items-center gap-1.5 rounded px-3 py-1.5 text-sm font-medium transition '
                + (isBusy
                  ? 'cursor-not-allowed bg-sky-200 text-sky-700'
                  : 'bg-sky-600 text-white hover:bg-sky-700')
              }
            >
              {isRunning && (
                <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-sky-500 border-r-transparent" />
              )}
              <span>{label}</span>
            </button>
          );
        })()}

        <button
          type="button"
          onClick={() =>
            setPhase((p) => (p.kind === 'manual_open' ? { kind: 'idle' } : { kind: 'manual_open' }))
          }
          disabled={submitting}
          className="rounded border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
        >
          {phase.kind === 'manual_open' ? '取消' : '✏️ 手动填入'}
        </button>

        {phase.kind === 'discover_running' && (
          <div className="rounded bg-sky-100 px-2 py-1 text-xs text-sky-800" role="status">
            ✓ 搜索 agent 已派出（run_id={phase.runId ? phase.runId.slice(0, 8) : '?'}…），
            {' '}邮箱写回 CAL 后这里会自动刷新；找不到会以 escalation 形式提交。
            {' '}如果 {Math.ceil(DISCOVER_INFLIGHT_DISPLAY_MS / 1000)}s 还没结果，按钮会重新可点。
          </div>
        )}
      </div>

      {(phase.kind === 'manual_open'
        || (phase.kind === 'submitting' && phase.action === 'manual')) && (
        <div className="mt-3 rounded border border-slate-200 bg-white p-2">
          <label className="block text-xs text-slate-600">
            邮箱
            <input
              type="email"
              autoFocus
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') submitManual();
                if (e.key === 'Escape') setPhase({ kind: 'idle' });
              }}
              placeholder="name@example.com"
              disabled={submitting}
              className="mt-1 w-full rounded border border-slate-300 px-2 py-1 text-sm focus:border-emerald-400 focus:outline-none disabled:bg-slate-50 disabled:text-slate-400"
            />
          </label>
          <div className="mt-2 flex justify-end gap-2">
            <button
              type="button"
              onClick={() => {
                setDraft('');
                setPhase({ kind: 'idle' });
              }}
              disabled={submitting}
              className="rounded border border-slate-300 bg-white px-2 py-1 text-xs text-slate-600 hover:bg-slate-50 disabled:opacity-50"
            >
              取消
            </button>
            <button
              type="button"
              onClick={submitManual}
              disabled={!draft.trim() || submitting}
              className="rounded bg-emerald-600 px-2 py-1 text-xs font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
            >
              {submitting ? '保存中…' : '保存'}
            </button>
          </div>
        </div>
      )}

      {phase.kind === 'error' && (
        <div className="mt-3 rounded border border-rose-300 bg-white p-2 text-xs text-rose-900">
          <div className="font-medium">
            {phase.code === 'already_has_email'
              ? '邮箱已经有了'
              : phase.code === 'discover_email_inflight'
              ? '已经有一个搜索在跑'
              : phase.code === 'email_already_set'
              ? '已有邮箱（需 override_existing=true 才能覆盖）'
              : phase.code === 'invalid_email' || phase.code === 'empty_email'
              ? '输入格式不对'
              : phase.action === 'discover'
              ? '搜索请求失败'
              : '保存失败'}
          </div>
          <div className="mt-0.5 break-words">{phase.message}</div>
          <button
            type="button"
            onClick={() =>
              setPhase(phase.action === 'manual' ? { kind: 'manual_open' } : { kind: 'idle' })
            }
            className="mt-1 rounded border border-rose-300 bg-white px-2 py-0.5 text-[10px] text-rose-700 hover:bg-rose-100"
          >
            关闭
          </button>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 社交主页快速跳转 — SocialLinksBar + SocialLinksPanel
// ---------------------------------------------------------------------------
//
// 操作员在审批/起草/追单各阶段经常想跳到 KOL 的真实主页看动态（"她还在
// 更新吗""IG bio 那个链接是啥"）。这两个组件让 identity.*_profile_url
// facts 直接变成可点击的快速跳转按钮，外加 KOL 没邮箱不会跑 email-
// discovery 时操作员能手动触发 social-link 搜索的入口。
//
// 数据约定：所有社交 URL 走 identity namespace 的 facts，key 与
// factKeyLabel.ts 注册保持一致；renderFactValue 已经会把 kind: 'url' 的
// fact 渲染为链接，但 ConfirmedFactsPanel 在折叠区域，操作员看一眼上方
// 即可一键跳走的体验需要这个独立 bar。
//
// SocialLinksBar：紧凑图标按钮组。没有任何 URL 时不渲染（不留空栏）。
// SocialLinksPanel：常驻折叠面板（与 EmailPanel 的"邮箱缺失"区别在于
// 社交链接永远不存在"完整"状态，操作员随时可能想补充一个）。

type SocialPlatformKey =
  | 'identity.instagram_profile_url'
  | 'identity.tiktok_profile_url'
  | 'identity.youtube_profile_url'
  | 'identity.facebook_profile_url'
  | 'identity.twitter_profile_url'
  | 'identity.threads_profile_url'
  | 'identity.linktree_url'
  | 'identity.personal_site_url';

// 同时是 SocialLinksBar 的渲染顺序与 SocialLinksPanel 下拉框的展示顺序。
// IG 排第一是因为大部分 KOL 都是 IG 来源；个人站排最后是因为它最罕见。
const SOCIAL_LINKS: ReadonlyArray<{
  key: SocialPlatformKey;
  label: string;
  shortLabel: string;
}> = [
  { key: 'identity.instagram_profile_url', label: 'Instagram', shortLabel: 'IG' },
  { key: 'identity.tiktok_profile_url', label: 'TikTok', shortLabel: 'TikTok' },
  { key: 'identity.youtube_profile_url', label: 'YouTube', shortLabel: 'YT' },
  { key: 'identity.facebook_profile_url', label: 'Facebook', shortLabel: 'FB' },
  { key: 'identity.twitter_profile_url', label: 'X', shortLabel: 'X' },
  { key: 'identity.threads_profile_url', label: 'Threads', shortLabel: 'Threads' },
  { key: 'identity.linktree_url', label: 'Link-in-bio', shortLabel: 'bio' },
  { key: 'identity.personal_site_url', label: '个人站', shortLabel: 'site' },
];

function readSocialUrl(facts: Record<string, unknown>, key: SocialPlatformKey): string | null {
  const v = facts[key];
  if (typeof v !== 'string') return null;
  const trimmed = v.trim();
  if (!trimmed) return null;
  // 客户端轻量校验：拒绝渲染明显不是 URL 的字段值（防止脏数据生成
  // 点不动 / 跳错地方的按钮）。后端 set_social_link 已经在写入前做了
  // 完整校验，这里只是兜底。
  if (!/^https?:\/\//i.test(trimmed)) return null;
  return trimmed;
}

function SocialLinksBar({ facts }: { facts: Record<string, unknown> }) {
  const items = SOCIAL_LINKS
    .map(({ key, label, shortLabel }) => {
      const url = readSocialUrl(facts, key);
      return url ? { key, label, shortLabel, url } : null;
    })
    .filter((x): x is { key: SocialPlatformKey; label: string; shortLabel: string; url: string } => !!x);

  if (items.length === 0) return null;

  return (
    <div className="flex flex-wrap items-center gap-1.5 rounded border border-slate-200 bg-white px-2 py-1.5">
      <span className="text-[10px] uppercase tracking-wide text-slate-500">
        快速跳转
      </span>
      {items.map((item) => (
        <a
          key={item.key}
          href={item.url}
          target="_blank"
          rel="noreferrer noopener"
          title={`${item.label} · ${item.url}`}
          className="rounded border border-sky-200 bg-sky-50 px-2 py-0.5 text-xs font-medium text-sky-800 hover:border-sky-400 hover:bg-sky-100"
        >
          {item.shortLabel}
        </a>
      ))}
    </div>
  );
}

// SocialLinksPanel 状态机镜像 EmailPanel：discover_running 持续 sessionStorage
// 冷却 + 一秒一次的 tick 让 "已等待 Xs" 走起来。后端 dedup TTL 是 300s，
// 前端 UI 限 180s 让一次失败/慢的 run 不需要刷页就能重试。
type SocialDiscoverPhase =
  | { kind: 'idle' }
  | { kind: 'submitting' }
  | {
      kind: 'discover_running';
      runId: string | null;
      startedAt: number;
      cooldownUntil: number;
    }
  | { kind: 'error'; code: string | null; message: string };

const SOCIAL_DISCOVER_INFLIGHT_DISPLAY_MS = 180_000;

function socialDiscoverCooldownKey(identityId: number, env: string) {
  return `koc.discover_social_cooldown:${env}:${identityId}`;
}

function readSocialDiscoverCooldown(
  identityId: number,
  env: string,
): { runId: string | null; startedAt: number; cooldownUntil: number } | null {
  try {
    const raw = sessionStorage.getItem(socialDiscoverCooldownKey(identityId, env));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as {
      runId?: string | null;
      startedAt?: number;
      cooldownUntil?: number;
    };
    if (
      typeof parsed.cooldownUntil !== 'number'
      || parsed.cooldownUntil <= Date.now()
    ) {
      sessionStorage.removeItem(socialDiscoverCooldownKey(identityId, env));
      return null;
    }
    return {
      runId: parsed.runId ?? null,
      startedAt: parsed.startedAt ?? Date.now(),
      cooldownUntil: parsed.cooldownUntil,
    };
  } catch {
    return null;
  }
}

function writeSocialDiscoverCooldown(
  identityId: number,
  env: string,
  payload: { runId: string | null; startedAt: number; cooldownUntil: number },
) {
  try {
    sessionStorage.setItem(
      socialDiscoverCooldownKey(identityId, env),
      JSON.stringify(payload),
    );
  } catch {
    // best-effort
  }
}

function clearSocialDiscoverCooldown(identityId: number, env: string) {
  try {
    sessionStorage.removeItem(socialDiscoverCooldownKey(identityId, env));
  } catch {
    // ignore
  }
}

function SocialLinksPanel({
  identityId,
  facts,
  campaignId,
  env,
  onChanged,
}: {
  identityId: number;
  facts: Record<string, unknown>;
  campaignId: string;
  env: 'TEST' | 'LIVE';
  onChanged: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [phase, setPhase] = useState<SocialDiscoverPhase>(() => {
    const cd = readSocialDiscoverCooldown(identityId, env);
    if (cd) {
      return {
        kind: 'discover_running',
        runId: cd.runId,
        startedAt: cd.startedAt,
        cooldownUntil: cd.cooldownUntil,
      };
    }
    return { kind: 'idle' };
  });
  const [manualKey, setManualKey] = useState<SocialPlatformKey>(SOCIAL_LINKS[1].key);
  const [manualUrl, setManualUrl] = useState('');
  const [manualBusy, setManualBusy] = useState(false);
  const [manualError, setManualError] = useState<string | null>(null);

  // 1s tick so "已等待 Xs" counter updates without re-creating timers.
  const [, forceTick] = useState(0);
  useEffect(() => {
    if (phase.kind !== 'discover_running') return;
    const tick = setInterval(() => forceTick((n) => (n + 1) & 0xffff), 1000);
    return () => clearInterval(tick);
  }, [phase.kind]);

  // Cooldown expiry → back to idle so operator can retry.
  useEffect(() => {
    if (phase.kind !== 'discover_running') return;
    const remaining = phase.cooldownUntil - Date.now();
    if (remaining <= 0) {
      clearSocialDiscoverCooldown(identityId, env);
      setPhase({ kind: 'idle' });
      return;
    }
    const t = setTimeout(() => {
      clearSocialDiscoverCooldown(identityId, env);
      setPhase((p) =>
        p.kind === 'discover_running' && p.startedAt === phase.startedAt
          ? { kind: 'idle' }
          : p,
      );
    }, remaining);
    return () => clearTimeout(t);
  }, [phase, identityId, env]);

  const filledCount = useMemo(
    () => SOCIAL_LINKS.reduce((n, { key }) => (readSocialUrl(facts, key) ? n + 1 : n), 0),
    [facts],
  );
  const missing = useMemo(
    () => SOCIAL_LINKS.filter(({ key }) => !readSocialUrl(facts, key)),
    [facts],
  );

  const discover = async () => {
    setPhase({ kind: 'submitting' });
    try {
      const r = await api.post<{ run_id: string | null; started_at: string }>(
        `/kols/${identityId}/discover-social-links`,
        { env, campaign_id: campaignId },
      );
      const startedAt = Date.now();
      const cooldownUntil = startedAt + SOCIAL_DISCOVER_INFLIGHT_DISPLAY_MS;
      writeSocialDiscoverCooldown(identityId, env, {
        runId: r.run_id,
        startedAt,
        cooldownUntil,
      });
      setPhase({
        kind: 'discover_running',
        runId: r.run_id,
        startedAt,
        cooldownUntil,
      });
      onChanged();
    } catch (ex) {
      const detail = parseApiErrorDetail(ex);
      const code = detail?.code ?? null;
      if (code === 'discover_social_links_inflight') {
        const startedAt = detail?.started_at
          ? Date.parse(detail.started_at)
          : Date.now();
        const safeStart = Number.isNaN(startedAt) ? Date.now() : startedAt;
        const cooldownUntil = safeStart + SOCIAL_DISCOVER_INFLIGHT_DISPLAY_MS;
        writeSocialDiscoverCooldown(identityId, env, {
          runId: detail?.run_id ?? null,
          startedAt: safeStart,
          cooldownUntil,
        });
        setPhase({
          kind: 'discover_running',
          runId: detail?.run_id ?? null,
          startedAt: safeStart,
          cooldownUntil,
        });
        return;
      }
      setPhase({
        kind: 'error',
        code,
        message: detail?.message ?? String(ex),
      });
    }
  };

  const submitManual = async () => {
    const url = manualUrl.trim();
    if (!url) {
      setManualError('URL 不能为空');
      return;
    }
    if (!/^https?:\/\//i.test(url)) {
      setManualError('URL 必须以 http:// 或 https:// 开头');
      return;
    }
    setManualBusy(true);
    setManualError(null);
    try {
      await api.post(`/kols/${identityId}/social-link`, {
        fact_key: manualKey,
        url,
        env,
        campaign_id: campaignId,
        override_existing: false,
      });
      setManualUrl('');
      onChanged();
    } catch (ex) {
      const detail = parseApiErrorDetail(ex);
      if (detail?.code === 'social_link_already_set') {
        const currentUrl =
          (detail as unknown as { current_url?: string })?.current_url || '?';
        setManualError(
          `${manualKey} 已经有值（${currentUrl}）。本表单不支持覆盖；如确实需要替换，请联系管理员从 bridge 端清空旧值后再填。`,
        );
      } else {
        setManualError(detail?.message ?? String(ex));
      }
    } finally {
      setManualBusy(false);
    }
  };

  const isRunning = phase.kind === 'discover_running';
  const isSubmitting = phase.kind === 'submitting';
  const isBusy = isRunning || isSubmitting;
  const elapsedSec = isRunning
    ? Math.max(0, Math.floor((Date.now() - phase.startedAt) / 1000))
    : 0;
  const remainSec = isRunning
    ? Math.max(0, Math.ceil((phase.cooldownUntil - Date.now()) / 1000))
    : 0;

  return (
    <div className="rounded border border-slate-200 bg-white">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center justify-between gap-2 px-3 py-1.5 text-left hover:bg-slate-50"
      >
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium uppercase tracking-wide text-slate-500">
            社交主页 / 快速跳转
          </span>
          <span className="text-[10px] text-slate-400">
            已填 {filledCount} / {SOCIAL_LINKS.length}
          </span>
          {isRunning && (
            <span className="rounded bg-sky-100 px-1.5 py-0.5 text-[10px] text-sky-800">
              搜索中 {elapsedSec}s
            </span>
          )}
        </div>
        <span className="text-xs text-slate-400">{expanded ? '收起' : '展开'}</span>
      </button>

      {expanded && (
        <div className="border-t border-slate-200 px-3 py-2.5">
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={discover}
              disabled={isBusy || missing.length === 0}
              title={
                missing.length === 0
                  ? '所有社交主页都已填写，无需再搜索'
                  : isRunning
                  ? `搜索 agent 正在跑，run_id=${phase.runId ?? '?'}。约 ${remainSec}s 后超时可重试`
                  : '派一个 agent 去公网搜索社交主页（link-in-bio / 个人站 / Facebook About），30–120s 后写回 CAL'
              }
              className={
                'flex items-center gap-1.5 rounded px-3 py-1.5 text-sm font-medium transition '
                + (isBusy || missing.length === 0
                  ? 'cursor-not-allowed bg-slate-200 text-slate-500'
                  : 'bg-sky-600 text-white hover:bg-sky-700')
              }
            >
              {isRunning && (
                <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-sky-500 border-r-transparent" />
              )}
              <span>
                {isSubmitting
                  ? '派单中…'
                  : isRunning
                  ? `搜索中… 已等待 ${elapsedSec}s`
                  : '🔍 搜索社交主页'}
              </span>
            </button>

            <span className="text-xs text-slate-500">
              {missing.length === 0
                ? '已收集所有支持的平台 URL'
                : `还缺：${missing.map((m) => m.label).join(' / ')}`}
            </span>
          </div>

          {isRunning && (
            <div
              className="mt-2 rounded bg-sky-50 px-2 py-1 text-xs text-sky-800"
              role="status"
            >
              ✓ 搜索 agent 已派出（run_id={phase.runId ? phase.runId.slice(0, 8) : '?'}…），
              {' '}找到的 URL 写回 CAL 后这里会自动刷新。
              {' '}如果 {Math.ceil(SOCIAL_DISCOVER_INFLIGHT_DISPLAY_MS / 1000)}s 还没结果，按钮会重新可点。
            </div>
          )}

          {phase.kind === 'error' && (
            <div className="mt-2 rounded border border-rose-300 bg-rose-50 p-2 text-xs text-rose-900">
              <div className="font-medium">搜索请求失败</div>
              <div className="mt-0.5 break-words">{phase.message}</div>
              <button
                type="button"
                onClick={() => setPhase({ kind: 'idle' })}
                className="mt-1 rounded border border-rose-300 bg-white px-2 py-0.5 text-[10px] text-rose-700 hover:bg-rose-100"
              >
                关闭
              </button>
            </div>
          )}

          <div className="mt-3 border-t border-slate-100 pt-2.5">
            <div className="text-xs font-medium text-slate-600">✏️ 手动添加</div>
            <div className="mt-1.5 flex flex-wrap items-end gap-2">
              <label className="text-xs text-slate-600">
                平台
                <select
                  value={manualKey}
                  onChange={(e) => {
                    setManualKey(e.target.value as SocialPlatformKey);
                    setManualError(null);
                  }}
                  disabled={manualBusy}
                  className="mt-0.5 block rounded border border-slate-300 px-2 py-1 text-sm focus:border-emerald-400 focus:outline-none disabled:bg-slate-50"
                >
                  {SOCIAL_LINKS.map(({ key, label }) => (
                    <option key={key} value={key}>
                      {label}
                      {readSocialUrl(facts, key) ? ' (已填)' : ''}
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex-1 text-xs text-slate-600">
                URL
                <input
                  type="url"
                  value={manualUrl}
                  onChange={(e) => {
                    setManualUrl(e.target.value);
                    setManualError(null);
                  }}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') submitManual();
                  }}
                  placeholder="https://www.tiktok.com/@..."
                  disabled={manualBusy}
                  className="mt-0.5 block w-full rounded border border-slate-300 px-2 py-1 text-sm focus:border-emerald-400 focus:outline-none disabled:bg-slate-50 disabled:text-slate-400"
                />
              </label>
              <button
                type="button"
                onClick={submitManual}
                disabled={manualBusy || !manualUrl.trim()}
                className="rounded bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-700 disabled:cursor-not-allowed disabled:bg-emerald-200 disabled:text-emerald-700"
              >
                {manualBusy ? '保存中…' : '保存'}
              </button>
            </div>
            {manualError && (
              <div className="mt-1.5 rounded border border-rose-300 bg-rose-50 px-2 py-1 text-xs text-rose-900">
                {manualError}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
