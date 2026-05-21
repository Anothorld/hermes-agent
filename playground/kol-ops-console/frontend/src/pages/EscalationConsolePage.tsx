import { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { api, EscalationRow } from '../api';

/**
 * Escalation operator console.
 * - List view (no :id) shows open escalations with parent-id chain.
 * - Detail view (with :id) lets the operator answer + provide facts +
 *   choose resume (default state) or terminate (with final_state).
 */
export function EscalationConsolePage() {
  const { id } = useParams();
  if (id) return <EscalationDetail id={Number(id)} />;
  return <EscalationList />;
}

function EscalationList() {
  const [rows, setRows] = useState<EscalationRow[]>([]);
  // Bridge-side states: awaiting_answer | answered | resolved | re_escalated | aborted.
  // Default to awaiting_answer so operators see the actionable queue first.
  const [state, setState] = useState<
    'awaiting_answer' | 'answered' | 'resolved' | 're_escalated' | 'aborted' | 'all'
  >('awaiting_answer');
  const [err, setErr] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const qs = state === 'all' ? '' : `?state=${state}`;
      setRows(await api.get<EscalationRow[]>(`/escalations${qs}`));
      setErr(null);
    } catch (ex) {
      setErr(String(ex));
    }
  }, [state]);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 10_000);
    return () => clearInterval(t);
  }, [refresh]);

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <h1 className="text-lg font-semibold">Escalations</h1>
        <select
          value={state}
          onChange={(e) => setState(e.target.value as typeof state)}
          className="rounded border border-slate-300 px-2 py-1 text-sm"
        >
          <option value="awaiting_answer">awaiting_answer</option>
          <option value="answered">answered</option>
          <option value="resolved">resolved</option>
          <option value="re_escalated">re_escalated</option>
          <option value="aborted">aborted</option>
          <option value="all">all</option>
        </select>
      </div>
      {err && <div className="text-red-600">{err}</div>}
      <table className="w-full text-sm">
        <thead className="text-left text-xs uppercase tracking-wide text-slate-500">
          <tr>
            <th className="p-2">id</th>
            <th className="p-2">identity</th>
            <th className="p-2">campaign</th>
            <th className="p-2">rule</th>
            <th className="p-2">reason</th>
            <th className="p-2">parent</th>
            <th className="p-2">created</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} className="border-t border-slate-100 hover:bg-slate-50">
              <td className="p-2">
                <Link to={`/escalations/${r.id}`} className="text-sky-700 hover:underline">
                  #{r.id}
                </Link>
              </td>
              <td className="p-2">
                <Link to={`/kols/${r.identity_id}?campaign_id=${encodeURIComponent(r.campaign_id)}`}>
                  {r.identity_id}
                </Link>
              </td>
              <td className="p-2">{r.campaign_id}</td>
              <td className="p-2">{r.rule_id ?? '—'}</td>
              <td className="p-2">{r.reason}</td>
              <td className="p-2">{r.parent_id ?? '—'}</td>
              <td className="p-2 text-xs text-slate-500">{r.created_at}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function EscalationDetail({ id }: { id: number }) {
  const [row, setRow] = useState<EscalationRow | null>(null);
  const [answer, setAnswer] = useState('');
  const [factKeysText, setFactKeysText] = useState('');
  const [factsRecord, setFactsRecord] = useState<Record<string, string>>({});
  const [finalState, setFinalState] = useState('aborted');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const all = await api.get<EscalationRow[]>(`/escalations`);
      setRow(all.find((r) => r.id === id) ?? null);
      setErr(null);
    } catch (ex) {
      setErr(String(ex));
    }
  }, [id]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const factKeys = useMemo(
    () => factKeysText.split(/[,\s]+/).map((s) => s.trim()).filter(Boolean),
    [factKeysText],
  );

  async function submit(decision: 'resume' | 'terminate') {
    setBusy(true);
    setErr(null);
    try {
      const facts: Record<string, unknown> = {};
      for (const k of factKeys) {
        const v = factsRecord[k];
        if (v !== undefined && v !== '') facts[k] = coerce(v);
      }
      const body: Record<string, unknown> = {
        decision,
        operator_answer: answer,
        operator_facts: facts,
      };
      if (decision === 'terminate') body.final_state = finalState;
      await api.patch(`/escalations/${id}`, body);
      setDone(`Submitted: ${decision}`);
      refresh();
    } catch (ex) {
      setErr(String(ex));
    } finally {
      setBusy(false);
    }
  }

  if (err) return <div className="text-red-600">{err}</div>;
  if (!row) return <div className="text-sm text-slate-500">Loading…</div>;

  return (
    <div className="space-y-3">
      <Link to="/escalations" className="text-xs text-sky-700 hover:underline">
        ← back
      </Link>
      <h1 className="text-lg font-semibold">Escalation #{row.id}</h1>
      <div className="rounded border border-slate-200 bg-white p-3 text-sm">
        <div>identity: <Link to={`/kols/${row.identity_id}?campaign_id=${encodeURIComponent(row.campaign_id)}`} className="text-sky-700 hover:underline">{row.identity_id}</Link></div>
        <div>campaign: {row.campaign_id}</div>
        <div>rule: {row.rule_id ?? '—'} · state: {row.state}</div>
        <div>reason: {row.reason}</div>
        {row.suggested_question && (
          <div className="mt-1 rounded bg-sky-50 p-2 text-sky-900">
            ❓ {row.suggested_question}
          </div>
        )}
        {row.parent_id && (
          <div className="text-xs text-slate-500">
            parent escalation:{' '}
            <Link to={`/escalations/${row.parent_id}`} className="hover:underline">
              #{row.parent_id}
            </Link>
          </div>
        )}
      </div>

      {row.state !== 'awaiting_answer' ? (
        <div className="rounded border border-slate-200 bg-white p-3 text-sm text-slate-600">
          Already {row.state}. Operator answer was:{' '}
          <em>{row.operator_answer || '(empty)'}</em>
        </div>
      ) : (
        <div className="space-y-2 rounded border border-slate-200 bg-white p-3">
          <label className="block text-sm">
            <span className="text-xs uppercase tracking-wide text-slate-500">
              Operator answer
            </span>
            <textarea
              value={answer}
              onChange={(e) => setAnswer(e.target.value)}
              className="mt-1 w-full rounded border border-slate-300 px-2 py-1 text-sm"
              rows={3}
            />
          </label>
          <label className="block text-sm">
            <span className="text-xs uppercase tracking-wide text-slate-500">
              Fact keys (comma-separated; required_facts_to_resume)
            </span>
            <input
              value={factKeysText}
              onChange={(e) => setFactKeysText(e.target.value)}
              className="mt-1 w-full rounded border border-slate-300 px-2 py-1 font-mono text-xs"
              placeholder="approval.paid_ceiling_override, offer.agreed_terms"
            />
          </label>
          {factKeys.map((k) => (
            <label key={k} className="flex items-center gap-2 text-sm">
              <span className="w-56 shrink-0 font-mono text-xs">{k}</span>
              <input
                value={factsRecord[k] ?? ''}
                onChange={(e) =>
                  setFactsRecord((v) => ({ ...v, [k]: e.target.value }))
                }
                className="flex-1 rounded border border-slate-300 px-2 py-1 text-sm"
              />
            </label>
          ))}
          <div className="flex flex-wrap items-center gap-2">
            <button
              disabled={busy}
              onClick={() => submit('resume')}
              className="rounded bg-emerald-600 px-3 py-1 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
            >
              提交并恢复
            </button>
            <select
              value={finalState}
              onChange={(e) => setFinalState(e.target.value)}
              className="rounded border border-slate-300 px-2 py-1 text-sm"
            >
              <option value="aborted">aborted</option>
              <option value="declined">declined</option>
              <option value="abandoned">abandoned</option>
            </select>
            <button
              disabled={busy}
              onClick={() => submit('terminate')}
              className="rounded bg-red-600 px-3 py-1 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-50"
            >
              直接终止
            </button>
          </div>
          {done && <div className="text-sm text-emerald-700">{done}</div>}
        </div>
      )}
    </div>
  );
}

function coerce(s: string): unknown {
  const t = s.trim();
  if (t === 'true') return true;
  if (t === 'false') return false;
  if (/^-?\d+$/.test(t)) return parseInt(t, 10);
  if (/^-?\d+\.\d+$/.test(t)) return parseFloat(t);
  if (t.startsWith('[') && t.endsWith(']')) {
    return t.slice(1, -1).split(',').map((x) => x.trim()).filter((x) => x);
  }
  return s;
}
