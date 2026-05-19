import { FormEvent, useState } from 'react';
import { useParams } from 'react-router-dom';
import { api } from '../api';

const SUB_STATUSES = ['pending', 'sent_for_signature', 'signed', 'declined'] as const;

export function ContractStubPage() {
  const { id } = useParams<{ id: string }>();
  const [sub, setSub] = useState<(typeof SUB_STATUSES)[number]>('sent_for_signature');
  const [note, setNote] = useState('');
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setMsg(null);
    setErr(null);
    try {
      await api.post('/contract/update', {
        kol_identity_id: Number(id),
        sub_status: sub,
        note: note || null,
      });
      setMsg('Updated.');
      setNote('');
    } catch (ex) {
      setErr(String(ex));
    }
  };

  return (
    <div className="max-w-md">
      <h1 className="mb-3 text-lg font-semibold">Contract — KOL #{id}</h1>
      <p className="mb-3 text-sm text-slate-500">
        Stub workflow: handle the contract in your usual channel, then advance the sub-status
        here. The agent does not email or e-sign.
      </p>
      <form onSubmit={submit} className="space-y-3">
        <label className="block text-sm">
          Sub-status
          <select
            value={sub}
            onChange={(e) => setSub(e.target.value as (typeof SUB_STATUSES)[number])}
            className="mt-1 block w-full rounded border px-2 py-1"
          >
            {SUB_STATUSES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <label className="block text-sm">
          Note (optional)
          <textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            rows={3}
            className="mt-1 block w-full rounded border px-2 py-1"
          />
        </label>
        <button className="rounded bg-emerald-600 px-3 py-1 text-white">Push update</button>
        {msg && <div className="text-sm text-emerald-700">{msg}</div>}
        {err && <div className="text-sm text-red-600">{err}</div>}
      </form>
    </div>
  );
}
