import { FormEvent, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';

type Product = { sku: string; name: string; url: string | null; tags: string[]; notes: string | null };

export function ProductListPage() {
  const [items, setItems] = useState<Product[]>([]);
  const [draft, setDraft] = useState({ sku: '', name: '', url: '', tags: '', notes: '' });
  const [err, setErr] = useState<string | null>(null);

  const refresh = () =>
    api.get<Product[]>('/products').then(setItems).catch((e) => setErr(String(e)));

  useEffect(() => {
    refresh();
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
            <Link to={`/products/${encodeURIComponent(p.sku)}`} className="font-medium hover:text-emerald-700">
              {p.sku} — {p.name}
            </Link>
            {p.tags.length > 0 && (
              <span className="ml-2 text-xs text-slate-500">[{p.tags.join(', ')}]</span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
