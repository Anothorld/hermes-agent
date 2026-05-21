import { FormEvent, useState } from 'react';
import { api } from '../api';

/**
 * Generic facts editor. The caller passes a list of fact keys (dotted,
 * namespace-prefixed) to render; the form posts to the console proxy
 * `POST /facts/{identity_id}`. The bridge enforces fact namespaces and
 * approval gates; this component just collects + dispatches.
 */
export function FactsEditor({
  identityId,
  campaignId,
  factKeys,
  onSubmitted,
}: {
  identityId: number;
  campaignId?: string;
  factKeys: string[];
  onSubmitted?: (resp: unknown) => void;
}) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  if (!factKeys.length) {
    return <div className="text-xs text-slate-500">No facts to collect.</div>;
  }

  async function submit(ev: FormEvent) {
    ev.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const facts: Record<string, unknown> = {};
      for (const k of factKeys) {
        const v = values[k];
        if (v === undefined || v === '') continue;
        facts[k] = coerce(v);
      }
      const body = {
        campaign_id: campaignId,
        facts,
        source: 'console',
      };
      const out = await api.post<unknown>(`/facts/${identityId}`, body);
      onSubmitted?.(out);
      setValues({});
    } catch (ex) {
      setErr(String(ex));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-2 rounded border border-slate-200 bg-white p-3">
      <div className="text-xs font-medium uppercase tracking-wide text-slate-500">
        Provide missing facts
      </div>
      {factKeys.map((k) => (
        <label key={k} className="flex items-center gap-2 text-sm">
          <span className="w-56 shrink-0 font-mono text-xs text-slate-700">{k}</span>
          <input
            value={values[k] ?? ''}
            onChange={(e) => setValues((v) => ({ ...v, [k]: e.target.value }))}
            className="flex-1 rounded border border-slate-300 px-2 py-1 text-sm"
            placeholder="value (string / number / true|false / [a,b])"
          />
        </label>
      ))}
      {err && <div className="text-xs text-red-600">{err}</div>}
      <button
        type="submit"
        disabled={busy}
        className="rounded bg-emerald-600 px-3 py-1 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
      >
        {busy ? 'Saving…' : 'Save facts'}
      </button>
    </form>
  );
}

/** Coerce string input into JSON-friendly value: bool, int, float, list, str. */
function coerce(s: string): unknown {
  const t = s.trim();
  if (t === 'true') return true;
  if (t === 'false') return false;
  if (/^-?\d+$/.test(t)) return parseInt(t, 10);
  if (/^-?\d+\.\d+$/.test(t)) return parseFloat(t);
  if (t.startsWith('[') && t.endsWith(']')) {
    return t
      .slice(1, -1)
      .split(',')
      .map((x) => x.trim())
      .filter((x) => x.length > 0);
  }
  return s;
}
