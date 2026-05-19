import { FormEvent, useEffect, useState } from 'react';
import { api } from '../api';

type Me = { id: number; email: string; role: string };

export function SettingsPage() {
  const [me, setMe] = useState<Me | null>(null);
  const [form, setForm] = useState({ email: '', password: '', role: 'operator' });
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.get<Me>('/auth/me').then(setMe).catch(() => undefined);
  }, []);

  const create = async (e: FormEvent) => {
    e.preventDefault();
    setMsg(null);
    setErr(null);
    try {
      await api.post('/auth/users', form);
      setMsg(`Created ${form.email}.`);
      setForm({ email: '', password: '', role: 'operator' });
    } catch (ex) {
      setErr(String(ex));
    }
  };

  const wipe = async () => {
    if (!confirm('Wipe ALL TEST env data from CAL? This is irreversible.')) return;
    try {
      const r = await api.post<Record<string, number>>('/admin/wipe-test');
      setMsg(`Wiped: ${JSON.stringify(r)}`);
    } catch (ex) {
      setErr(String(ex));
    }
  };

  return (
    <div className="space-y-6">
      <section className="rounded border bg-white p-3">
        <h2 className="mb-2 font-medium">Signed in as</h2>
        {me ? (
          <div className="text-sm">
            {me.email} · role={me.role}
          </div>
        ) : (
          <div className="text-sm text-slate-500">Loading…</div>
        )}
      </section>

      {me?.role === 'owner' && (
        <>
          <section className="rounded border bg-white p-3">
            <h2 className="mb-2 font-medium">Create user</h2>
            <form onSubmit={create} className="grid grid-cols-4 gap-2 text-sm">
              <input
                placeholder="email"
                type="email"
                value={form.email}
                onChange={(e) => setForm({ ...form, email: e.target.value })}
                className="rounded border px-2 py-1"
                required
              />
              <input
                placeholder="password (≥8)"
                type="password"
                value={form.password}
                onChange={(e) => setForm({ ...form, password: e.target.value })}
                className="rounded border px-2 py-1"
                required
              />
              <select
                value={form.role}
                onChange={(e) => setForm({ ...form, role: e.target.value })}
                className="rounded border px-2 py-1"
              >
                <option value="owner">owner</option>
                <option value="operator">operator</option>
                <option value="viewer">viewer</option>
              </select>
              <button className="rounded bg-emerald-600 px-3 py-1 text-white">Create</button>
            </form>
          </section>

          <section className="rounded border border-red-200 bg-red-50 p-3">
            <h2 className="mb-2 font-medium text-red-800">Danger zone</h2>
            <button onClick={wipe} className="rounded bg-red-600 px-3 py-1 text-white">
              Wipe TEST env from CAL
            </button>
          </section>
        </>
      )}
      {msg && <div className="text-sm text-emerald-700">{msg}</div>}
      {err && <div className="text-sm text-red-600">{err}</div>}
    </div>
  );
}
