import { useEffect, useState } from 'react';
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
};

type Escalation = { id: number; reason: string; ts: string };

export function ReplyMonitorPage() {
  const [events, setEvents] = useState<EventRow[]>([]);
  const [escs, setEscs] = useState<Escalation[]>([]);

  useEffect(() => {
    api.get<EventRow[]>('/events/recent?limit=100').catch(() => []).then((rows) => setEvents(rows ?? []));
    api.get<Escalation[]>('/escalations/open').catch(() => []).then((rows) => setEscs(rows ?? []));
  }, []);

  useLiveEvents((evt) => {
    if (evt.type !== 'events') return;
    setEvents((prev) => [...evt.items.map((e) => e as EventRow), ...prev].slice(0, 200));
  });

  return (
    <div className="grid grid-cols-3 gap-4">
      <section className="col-span-2 rounded border bg-white p-3">
        <h2 className="mb-2 font-medium">Live event feed</h2>
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
        <h2 className="mb-2 font-medium">Open escalations</h2>
        <ul className="space-y-1 text-sm">
          {escs.map((e) => (
            <li key={e.id} className="border-b border-slate-100 py-1">
              <div className="font-medium">{e.reason}</div>
              <div className="text-xs text-slate-400">{e.ts.slice(0, 19).replace('T', ' ')}</div>
            </li>
          ))}
        </ul>
      </aside>
    </div>
  );
}
