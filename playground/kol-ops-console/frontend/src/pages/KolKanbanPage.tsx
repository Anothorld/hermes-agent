import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import { useLiveEvents } from '../useLiveEvents';
import { STAGES, Stage } from '../components/StageProgressBar';

type Identity = {
  id: number;
  handle: string;
  primary_email: string | null;
  creator_type: string | null;
  env: string;
  updated_at: string;
};

type TimelineEvent = {
  kol_identity_id: number;
  stage: string | null;
};

export function KolKanbanPage() {
  const [identities, setIdentities] = useState<Identity[]>([]);
  const [stageByKol, setStageByKol] = useState<Record<number, string>>({});
  const [err, setErr] = useState<string | null>(null);
  const [env, setEnv] = useState<'TEST' | 'LIVE'>(() => {
    const stored = localStorage.getItem('kolEnv');
    return stored === 'LIVE' ? 'LIVE' : 'TEST';
  });

  const refresh = useCallback(async () => {
    try {
      const items = await api.get<Identity[]>(`/kols?env=${env}`);
      setIdentities(items);
      // Best-effort: derive each KOL's current stage from latest event.
      const stages: Record<number, string> = {};
      await Promise.all(
        items.map(async (i) => {
          try {
            const tl = await api.get<{ events: Array<{ stage: string | null }> }>(
              `/kols/${i.id}/timeline?env=${env}`,
            );
            const last = [...tl.events].reverse().find((e) => e.stage);
            if (last?.stage) stages[i.id] = last.stage;
          } catch {
            /* ignore per-row failures */
          }
        }),
      );
      setStageByKol(stages);
    } catch (ex) {
      setErr(String(ex));
    }
  }, [env]);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 10_000);
    return () => clearInterval(t);
  }, [refresh]);

  useEffect(() => {
    localStorage.setItem('kolEnv', env);
  }, [env]);

  useLiveEvents((evt) => {
    if (evt.type !== 'events') return;
    setStageByKol((prev) => {
      const next = { ...prev };
      for (const e of evt.items as TimelineEvent[]) {
        if (e.stage) next[e.kol_identity_id] = e.stage;
      }
      return next;
    });
  });

  const columns: Record<Stage, Identity[]> = Object.fromEntries(
    STAGES.map((s) => [s, [] as Identity[]]),
  ) as Record<Stage, Identity[]>;
  for (const i of identities) {
    const s = (stageByKol[i.id] || 'discovered') as Stage;
    if (columns[s]) columns[s].push(i);
    else columns.discovered.push(i);
  }

  if (err) return <div className="text-red-600">{err}</div>;

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">
          KOL Pipeline <span className="ml-2 text-xs text-slate-400">({identities.length} in {env})</span>
        </h1>
        <div className="flex items-center gap-2 text-xs">
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
            title="Refresh now (auto-refreshes every 10s)"
          >
            ↻
          </button>
        </div>
      </div>
      <div className="grid grid-cols-8 gap-2">
        {STAGES.map((s) => (
          <div key={s} className="rounded border border-slate-200 bg-white p-2">
            <div className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-500">
              {s} <span className="text-slate-400">({columns[s].length})</span>
            </div>
            <ul className="space-y-1">
              {columns[s].map((k) => (
                <li key={k.id}>
                  <Link
                    to={`/kols/${k.id}`}
                    className="block rounded bg-slate-50 px-2 py-1 text-sm hover:bg-emerald-50"
                  >
                    @{k.handle}
                    {k.creator_type && (
                      <span className="ml-1 text-xs text-slate-500">({k.creator_type})</span>
                    )}
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </div>
  );
}
