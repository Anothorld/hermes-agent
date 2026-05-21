import { useCallback, useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api, LaneSnapshot } from '../api';
import { GOAL_COLUMNS } from '../components/GoalProgressBar';
import { RepeatKolBadge } from '../components/RepeatKolBadge';

type LanesResponse = { campaign_id: string; lanes: LaneSnapshot[] };

export function KolKanbanPage() {
  const [search, setSearch] = useSearchParams();
  const campaignId = search.get('campaign_id') || '';
  const [data, setData] = useState<LaneSnapshot[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [env, setEnv] = useState<'TEST' | 'LIVE'>(() => {
    const stored = localStorage.getItem('kolEnv');
    return stored === 'LIVE' ? 'LIVE' : 'TEST';
  });

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
      setData(r.lanes);
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

  const grouped: Record<string, LaneSnapshot[]> = Object.fromEntries(
    GOAL_COLUMNS.map((c) => [c.goal, [] as LaneSnapshot[]]),
  );
  for (const row of data) {
    const goalNames = [
      row.goals.commerce?.goal,
      row.goals.fulfillment?.goal,
      row.goals.publish?.goal,
    ].filter(Boolean) as string[];
    const primary = goalNames[goalNames.length - 1] || 'cold_outreach';
    if (grouped[primary]) grouped[primary].push(row);
    else grouped['cold_outreach'].push(row);
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="text-lg font-semibold">
          KOL Pipeline{' '}
          <span className="ml-2 text-xs text-slate-400">
            ({data.length} in {env})
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
            onClick={() => refresh()}
            className="rounded border border-slate-300 bg-white px-2 py-0.5 text-slate-600 hover:bg-slate-50"
          >
            ↻
          </button>
        </div>
      </div>
      {err && <div className="text-red-600">{err}</div>}
      <div className="grid grid-cols-3 gap-2 lg:grid-cols-9">
        {GOAL_COLUMNS.map(({ goal, label, lane }) => (
          <div key={goal} className="rounded border border-slate-200 bg-white p-2">
            <div className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-500">
              {label}{' '}
              <span className="text-slate-400">
                ({grouped[goal].length} · {lane})
              </span>
            </div>
            <ul className="space-y-1">
              {grouped[goal].map((k) => {
                const blocked = !!k.goals[
                  lane as keyof typeof k.goals
                ]?.blocked_reason;
                return (
                  <li key={k.identity_id}>
                    <Link
                      to={`/kols/${k.identity_id}?campaign_id=${encodeURIComponent(
                        campaignId,
                      )}`}
                      className={
                        'block rounded px-2 py-1 text-sm hover:bg-emerald-50 ' +
                        (blocked ? 'bg-amber-50' : 'bg-slate-50')
                      }
                      title={blocked ? 'Blocked / escalated' : undefined}
                    >
                      @{k.handle}
                      <RepeatKolBadge
                        count={k.repeat_count || 0}
                        lastOutcome={k.last_outcome ?? null}
                      />
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </div>
    </div>
  );
}
