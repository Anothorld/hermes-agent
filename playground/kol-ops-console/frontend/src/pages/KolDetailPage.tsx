import { ReactNode, useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { api, Lane } from '../api';
import { GoalProgressBar } from '../components/GoalProgressBar';
import { FactsEditor } from '../components/FactsEditor';
import { RepeatKolBadge } from '../components/RepeatKolBadge';

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

function resolveEnv(urlEnv: string | null): 'TEST' | 'LIVE' {
  const raw = (urlEnv || localStorage.getItem('kolEnv') || 'LIVE').toUpperCase();
  return raw === 'TEST' ? 'TEST' : 'LIVE';
}

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

export function KolDetailPage() {
  const { id } = useParams();
  const [search] = useSearchParams();
  const campaignId = search.get('campaign_id') || '';
  const env = resolveEnv(search.get('env'));
  const identityId = Number(id);
  const [identity, setIdentity] = useState<IdentityResponse | null>(null);
  const [goals, setGoals] = useState<GoalsResponse | null>(null);
  const [escalations, setEscalations] = useState<EscalationLite[]>([]);
  const [pendingApprovals, setPendingApprovals] = useState<number>(0);
  const [facts, setFacts] = useState<Record<string, unknown>>({});
  const [err, setErr] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!identityId || !campaignId) {
      setErr('Need identity id + ?campaign_id=<>');
      return;
    }
    try {
      const [idResp, goalsResp, esc, approvals, factsResp] = await Promise.all([
        api.get<IdentityResponse>(`/kols/${identityId}?env=${env}`),
        api.get<GoalsResponse>(
          `/identities/${identityId}/goals?campaign_id=${encodeURIComponent(campaignId)}&env=${env}`,
        ),
        api.get<EscalationLite[]>(`/escalations?state=awaiting_answer&env=${env}`),
        api.get<Array<{ identity_id?: number; campaign_id?: string }>>(`/approvals?env=${env}`),
        api.get<{ facts: Record<string, unknown> }>(
          `/facts/${identityId}?campaign_id=${encodeURIComponent(campaignId)}&env=${env}`,
        ),
      ]);
      setIdentity(idResp);
      setGoals(goalsResp);
      setEscalations(
        (esc || []).filter((e) => (e as unknown as { identity_id?: number }).identity_id === identityId),
      );
      setPendingApprovals(
        (approvals || []).filter(
          (a) => a.identity_id === identityId && a.campaign_id === campaignId,
        ).length,
      );
      setFacts(factsResp?.facts ?? {});
      setErr(null);
    } catch (ex) {
      setErr(String(ex));
    }
  }, [identityId, campaignId, env]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const lanes: Lane[] = ['commerce', 'fulfillment', 'publish', 'meta'];
  const goalsByLane = useMemo(() => {
    const out: Record<Lane, GoalEntry[]> = {
      commerce: [], fulfillment: [], publish: [], meta: [],
    };
    for (const g of goals?.goals ?? []) {
      if (g.lane in out) out[g.lane].push(g);
    }
    return out;
  }, [goals]);

  if (err) return <div className="text-red-600">{err}</div>;
  if (!identity || !goals) return <div className="text-sm text-slate-500">Loading…</div>;

  const commerceActive = goalsByLane.commerce.find(
    (g) => g.status === 'active' || g.status === 'blocked',
  );
  const commerceCompleted = goalsByLane.commerce
    .filter((g) => g.status === 'satisfied')
    .map((g) => g.goal);
  const allMissing = goals.goals.flatMap((g) => g.missing_facts ?? []);
  const displayHandle = identity.primary_handle || identity.display_name || `kol#${identity.id}`;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-lg font-semibold">
          @{displayHandle}
          <RepeatKolBadge
            count={identity.repeat_count || 0}
            lastOutcome={identity.last_outcome ?? null}
          />
        </h1>
        <div className="text-xs text-slate-500">
          {identity.primary_email || <span className="italic">no email</span>} · {identity.env}
        </div>
        <Link
          to={`/kols/${identity.id}/relationship`}
          className="ml-auto text-xs text-sky-700 hover:underline"
        >
          history & reusable facts →
        </Link>
      </div>

      <div className="flex flex-wrap gap-2 text-xs">
        <Link
          to={`/approvals?campaign_id=${encodeURIComponent(campaignId)}&identity_id=${identity.id}&env=${env}`}
          className={`rounded px-2 py-1 ${
            pendingApprovals > 0
              ? 'bg-rose-100 text-rose-800 hover:bg-rose-200'
              : 'bg-slate-100 text-slate-600'
          }`}
        >
          Pending approvals: {pendingApprovals}
        </Link>
        <Link
          to={`/escalations?campaign_id=${encodeURIComponent(campaignId)}&identity_id=${identity.id}&env=${env}`}
          className={`rounded px-2 py-1 ${
            escalations.length > 0
              ? 'bg-amber-100 text-amber-800 hover:bg-amber-200'
              : 'bg-slate-100 text-slate-600'
          }`}
        >
          Open escalations: {escalations.length}
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
                <div className="text-xs italic text-slate-400">idle</div>
              ) : (
                <ul className="space-y-2">
                  {visible.map((g) => (
                    <li key={g.goal}>
                      <div className="flex items-center gap-2">
                        <span className="font-medium">{g.goal}</span>
                        <span className={`rounded px-1.5 py-0.5 text-[10px] ${statusChip(g.status)}`}>
                          {g.status}
                        </span>
                      </div>
                      {g.blocking_escalation_id && (
                        <div className="mt-1 rounded bg-amber-100 px-2 py-1 text-xs text-amber-900">
                          blocked by escalation #{g.blocking_escalation_id}
                        </div>
                      )}
                      {!!g.missing_facts?.length && (
                        <div className="mt-1 flex flex-wrap gap-1">
                          {g.missing_facts.map((f) => (
                            <span
                              key={f}
                              className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[10px] text-slate-700"
                            >
                              {f}
                            </span>
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

      {!!escalations.length && (
        <div className="rounded border border-amber-300 bg-amber-50 p-3">
          <div className="mb-1 text-xs font-medium uppercase tracking-wide text-amber-800">
            Open escalations ({escalations.length})
          </div>
          <ul className="space-y-1 text-sm">
            {escalations.map((e) => (
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

      <ConfirmedFactsPanel facts={facts} />

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

const NS_ORDER_PANEL: Array<'identity' | 'offer' | 'fulfillment' | 'approval'> = [
  'identity', 'offer', 'fulfillment', 'approval',
];

const NS_PANEL_COLOR: Record<string, string> = {
  identity: 'border-sky-200 bg-sky-50',
  offer: 'border-emerald-200 bg-emerald-50',
  fulfillment: 'border-amber-200 bg-amber-50',
  approval: 'border-rose-200 bg-rose-50',
  other: 'border-slate-200 bg-slate-50',
};

function ConfirmedFactsPanel({ facts }: { facts: Record<string, unknown> }) {
  const groups = useMemo(() => {
    const out: Record<string, Array<[string, unknown]>> = {
      identity: [], offer: [], fulfillment: [], approval: [], other: [],
    };
    for (const [k, v] of Object.entries(facts)) {
      const ns = k.split('.', 1)[0];
      (out[ns] ?? out.other).push([k, v]);
    }
    for (const arr of Object.values(out)) arr.sort((a, b) => a[0].localeCompare(b[0]));
    return out;
  }, [facts]);

  const total = Object.values(groups).reduce((n, arr) => n + arr.length, 0);
  if (total === 0) {
    return (
      <div className="rounded border border-slate-200 bg-white p-3 text-xs text-slate-500">
        No confirmed facts yet for this campaign.
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <div className="text-xs font-medium uppercase tracking-wide text-slate-500">
        Confirmed facts ({total})
      </div>
      <div className="grid grid-cols-1 gap-2 lg:grid-cols-2">
        {NS_ORDER_PANEL.map((ns) => {
          const entries = groups[ns];
          if (!entries.length) return null;
          return (
            <details key={ns} open className={`rounded border p-2 ${NS_PANEL_COLOR[ns]}`}>
              <summary className="cursor-pointer text-xs font-semibold uppercase tracking-wide">
                {ns} ({entries.length})
              </summary>
              <ul className="mt-2 space-y-1.5">
                {entries.map(([k, v]) => (
                  <li key={k} className="rounded bg-white/60 p-1.5">
                    <div className="break-all font-mono text-[11px] leading-tight text-slate-600">
                      {k}
                    </div>
                    <div className="mt-0.5 break-all font-mono text-xs leading-snug text-slate-900">
                      {renderFactValue(v)}
                    </div>
                  </li>
                ))}
              </ul>
            </details>
          );
        })}
        {groups.other.length > 0 && (
          <details className={`rounded border p-2 ${NS_PANEL_COLOR.other}`}>
            <summary className="cursor-pointer text-xs font-semibold uppercase tracking-wide">
              other ({groups.other.length})
            </summary>
            <ul className="mt-2 space-y-1.5">
              {groups.other.map(([k, v]) => (
                <li key={k} className="rounded bg-white/60 p-1.5">
                  <div className="break-all font-mono text-[11px] leading-tight text-slate-600">
                    {k}
                  </div>
                  <div className="mt-0.5 break-all font-mono text-xs leading-snug text-slate-900">
                    {renderFactValue(v)}
                  </div>
                </li>
              ))}
            </ul>
          </details>
        )}
      </div>
    </div>
  );
}

function renderFactValue(v: unknown): ReactNode {
  if (v === null || v === undefined) return <span className="italic text-slate-400">—</span>;
  if (typeof v === 'boolean') return v ? 'true' : 'false';
  if (typeof v === 'number' || typeof v === 'string') return String(v);
  return (
    <details>
      <summary className="cursor-pointer text-slate-600">{Array.isArray(v) ? `[${v.length} items]` : '{...}'}</summary>
      <pre className="mt-1 whitespace-pre-wrap rounded bg-white/60 p-2 text-[11px]">
        {JSON.stringify(v, null, 2)}
      </pre>
    </details>
  );
}
