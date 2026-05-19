import { FormEvent, useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { api } from '../api';

type Product = { sku: string; name: string; url: string | null; tags: string[]; notes: string | null };

export function ProductDetailPage() {
  const { sku } = useParams<{ sku: string }>();
  const [p, setP] = useState<Product | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [form, setForm] = useState({
    campaign_id: '',
    budget_per_kol: '1500',
    absolute_floor: '600',
    budget_total: '12000',
    headcount_target: '8',
    test_mode_to: 'tester@example.com',
    env: 'TEST',
  });
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    if (!sku) return;
    api.get<Product>(`/products/${encodeURIComponent(sku)}`).then(setP).catch((e) => setErr(String(e)));
  }, [sku]);

  const start = async (e: FormEvent) => {
    e.preventDefault();
    setMsg(null);
    setErr(null);
    if (!sku) return;
    try {
      const r = await api.post<{ run_id?: string }>(
        `/campaigns/${encodeURIComponent(form.campaign_id)}/start`,
        {
          product_sku: sku,
          budget_per_kol: Number(form.budget_per_kol),
          absolute_floor: Number(form.absolute_floor),
          budget_total: Number(form.budget_total),
          headcount_target: Number(form.headcount_target),
          test_mode_to: form.test_mode_to,
          env: form.env,
        },
      );
      setMsg(`Started: run_id=${r.run_id ?? '(none)'}`);
    } catch (ex) {
      setErr(String(ex));
    }
  };

  if (err) return <div className="text-red-600">{err}</div>;
  if (!p) return <div>Loading…</div>;

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold">
        {p.sku} — {p.name}
      </h1>
      {p.url && (
        <a href={p.url} target="_blank" rel="noreferrer" className="text-sm text-emerald-700 underline">
          {p.url}
        </a>
      )}
      {p.notes && <p className="text-sm text-slate-600">{p.notes}</p>}

      <section className="rounded border bg-white p-3">
        <h2 className="mb-2 font-medium">Start a new campaign</h2>
        <form onSubmit={start} className="grid grid-cols-2 gap-3 text-sm">
          <label>
            campaign_id
            <input
              required
              value={form.campaign_id}
              onChange={(e) => setForm({ ...form, campaign_id: e.target.value })}
              className="mt-1 block w-full rounded border px-2 py-1"
            />
          </label>
          <label>
            env
            <select
              value={form.env}
              onChange={(e) => setForm({ ...form, env: e.target.value })}
              className="mt-1 block w-full rounded border px-2 py-1"
            >
              <option value="TEST">TEST</option>
              <option value="LIVE">LIVE</option>
            </select>
          </label>
          {(
            [
              ['budget_per_kol', 'Budget per KOL (USD)'],
              ['absolute_floor', 'Absolute floor (USD)'],
              ['budget_total', 'Total budget (USD)'],
              ['headcount_target', 'Headcount target'],
              ['test_mode_to', 'TEST inbox'],
            ] as const
          ).map(([k, label]) => (
            <label key={k}>
              {label}
              <input
                value={form[k as keyof typeof form]}
                onChange={(e) => setForm({ ...form, [k]: e.target.value })}
                className="mt-1 block w-full rounded border px-2 py-1"
              />
            </label>
          ))}
          <button className="col-span-2 rounded bg-emerald-600 px-3 py-1 text-white">Start</button>
        </form>
        {msg && <div className="mt-2 text-sm text-emerald-700">{msg}</div>}
      </section>
    </div>
  );
}
