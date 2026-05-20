import { FormEvent, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';

type ProductSummary = {
  sku: string;
  name: string;
  url: string | null;
  tags: string[];
  notes: string | null;
  campaigns_total: number;
  campaigns_running: number;
  active_campaign_ids: string[];
  stage: string | null;
  sub_status: string | null;
  last_event_type: string | null;
  last_event_ts: string | null;
  kols_contacted: number;
};

function StatusBadges({ p }: { p: ProductSummary }) {
  if (p.campaigns_total === 0) {
    return <span className="ml-2 rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-500">idle</span>;
  }
  return (
    <span className="ml-2 inline-flex flex-wrap items-center gap-1 text-xs">
      {p.campaigns_running > 0 ? (
        <span className="rounded bg-emerald-100 px-2 py-0.5 text-emerald-800">
          running × {p.campaigns_running}
        </span>
      ) : (
        <span className="rounded bg-slate-200 px-2 py-0.5 text-slate-700">no active run</span>
      )}
      {p.stage && (
        <span className="rounded bg-sky-100 px-2 py-0.5 text-sky-800">stage: {p.stage}</span>
      )}
      {p.kols_contacted > 0 && (
        <span className="rounded bg-violet-100 px-2 py-0.5 text-violet-800">
          KOLs · {p.kols_contacted}
        </span>
      )}
      {p.last_event_ts && (
        <span className="text-slate-400">last: {p.last_event_ts.replace('T', ' ').slice(0, 19)}</span>
      )}
    </span>
  );
}

export function ProductListPage() {
  const [items, setItems] = useState<ProductSummary[]>([]);
  const [draft, setDraft] = useState({ sku: '', name: '', url: '', tags: '', notes: '' });
  const [err, setErr] = useState<string | null>(null);

  const refresh = () =>
    api
      .get<ProductSummary[]>('/products/summary')
      .then(setItems)
      .catch((e) => setErr(String(e)));

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 15_000);
    return () => clearInterval(t);
  }, []);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setErr(null);
    try {
      await api.post('/products', {
        sku: draft.sku.trim(),
        name: draft.name.trim(),
        url: draft.url || null,
        tags: draft.tags.split(',').map((t) => t.trim()).filter(Boolean),
        notes: draft.notes || null,
      });
      setDraft({ sku: '', name: '', url: '', tags: '', notes: '' });
      refresh();
    } catch (ex) {
      setErr(String(ex));
    }
  };

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold">Products (SKU catalog)</h1>
      <form onSubmit={submit} className="grid grid-cols-5 gap-2 rounded border bg-white p-3 text-sm">
        <input
          placeholder="SKU"
          value={draft.sku}
          onChange={(e) => setDraft({ ...draft, sku: e.target.value })}
          className="rounded border px-2 py-1"
          required
        />
        <input
          placeholder="Name"
          value={draft.name}
          onChange={(e) => setDraft({ ...draft, name: e.target.value })}
          className="rounded border px-2 py-1"
          required
        />
        <input
          placeholder="URL"
          value={draft.url}
          onChange={(e) => setDraft({ ...draft, url: e.target.value })}
          className="rounded border px-2 py-1"
        />
        <input
          placeholder="tags (comma)"
          value={draft.tags}
          onChange={(e) => setDraft({ ...draft, tags: e.target.value })}
          className="rounded border px-2 py-1"
        />
        <button className="rounded bg-emerald-600 px-3 py-1 text-white">Save</button>
      </form>
      {err && <div className="text-sm text-red-600">{err}</div>}
      <ul className="divide-y rounded border bg-white">
        {items.map((p) => (
          <li key={p.sku} className="px-3 py-2">
            <div className="flex flex-wrap items-center">
              <Link
                to={`/products/${encodeURIComponent(p.sku)}`}
                className="font-medium hover:text-emerald-700"
              >
                {p.sku} — {p.name}
              </Link>
              {p.tags.length > 0 && (
                <span className="ml-2 text-xs text-slate-500">[{p.tags.join(', ')}]</span>
              )}
              <StatusBadges p={p} />
            </div>
            {p.active_campaign_ids.length > 0 && (
              <div className="mt-1 text-xs text-slate-500">
                active campaigns: {p.active_campaign_ids.join(', ')}
              </div>
            )}
          </li>
        ))}
        {items.length === 0 && (
          <li className="px-3 py-4 text-sm text-slate-500">No products yet.</li>
        )}
      </ul>
    </div>
  );
}
