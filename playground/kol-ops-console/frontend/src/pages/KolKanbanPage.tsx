import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api, LaneSnapshot } from '../api';
import { GOAL_COLUMNS } from '../components/GoalProgressBar';
import { RepeatKolBadge } from '../components/RepeatKolBadge';
import { LaneFilterBar, type LaneFilter } from '../components/LaneFilterBar';

type LanesResponse = {
  campaign_id: string;
  lanes: LaneSnapshot[];
  counts?: { pending_approvals: number; open_escalations: number };
};

type EnrichedSnapshot = LaneSnapshot & {
  candidate_status?: string | null;
  archived?: boolean;
};

// Namespace prefix → tailwind chip color. Matches ApprovalsPage palette
// so operators see one consistent visual contract across the console.
const NS_CHIP: Record<string, string> = {
  identity: 'bg-sky-100 text-sky-800',
  offer: 'bg-emerald-100 text-emerald-800',
  fulfillment: 'bg-amber-100 text-amber-800',
  approval: 'bg-rose-100 text-rose-800',
};

function chipColorFor(fact: string): string {
  const ns = fact.split('.', 1)[0];
  return NS_CHIP[ns] ?? 'bg-slate-100 text-slate-700';
}

export function KolKanbanPage() {
  const [search, setSearch] = useSearchParams();
  const campaignId = search.get('campaign_id') || '';
  const [data, setData] = useState<EnrichedSnapshot[]>([]);
  const [counts, setCounts] = useState<{ pending_approvals: number; open_escalations: number }>(
    { pending_approvals: 0, open_escalations: 0 },
  );
  const [err, setErr] = useState<string | null>(null);
  const [env, setEnv] = useState<'TEST' | 'LIVE'>(() => {
    const stored = localStorage.getItem('kolEnv');
    return stored === 'LIVE' ? 'LIVE' : 'TEST';
  });
  const [laneFilter, setLaneFilter] = useState<LaneFilter>('all');
  const [repeatOnly, setRepeatOnly] = useState(false);
  const [showDone, setShowDone] = useState(false);

  const refresh = useCallback(async () => {
    if (!campaignId) {
      setData([]);
      setErr('Provide ?campaign_id=<id> in the URL to view the Kanban.');
      return;
    }
    try {
      const r = await api.get<LanesResponse>(
        `/campaigns/${encodeURIComponent(campaignId)}/lanes?env=${env}`,
      );
      setData(r.lanes as EnrichedSnapshot[]);
      setCounts(r.counts ?? { pending_approvals: 0, open_escalations: 0 });
      setErr(null);
    } catch (ex) {
      setErr(String(ex));
    }
  }, [campaignId, env]);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 10_000);
    return () => clearInterval(t);
  }, [refresh]);

  useEffect(() => {
    localStorage.setItem('kolEnv', env);
  }, [env]);

  const visibleColumns = useMemo(
    () =>
      laneFilter === 'all'
        ? GOAL_COLUMNS
        : GOAL_COLUMNS.filter((c) => c.lane === laneFilter),
    [laneFilter],
  );

  const filtered = useMemo(() => {
    return data.filter((row) => {
      if (repeatOnly && (row.repeat_count || 0) <= 0) return false;
      return true;
    });
  }, [data, repeatOnly]);

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
    const primary = goalNames[goalNames.length - 1] || 'cold_outreach';
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
          KOL Pipeline{' '}
          <span className="ml-2 text-xs text-slate-400">
            ({liveItems.length} live · {doneItems.length} done · {env})
          </span>
        </h1>
        <input
          value={campaignId}
          onChange={(e) => {
            const next = new URLSearchParams(search);
            next.set('campaign_id', e.target.value);
            setSearch(next, { replace: true });
          }}
          placeholder="campaign_id"
          className="rounded border border-slate-300 px-2 py-1 text-sm"
        />
        {/* Top-of-page dual badges: pending approvals + open escalations */}
        <Link
          to="/approvals"
          className="rounded bg-rose-100 px-2 py-0.5 text-xs font-medium text-rose-800 hover:bg-rose-200"
          title="Pending approvals across all campaigns"
        >
          ◷ {counts.pending_approvals} pending
        </Link>
        <Link
          to="/escalations"
          className="rounded bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800 hover:bg-amber-200"
          title="Open escalations for this campaign"
        >
          ! {counts.open_escalations} escalated
        </Link>
        <div className="ml-auto flex items-center gap-2 text-xs">
          <span className="text-slate-500">env:</span>
          <select
            value={env}
            onChange={(e) => setEnv(e.target.value as 'TEST' | 'LIVE')}
            className="rounded border border-slate-300 bg-white px-2 py-0.5"
          >
            <option value="TEST">TEST</option>
            <option value="LIVE">LIVE</option>
          </select>
          <button
            onClick={() => setShowDone((v) => !v)}
            className="rounded border border-slate-300 bg-white px-2 py-0.5 text-slate-600 hover:bg-slate-50"
          >
            {showDone ? 'Hide Done' : `Done (${doneItems.length})`}
          </button>
          <button
            onClick={() => refresh()}
            className="rounded border border-slate-300 bg-white px-2 py-0.5 text-slate-600 hover:bg-slate-50"
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

      {err && <div className="text-red-600">{err}</div>}

      <div className="flex gap-2">
        <div
          className={
            'grid flex-1 gap-2 ' +
            (visibleColumns.length <= 3
              ? 'grid-cols-3'
              : 'grid-cols-3 lg:grid-cols-9')
          }
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
                {(grouped[goal] ?? []).map((k) => {
                  const goalState = k.goals[lane as keyof typeof k.goals];
                  const blocked = !!goalState?.blocked_reason;
                  const missing = goalState?.missing_facts ?? [];
                  return (
                    <li key={k.identity_id} className="rounded border border-slate-100 bg-slate-50 p-2 text-sm">
                      <Link
                        to={`/kols/${k.identity_id}?campaign_id=${encodeURIComponent(campaignId)}`}
                        className={
                          'block font-medium hover:text-emerald-700 ' +
                          (blocked ? 'text-amber-700' : 'text-slate-800')
                        }
                      >
                        @{k.handle}
                        <RepeatKolBadge
                          count={k.repeat_count || 0}
                          lastOutcome={k.last_outcome ?? null}
                        />
                      </Link>
                      {missing.length > 0 && (
                        <div className="mt-1 flex flex-wrap gap-1">
                          {missing.slice(0, 5).map((f) => (
                            <span
                              key={f}
                              className={`rounded px-1.5 py-0.5 text-[10px] ${chipColorFor(f)}`}
                              title={f}
                            >
                              {f}
                            </span>
                          ))}
                          {missing.length > 5 && (
                            <span className="text-[10px] text-slate-500">
                              +{missing.length - 5}
                            </span>
                          )}
                        </div>
                      )}
                      <div className="mt-1.5 flex gap-1.5">
                        <Link
                          to={`/kols/${k.identity_id}?campaign_id=${encodeURIComponent(campaignId)}#facts`}
                          className="rounded border border-emerald-300 bg-emerald-50 px-1.5 py-0.5 text-[10px] font-medium text-emerald-800 hover:bg-emerald-100"
                        >
                          补字段
                        </Link>
                        <Link
                          to={`/escalations?campaign_id=${encodeURIComponent(campaignId)}&identity_id=${k.identity_id}`}
                          className="rounded border border-amber-300 bg-amber-50 px-1.5 py-0.5 text-[10px] font-medium text-amber-800 hover:bg-amber-100"
                        >
                          升级
                        </Link>
                      </div>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </div>

        {showDone && (
          <div className="w-64 rounded border border-slate-200 bg-white p-2">
            <div className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-500">
              Done <span className="text-slate-400">({doneItems.length})</span>
            </div>
            <ul className="space-y-1">
              {doneItems.map((k) => (
                <li key={k.identity_id}>
                  <Link
                    to={`/kols/${k.identity_id}?campaign_id=${encodeURIComponent(campaignId)}`}
                    className="block rounded bg-slate-100 px-2 py-1 text-sm text-slate-600 hover:bg-slate-200"
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
                <li className="text-xs text-slate-400">No archived KOLs.</li>
              )}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}
