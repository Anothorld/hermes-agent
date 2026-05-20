import { useEffect, useState } from 'react';
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
};

export function DraftQueuePage() {
  const [items, setItems] = useState<Draft[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [envFilter, setEnvFilter] = useState<'TEST' | 'LIVE'>('TEST');

  useEffect(() => {
    api
      .get<Draft[]>(`/drafts/pending?env=${envFilter}`)
      .then(setItems)
      .catch((e) => setErr(String(e)));
  }, [envFilter]);

  if (err) return <div className="text-red-600">{err}</div>;
  return (
    <div>
      <div className="mb-3 flex items-center gap-2">
        <h1 className="text-lg font-semibold">Pending drafts ({items.length})</h1>
        <select
          value={envFilter}
          onChange={(e) => setEnvFilter(e.target.value as 'TEST' | 'LIVE')}
          className="rounded border px-2 py-0.5 text-xs"
        >
          <option value="TEST">TEST</option>
          <option value="LIVE">LIVE</option>
        </select>
      </div>
      <table className="w-full text-sm">
        <thead className="text-left text-xs uppercase text-slate-500">
          <tr>
            <th>stage</th>
            <th>sub</th>
            <th>subject</th>
            <th>kol</th>
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
                <Link to={`/kols/${d.kol_identity_id}`} className="text-emerald-700 hover:underline">
                  #{d.kol_identity_id}
                </Link>
              </td>
              <td className="text-slate-400">
                {d.created_at.slice(0, 16).replace('T', ' ')}
              </td>
              <td className="font-mono text-xs">{d.draft_id}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
