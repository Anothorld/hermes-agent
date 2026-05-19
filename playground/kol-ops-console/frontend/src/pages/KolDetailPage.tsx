import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api } from '../api';
import { StageProgressBar } from '../components/StageProgressBar';

type TimelineEvent = {
  id: number;
  event_type: string;
  stage: string | null;
  sub_status: string | null;
  ts: string;
  actor: string;
  payload_json: string;
};

type Draft = {
  id: number;
  draft_id: string;
  stage: string;
  sub_status: string | null;
  subject: string | null;
  body: string | null;
  context_snapshot_json: string;
  created_at: string;
  sent_at: string | null;
};

type Reply = {
  id: number;
  gmail_message_id: string;
  received_at: string;
  intent: string | null;
  confidence: number | null;
  match_strategy: string;
  match_confidence: number;
  snippet: string | null;
};

type Negotiation = {
  seq: number;
  decision: string;
  kol_request_amount: number | null;
  agent_counter_amount: number | null;
  budget_per_kol_at_time: number | null;
  absolute_floor_at_time: number | null;
  decided_at: string;
};

type Identity = {
  id: number;
  handle: string;
  primary_email: string | null;
  region: string | null;
  creator_type: string | null;
  env: string;
  notes?: Array<{ id: number; body: string; created_at: string }>;
};

type Timeline = {
  events: TimelineEvent[];
  drafts: Draft[];
  replies: Reply[];
  negotiations: Negotiation[];
  escalations: Array<{ id: number; reason: string; ts: string }>;
};

