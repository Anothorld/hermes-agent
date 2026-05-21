import { useCallback, useEffect, useState } from 'react';
import { api } from '../api';
import { useLiveEvents } from '../useLiveEvents';

type EventRow = {
  id: number;
  ts: string;
  event_type: string;
  kol_identity_id: number;
  stage: string | null;
  sub_status: string | null;
  actor: string;
  env?: string;
};

type Escalation = {
  id: number;
  reason: string;
  ts: string;
  kol_identity_id?: number | null;
  campaign_id?: string | null;
  classifier_confidence?: number | null;
  ai_recommendation?: string | null;
  env?: string | null;
};

type NextReplyType =
  | 'brief_clarification'
  | 'negotiation'
  | 'product_pitch'
  | 'content_followup'
  | 'close_no_reply';

const NEXT_REPLY_OPTIONS: { value: NextReplyType; label: string }[] = [
  { value: 'brief_clarification', label: 'Brief / budget clarification' },
  { value: 'negotiation', label: 'Negotiation' },
  { value: 'product_pitch', label: 'Product pitch' },
  { value: 'content_followup', label: 'Content follow-up' },
  { value: 'close_no_reply', label: 'Close / no reply' },
];

const POLL_MS = 10_000;

export function ReplyMonitorPage() {
  const [events, setEvents] = useState<EventRow[]>([]);
  const [escs, setEscs] = useState<Escalation[]>([]);
  const [envFilter, setEnvFilter] = useState<'TEST' | 'LIVE'>(
    (localStorage.getItem('replyEnv') as 'TEST' | 'LIVE') ||
      (localStorage.getItem('kolEnv') as 'TEST' | 'LIVE') ||
      'TEST',
  );
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  const [nextTypes, setNextTypes] = useState<Record<number, NextReplyType>>({});
  const [notes, setNotes] = useState<Record<number, string>>({});
  const [busyEscalation, setBusyEscalation] = useState<number | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    api
      .get<EventRow[]>(`/events/recent?env=${envFilter}&limit=100`)
      .catch(() => [])
      .then((rows) => {
        setEvents(rows ?? []);
        setLastRefresh(new Date());
      });
    api
      .get<Escalation[]>(`/escalations/open?env=${envFilter}`)
      .catch(() => [])
      .then((rows) => setEscs(rows ?? []));
  }, [envFilter]);

  useEffect(() => {
    localStorage.setItem('replyEnv', envFilter);
    refresh();
    const id = setInterval(refresh, POLL_MS);
    return () => clearInterval(id);
  }, [refresh, envFilter]);

  useLiveEvents((evt) => {
    if (evt.type !== 'events') return;
    setEvents((prev) =>
      [
        ...evt.items
          .map((e) => e as EventRow)
          .filter((e) => !e.env || e.env === envFilter),
        ...prev,
      ].slice(0, 200),
    );
  });

  const submitNextAction = async (escalation: Escalation) => {
    const nextReplyType = nextTypes[escalation.id] || 'brief_clarification';
    setBusyEscalation(escalation.id);
    setActionError(null);
    try {
      await api.post(`/escalations/${escalation.id}/next-action`, {
        next_reply_type: nextReplyType,
        human_note: notes[escalation.id] || undefined,
        env: envFilter,
      });
      setNotes((prev) => {
        const next = { ...prev };
        delete next[escalation.id];
        return next;
      });
      refresh();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Failed to record action');
    } finally {
      setBusyEscalation(null);
    }
  };

  return (
    <div className="grid grid-cols-3 gap-4">
      <section className="col-span-2 rounded border bg-white p-3">
        <div className="mb-2 flex items-center gap-3">
          <h2 className="font-medium">Live event feed ({events.length})</h2>
          <span className="text-xs text-slate-500">env:</span>
          <select
            value={envFilter}
            onChange={(e) => setEnvFilter(e.target.value as 'TEST' | 'LIVE')}
            className="rounded border px-2 py-0.5 text-xs"
          >
            <option value="TEST">TEST</option>
            <option value="LIVE">LIVE</option>
          </select>
          <button
            type="button"
            onClick={refresh}
            className="rounded border border-slate-300 px-2 py-0.5 text-xs text-slate-600 hover:bg-slate-50"
            title="Refresh now (auto-refreshes every 10s)"
          >
            ↻
          </button>
          {lastRefresh && (
            <span className="text-xs text-slate-400">
              last refresh {lastRefresh.toLocaleTimeString()}
            </span>
          )}
        </div>
        <ul className="space-y-1 text-sm">
          {events.map((e) => (
            <li key={e.id} className="flex gap-2 border-b border-slate-100 py-1">
              <span className="text-slate-400">{e.ts.slice(0, 19).replace('T', ' ')}</span>
              <span className="font-medium">{e.event_type}</span>
              <span className="text-emerald-700">
                {e.stage}/{e.sub_status}
              </span>
              <span className="ml-auto text-xs text-slate-500">
                kol #{e.kol_identity_id} · {e.actor}
              </span>
            </li>
          ))}
        </ul>
      </section>
      <aside className="rounded border bg-white p-3">
        <h2 className="mb-2 font-medium">Open escalations ({escs.length})</h2>
        {actionError && (
          <div className="mb-2 rounded border border-red-200 bg-red-50 px-2 py-1 text-xs text-red-700">
            {actionError}
          </div>
        )}
        <ul className="space-y-3 text-sm">
          {escs.map((e) => (
            <li key={e.id} className="border-b border-slate-100 pb-3">
              <div className="flex items-start justify-between gap-2">
                <div>
                  <div className="font-medium">{e.reason}</div>
                  <div className="text-xs text-slate-400">
                    {e.ts.slice(0, 19).replace('T', ' ')}
                  </div>
                </div>
                {e.kol_identity_id && (
                  <a
                    href={`/kols/${e.kol_identity_id}`}
                    className="shrink-0 rounded border border-slate-300 px-2 py-0.5 text-xs text-slate-600 hover:bg-slate-50"
                  >
                    KOL #{e.kol_identity_id}
                  </a>
                )}
              </div>
              <div className="mt-1 space-y-1 text-xs text-slate-500">
                {e.campaign_id && <div>campaign: {e.campaign_id}</div>}
                {typeof e.classifier_confidence === 'number' && (
                  <div>confidence: {(e.classifier_confidence * 100).toFixed(0)}%</div>
                )}
                {e.ai_recommendation && <div className="text-slate-700">{e.ai_recommendation}</div>}
              </div>
              <div className="mt-2 grid gap-2">
                <select
                  value={nextTypes[e.id] || 'brief_clarification'}
                  onChange={(evt) =>
                    setNextTypes((prev) => ({
                      ...prev,
                      [e.id]: evt.target.value as NextReplyType,
                    }))
                  }
                  className="rounded border px-2 py-1 text-xs"
                >
                  {NEXT_REPLY_OPTIONS.map((opt) => (
                    <option key={opt.value} value={opt.value}>
                      {opt.label}
                    </option>
                  ))}
                </select>
                <input
                  value={notes[e.id] || ''}
                  onChange={(evt) =>
                    setNotes((prev) => ({ ...prev, [e.id]: evt.target.value }))
                  }
                  className="rounded border px-2 py-1 text-xs"
                  placeholder="Operator note"
                />
                <button
                  type="button"
                  onClick={() => submitNextAction(e)}
                  disabled={busyEscalation === e.id}
                  className="rounded bg-slate-900 px-2 py-1 text-xs font-medium text-white disabled:opacity-50"
                >
                  {busyEscalation === e.id ? 'Recording...' : 'Record next action'}
                </button>
              </div>
            </li>
          ))}
          {!escs.length && <li className="text-xs text-slate-400">No open escalations.</li>}
        </ul>
      </aside>
    </div>
  );
}
