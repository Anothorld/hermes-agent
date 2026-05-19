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

  useEffect(() => {
    api
      .get<Draft[]>('/drafts/pending')
      .then(setItems)
      .catch((e) => setErr(String(e)));
  }, []);

  if (err) return <div className="text-red-600">{err}</div>;
  return (
    <div>
      <h1 className="mb-3 text-lg font-semibold">Pending drafts ({items.length})</h1>
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