export function KolDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [tl, setTl] = useState<Timeline | null>(null);
  const [selectedDraft, setSelectedDraft] = useState<Draft | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    Promise.all([api.get<Identity>(`/kols/${id}`), api.get<Timeline>(`/kols/${id}/timeline`)])
      .then(([i, t]) => {
        setIdentity(i);
        setTl(t);
      })
      .catch((e) => setErr(String(e)));
  }, [id]);

  if (err) return <div className="text-red-600">{err}</div>;
  if (!identity || !tl) return <div>Loading…</div>;

  const stage = tl.events.slice().reverse().find((e) => e.stage)?.stage || 'discovered';

  return (
    <div className="grid grid-cols-3 gap-4">
      <div className="col-span-2 space-y-4">
        <div className="flex items-center gap-3">
          <h1 className="text-xl font-semibold">@{identity.handle}</h1>
          <span className="text-sm text-slate-500">
            {identity.creator_type} · {identity.region} · env={identity.env}
          </span>
          <Link
            to={`/kols/${id}/contract`}
            className="ml-auto rounded bg-slate-100 px-3 py-1 text-sm hover:bg-slate-200"
          >
            Contract
          </Link>
          <Link
            to={`/kols/${id}/logistics`}
            className="rounded bg-slate-100 px-3 py-1 text-sm hover:bg-slate-200"
          >
            Logistics
          </Link>
        </div>
        <StageProgressBar stage={stage} />

        <section className="rounded border border-slate-200 bg-white p-3">
          <h2 className="mb-2 font-medium">Timeline</h2>
          <ul className="space-y-1 text-sm">
            {tl.events.map((e) => (
              <li key={e.id} className="flex gap-2 border-b border-slate-100 py-1">
                <span className="text-slate-400">{e.ts.slice(0, 16).replace('T', ' ')}</span>
                <span className="font-medium">{e.event_type}</span>
                {e.stage && (
                  <span className="text-emerald-700">
                    {e.stage}/{e.sub_status ?? '-'}
                  </span>
                )}
                <span className="ml-auto text-xs text-slate-500">{e.actor}</span>
              </li>
            ))}
          </ul>
        </section>

        <section className="rounded border border-slate-200 bg-white p-3">
          <h2 className="mb-2 font-medium">Drafts</h2>
          <ul className="space-y-1 text-sm">
            {tl.drafts.map((d) => (
              <li
                key={d.id}
                onClick={() => setSelectedDraft(d)}
                className={
                  'cursor-pointer rounded px-2 py-1 ' +
                  (selectedDraft?.id === d.id ? 'bg-emerald-100' : 'hover:bg-slate-50')
                }
              >
                <span className="font-medium">{d.stage}</span>{' '}
                <span className="text-slate-500">{d.sub_status}</span> ·{' '}
                <span className="text-slate-400">{d.created_at.slice(0, 16).replace('T', ' ')}</span>
                {d.sent_at ? (
                  <span className="ml-2 text-xs text-emerald-700">sent</span>
                ) : (
                  <span className="ml-2 text-xs text-amber-600">pending</span>
                )}
              </li>
            ))}
          </ul>
        </section>

        <section className="rounded border border-slate-200 bg-white p-3">
          <h2 className="mb-2 font-medium">Replies</h2>
          <ul className="space-y-1 text-sm">
            {tl.replies.map((r) => (
              <li key={r.id} className="border-b border-slate-100 py-1">
                <span className="text-slate-400">{r.received_at.slice(0, 16).replace('T', ' ')}</span>{' '}
                <span className="font-medium">{r.intent ?? '?'}</span>{' '}
                <span className="text-xs text-slate-500">
                  conf={r.confidence?.toFixed(2)} match={r.match_strategy}/
                  {r.match_confidence.toFixed(2)}
                </span>
                {r.snippet && <div className="text-slate-600">{r.snippet}</div>}
              </li>
            ))}
          </ul>
        </section>

        <section className="rounded border border-slate-200 bg-white p-3">
          <h2 className="mb-2 font-medium">Negotiation history</h2>
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-slate-500">
              <tr>
                <th>#</th>
                <th>decision</th>
                <th>ask</th>
                <th>counter</th>
                <th>budget</th>
                <th>floor</th>
                <th>at</th>
              </tr>
            </thead>
            <tbody>
              {tl.negotiations.map((n) => (
                <tr key={n.seq} className="border-t border-slate-100">
                  <td>{n.seq}</td>
                  <td className="font-medium">{n.decision}</td>
                  <td>{n.kol_request_amount ?? '-'}</td>
                  <td>{n.agent_counter_amount ?? '-'}</td>
                  <td>{n.budget_per_kol_at_time ?? '-'}</td>
                  <td>{n.absolute_floor_at_time ?? '-'}</td>
                  <td className="text-slate-400">{n.decided_at.slice(0, 16).replace('T', ' ')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      </div>

      <aside className="space-y-3">
        <section className="rounded border border-slate-200 bg-white p-3">
          <h2 className="mb-2 font-medium">Generation rationale</h2>
          {selectedDraft ? (
            <DraftRationale draft={selectedDraft} />
          ) : (
            <p className="text-sm text-slate-500">Select a draft on the left.</p>
          )}
        </section>
      </aside>
    </div>
  );
}

function DraftRationale({ draft }: { draft: Draft }) {
  let ctx: Record<string, unknown> = {};
  try {
    ctx = JSON.parse(draft.context_snapshot_json);
  } catch {
    /* ignore */
  }
  return (
    <div className="space-y-2 text-sm">
      <div>
        <div className="text-xs uppercase text-slate-500">draft_id</div>
        <div className="font-mono">{draft.draft_id}</div>
      </div>
      <div>
        <div className="text-xs uppercase text-slate-500">subject</div>
        <div>{draft.subject}</div>
      </div>
      <details>
        <summary className="cursor-pointer text-xs uppercase text-slate-500">body</summary>
        <pre className="whitespace-pre-wrap rounded bg-slate-50 p-2 text-xs">{draft.body}</pre>
      </details>
      <div>
        <div className="text-xs uppercase text-slate-500">context</div>
        <pre className="whitespace-pre-wrap rounded bg-slate-50 p-2 text-xs">
          {JSON.stringify(ctx, null, 2)}
        </pre>
      </div>
    </div>
  );
}
