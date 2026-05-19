import { FormEvent, useState } from 'react';
import { useParams } from 'react-router-dom';
import { api } from '../api';

const SUB_STATUSES = [
  'pending',
  'address_collected',
  'tracking_filled',
  'in_transit',
  'delivered',
] as const;

export function LogisticsStubPage() {
  const { id } = useParams<{ id: string }>();
  const [form, setForm] = useState({
    sub_status: 'address_collected' as (typeof SUB_STATUSES)[number],
    address: '',
    carrier: '',
    tracking_no: '',
    shipped_at: '',
    delivered_at: '',
  });
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const upd = (k: keyof typeof form, v: string) => setForm({ ...form, [k]: v });

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setMsg(null);
    setErr(null);
    try {
      await api.post('/logistics/update', {
        kol_identity_id: Number(id),
        ...form,
        address: form.address || null,
        carrier: form.carrier || null,
        tracking_no: form.tracking_no || null,
        shipped_at: form.shipped_at || null,
        delivered_at: form.delivered_at || null,
      });
      setMsg('Updated.');
    } catch (ex) {
      setErr(String(ex));
    }
  };

  return (
    <div className="max-w-md">
      <h1 className="mb-3 text-lg font-semibold">Logistics — KOL #{id}</h1>
      <p className="mb-3 text-sm text-slate-500">
        Stub workflow: arrange shipping in your usual channel, record the details here.
      </p>
      <form onSubmit={submit} className="space-y-3">
        <label className="block text-sm">
          Sub-status
          <select
            value={form.sub_status}
            onChange={(e) => upd('sub_status', e.target.value)}
            className="mt-1 block w-full rounded border px-2 py-1"
          >
            {SUB_STATUSES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        {(
          [
            ['address', 'Shipping address'],
            ['carrier', 'Carrier'],
            ['tracking_no', 'Tracking number'],
            ['shipped_at', 'Shipped at (ISO8601)'],
            ['delivered_at', 'Delivered at (ISO8601)'],
          ] as const
        ).map(([k, label]) => (
          <label key={k} className="block text-sm">
            {label}
            <input
              value={form[k]}
              onChange={(e) => upd(k, e.target.value)}
              className="mt-1 block w-full rounded border px-2 py-1"
            />
          </label>
        ))}
        <button className="rounded bg-emerald-600 px-3 py-1 text-white">Push update</button>
        {msg && <div className="text-sm text-emerald-700">{msg}</div>}
        {err && <div className="text-sm text-red-600">{err}</div>}
      </form>
    </div>
  );
}
