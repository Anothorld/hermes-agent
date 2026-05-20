import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';

type Draft = {
  id: number;
  draft_id: string;
  kol_identity_id: number;
  stage: string;
  sub_status: string | null;
  subject: string | null;
  created_at: string;
  gmail_message_id: string | null;
  gmail_thread_id: string | null;
};

const POLL_MS = 10_000;

export function DraftQueuePage() {
  const [items, setItems] = useState<Draft[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [envFilter, setEnvFilter] = useState<'TEST' | 'LIVE'>(
    (localStorage.getItem('draftEnv') as 'TEST' | 'LIVE') || 'TEST'
  );
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);

  const refresh = useCallback(() => {
    api
      .get<Draft[]>(`/drafts/pending?env=${envFilter}`)
      .then((rows) => {
        setItems(rows);
        setErr(null);
        setLastRefresh(new Date());
      })
      .catch((e) => setErr(String(e)));
  }, [envFilter]);

  useEffect(() => {
    localStorage.setItem('draftEnv', envFilter);
    refresh();
    const id = setInterval(refresh, POLL_MS);
    return () => clearInterval(id);
  }, [refresh, envFilter]);

  if (err) return <div className="text-red-600">{err}</div>;
  return (
    <div>
      <div className="mb-3 flex items-center gap-3">
        <h1 className="text-lg font-semibold">
          Pending drafts ({items.length})
        </h1>
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
      <table className="w-full text-sm">
        <thead className="text-left text-xs uppercase text-slate-500">
          <tr>
            <th>stage</th>
            <th>sub</th>
            <th>subject</th>
            <th>kol</th>
            <th>gmail</th>
            <th>created</th>
            <th>draft_id</th>
          </tr>
        </thead>
        <tbody>
          {items.map((d) => (
            <tr key={d.id} className="border-t border-slate-100">
              <td className="font-medium">{d.stage}</td>
              <td className="text-slate-500">{d.sub_status}</td>
              <td>{d.subject}</td>
              <td>
                <Link
                  to={`/kols/${d.kol_identity_id}`}
                  className="text-emerald-700 hover:underline"
                >
                  #{d.kol_identity_id}
                </Link>
              </td>
              <td className="text-xs">
                {d.gmail_message_id ? (
                  <a
                    href={`https://mail.google.com/mail/u/0/#drafts/${d.gmail_message_id}`}
                    target="_blank"
                    rel="noreferrer"
                    className="text-sky-700 hover:underline"
                    title={`Gmail draft ${d.gmail_message_id}`}
                  >
                    ✉ open
                  </a>
                ) : (
                  <span className="italic text-slate-400">stub</span>
                )}
              </td>
              <td className="text-slate-400">
                {d.created_at.slice(0, 16).replace('T', ' ')}
              </td>
              <td className="font-mono text-xs">{d.draft_id}</td>
            </tr>
          ))}
          {items.length === 0 && (
            <tr>
              <td colSpan={7} className="py-6 text-center text-sm text-slate-400">
                No pending drafts in {envFilter}.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
