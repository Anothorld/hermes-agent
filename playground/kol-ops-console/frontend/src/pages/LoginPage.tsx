import { FormEvent, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { api, setToken } from '../api';

export function LoginPage() {
  const nav = useNavigate();
  const loc = useLocation() as { state?: { from?: { pathname?: string } } };
  const [email, setEmail] = useState('owner@console.app');
  const [password, setPassword] = useState('');
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const r = await api.post<{ access_token: string }>('/auth/login', { email, password });
      setToken(r.access_token);
      nav(loc.state?.from?.pathname || '/', { replace: true });
    } catch (ex) {
      setErr(String(ex));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto mt-20 max-w-sm rounded-lg bg-white p-6 shadow">
      <h1 className="mb-4 text-xl font-semibold">KOL Ops Console</h1>
      <form onSubmit={submit} className="space-y-3">
        <input
          className="w-full rounded border px-3 py-2"
          type="email"
          autoComplete="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="email"
        />
        <input
          className="w-full rounded border px-3 py-2"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="password"
        />
        {err && <div className="text-sm text-red-600">{err}</div>}
        <button
          className="w-full rounded bg-emerald-600 px-3 py-2 text-white disabled:opacity-50"
          disabled={busy || !password}
        >
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </div>
  );
}
