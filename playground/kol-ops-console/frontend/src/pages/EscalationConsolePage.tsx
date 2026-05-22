import { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams, Link, useSearchParams } from 'react-router-dom';
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
  const [env, setEnv] = useState<'TEST' | 'LIVE'>(() => {
    const saved = localStorage.getItem('escalationEnv') || localStorage.getItem('kolEnv');
    return saved === 'LIVE' ? 'LIVE' : 'TEST';
  });
  // Bridge-side states: awaiting_answer | answered | resolved | re_escalated | aborted.
  // Default to awaiting_answer so operators see the actionable queue first.
  const [state, setState] = useState<
    'awaiting_answer' | 'answered' | 'resolved' | 're_escalated' | 'aborted' | 'all'
  >('awaiting_answer');
  const [err, setErr] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const params = new URLSearchParams({ env });
      if (state !== 'all') params.set('state', state);
      const qs = `?${params.toString()}`;
      setRows(await api.get<EscalationRow[]>(`/escalations${qs}`));
      setErr(null);
    } catch (ex) {
      setErr(String(ex));
    }
  }, [env, state]);

  useEffect(() => {
    localStorage.setItem('escalationEnv', env);
    refresh();
    const t = setInterval(refresh, 10_000);
    return () => clearInterval(t);
  }, [env, refresh]);

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <h1 className="text-lg font-semibold">Escalations</h1>
        <select
          value={env}
          onChange={(e) => setEnv(e.target.value as typeof env)}
          className="rounded border border-slate-300 px-2 py-1 text-sm"
        >
          <option value="TEST">TEST</option>
          <option value="LIVE">LIVE</option>
        </select>
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
          {rows.map((r) => {
            const missing = extractMissingFields(r);
            return (
              <tr key={r.id} className="border-t border-slate-100 align-top hover:bg-slate-50">
                <td className="p-2">
                    <Link to={`/escalations/${r.id}?env=${env}`} className="text-sky-700 hover:underline">
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
                <td className="p-2">
                  <div className="font-mono text-xs text-slate-800">{r.reason}</div>
                  {missing.length > 0 && (
                    <div className="mt-1 flex flex-wrap gap-1">
                      {missing.map((f) => (
                        <span
                          key={f}
                          className="rounded bg-rose-100 px-1.5 py-0.5 font-mono text-[10px] text-rose-800"
                          title="Field reported as missing by the agent"
                        >
                          {f}
                        </span>
                      ))}
                    </div>
                  )}
                  {r.suggested_question && (
                    <div className="mt-1 line-clamp-2 text-xs text-slate-500" title={r.suggested_question}>
                      {r.suggested_question}
                    </div>
                  )}
                </td>
                <td className="p-2">{r.parent_id ?? '—'}</td>
                <td className="p-2 text-xs text-slate-500">{r.created_at}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// Pull "what's missing" out of an escalation's resume_context (structured
// list set by the skill) or, as a fallback, scrape obvious snake_case
// identifiers out of suggested_question. The latter is best-effort; once
// all skills emit ``missing_config_fields`` we can drop the regex path.
function extractMissingFields(r: EscalationRow): string[] {
  const ctx = (r.resume_context ?? {}) as Record<string, unknown>;
  for (const key of ['missing_config_fields', 'missing_facts', 'missing']) {
    const v = ctx[key];
    if (Array.isArray(v)) {
      const arr = v.filter((x): x is string => typeof x === 'string');
      if (arr.length) return arr;
    }
  }
  if (!r.reason?.startsWith('campaign_config_incomplete') || !r.suggested_question) {
    return [];
  }
  const seen = new Set<string>();
  const out: string[] = [];
  const re = /\b([a-z][a-z0-9_]{6,40})\b/g;
  for (const m of r.suggested_question.matchAll(re)) {
    const tok = m[1];
    if (!tok.includes('_')) continue;
    if (CONFIG_FIELD_BLOCKLIST.has(tok)) continue;
    if (seen.has(tok)) continue;
    seen.add(tok);
    out.push(tok);
  }
  return out.slice(0, 6);
}

// Common words / snake_case tokens in escalation prose that aren't
// config fields; suppress them so the chip row stays signal-only.
const CONFIG_FIELD_BLOCKLIST = new Set<string>([
  'campaign_config', 'campaign_id', 'identity_id', 'test_mode',
  'deliverables_scope', 'fact_path', 'goal_state', 'kol_bridge_tool',
  'human_takeover_hint', 'required_facts_to_resume',
]);

// Operator-facing explanation for the 4 fact namespaces. Operators
// rarely need to add custom keys — the resumer pre-populates required
// ones — but when they do, the Advanced section needs to explain what
// a legal key looks like in plain language, not "namespace prefix".
const NAMESPACE_HELP: ReadonlyArray<{ prefix: string; label: string; hint: string }> = [
  { prefix: 'approval.', label: '审批 / 操作员决定', hint: '例：approval.paid_ceiling_override（提价上限）' },
  { prefix: 'offer.', label: '报价 / 合作条款', hint: '例：offer.compensation_amount, offer.agreed_terms' },
  { prefix: 'fulfillment.', label: '物流 / 内容履约', hint: '例：fulfillment.tracking_no, fulfillment.shipping_address' },
  { prefix: 'identity.', label: 'KOL 身份信息', hint: '例：identity.outreach_path' },
];

const NAMESPACE_PREFIXES: ReadonlyArray<string> = NAMESPACE_HELP.map((n) => n.prefix);

// Curated subset of fact keys that operators are most likely to add
// manually — sourced from the keys actually written by the negotiation /
// contract / logistics skills. Surface as a <datalist> autocomplete so
// the operator doesn't have to memorise snake_case names.
const COMMON_FACT_KEYS: ReadonlyArray<string> = [
  'approval.paid_ceiling_override',
  'approval.over_budget_request',
  'approval.contract_change_request',
  'approval.logistics_anomaly',
  'offer.compensation_amount',
  'offer.compensation_currency',
  'offer.compensation_mode',
  'offer.agreed_terms',
  'offer.deliverables_scope',
  'offer.kol_quote',
  'fulfillment.shipping_address',
  'fulfillment.tracking_no',
  'fulfillment.tracking_carrier',
];

function isValidFactKey(k: string): boolean {
  const trimmed = k.trim();
  if (!trimmed) return false;
  if (!NAMESPACE_PREFIXES.some((p) => trimmed.startsWith(p))) return false;
  return /^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$/.test(trimmed);
}

function EscalationDetail({ id }: { id: number }) {
  const [searchParams] = useSearchParams();
  const env = searchParams.get('env') === 'LIVE' ? 'LIVE' : 'TEST';
  const [row, setRow] = useState<EscalationRow | null>(null);
  const [answer, setAnswer] = useState('');
  const [factKeysText, setFactKeysText] = useState('');
  const [factsRecord, setFactsRecord] = useState<Record<string, string>>({});
  const [finalState] = useState<'aborted'>('aborted');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [showAdvanced, setShowAdvanced] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const all = await api.get<EscalationRow[]>(`/escalations?env=${env}`);
      setRow(all.find((r) => r.id === id) ?? null);
      setErr(null);
    } catch (ex) {
      setErr(String(ex));
    }
  }, [env, id]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // When the triggering skill embeds ``required_facts_to_resume`` in
  // ``resume_context``, prefill the structured-facts form with those
  // keys so the operator sees exactly which facts the resumer needs.
  // The free-text input remains for ad-hoc extras.
  const requiredFacts = useMemo<string[]>(() => {
    const ctx = (row?.resume_context ?? null) as Record<string, unknown> | null;
    const raw = ctx?.required_facts_to_resume;
    if (Array.isArray(raw)) {
      return raw.filter((x): x is string => typeof x === 'string');
    }
    return [];
  }, [row]);

  useEffect(() => {
    if (requiredFacts.length > 0 && factKeysText === '') {
      setFactKeysText(requiredFacts.join(', '));
    }
  }, [requiredFacts, factKeysText]);

  const factKeys = useMemo(
    () => {
      const fromText = factKeysText
        .split(/[,\s]+/)
        .map((s) => s.trim())
        .filter(Boolean)
        .filter(isValidFactKey);
      // De-dup while preserving order (required first).
      const seen = new Set<string>();
      return [...requiredFacts, ...fromText].filter((k) => {
        if (seen.has(k)) return false;
        seen.add(k);
        return true;
      });
    },
    [factKeysText, requiredFacts],
  );

  const takeoverHint = useMemo<boolean>(() => {
    const ctx = (row?.resume_context ?? null) as Record<string, unknown> | null;
    return Boolean(ctx?.force_human_takeover_hint);
  }, [row]);

  const rejectedDrafts = useMemo<Array<{ note: string; decided_by: string; decided_at: string }>>(() => {
    const ctx = (row?.resume_context ?? null) as Record<string, unknown> | null;
    const raw = ctx?.rejected_drafts;
    if (!Array.isArray(raw)) return [];
    return raw.flatMap((entry) => {
      if (!entry || typeof entry !== 'object') return [];
      const e = entry as Record<string, unknown>;
      return [{
        note: typeof e.note === 'string' ? e.note : '',
        decided_by: typeof e.decided_by === 'string' ? e.decided_by : '',
        decided_at: typeof e.decided_at === 'string' ? e.decided_at : '',
      }];
    });
  }, [row]);

  function collectFacts(): Record<string, unknown> {
    const facts: Record<string, unknown> = {};
    for (const k of factKeys) {
      const v = factsRecord[k];
      if (v !== undefined && v !== '') facts[k] = coerce(v);
    }
    return facts;
  }

  async function submit(decision: 'resume' | 'terminate') {
    setBusy(true);
    setErr(null);
    try {
      const body: Record<string, unknown> = {
        decision,
        operator_answer: answer,
        operator_facts: collectFacts(),
        env,
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

  async function previewDraft() {
    setBusy(true);
    setErr(null);
    try {
      const resp = await api.post<{ run_id: string; hint: string }>(
        `/escalations/${id}/preview-draft`,
        { operator_answer: answer, operator_facts: collectFacts(), env },
      );
      setDone(
        `Draft requested (run ${resp.run_id?.slice(0, 8) ?? '?'}…). ` +
        `Check the Approvals page in 30–60s for an approval.reply_draft.`,
      );
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
      {takeoverHint && (
        <div className="rounded border border-amber-300 bg-amber-50 p-2 text-sm text-amber-900">
          ⚠️ 已达 max_escalation_depth（attempts_count={row.attempts_count ?? '?'}）。
          建议人工接管或直接终止本目标，避免循环升级。
        </div>
      )}
      <div className="rounded border border-slate-200 bg-white p-3 text-sm">
        <div>identity: <Link to={`/kols/${row.identity_id}?campaign_id=${encodeURIComponent(row.campaign_id)}`} className="text-sky-700 hover:underline">{row.identity_id}</Link></div>
        <div>campaign: {row.campaign_id}</div>
        <div>rule: {row.rule_id ?? '—'} · state: {row.state}</div>
        <div>reason: {row.reason}</div>
        {extractMissingFields(row).length > 0 && (
          <div className="mt-1 flex flex-wrap items-center gap-1">
            <span className="text-xs text-slate-500">missing:</span>
            {extractMissingFields(row).map((f) => (
              <span
                key={f}
                className="rounded bg-rose-100 px-1.5 py-0.5 font-mono text-[11px] text-rose-800"
              >
                {f}
              </span>
            ))}
          </div>
        )}
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
          {rejectedDrafts.length > 0 && (
            <div className="rounded border border-amber-300 bg-amber-50 px-2 py-1.5 text-xs text-amber-900">
              <div className="font-medium">
                之前生成的草稿被驳回 {rejectedDrafts.length} 次，未关闭此 escalation。请补充答复或终止此目标。
              </div>
              <ul className="mt-1 space-y-0.5">
                {rejectedDrafts.slice(-3).map((d, idx) => (
                  <li key={`${d.decided_at}-${idx}`} className="font-mono text-[11px]">
                    · {d.decided_at?.slice(0, 19) || '?'} {d.decided_by ? `(${d.decided_by})` : ''}
                    {d.note ? ` — ${d.note}` : ' — 无理由'}
                  </li>
                ))}
              </ul>
            </div>
          )}
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
          {requiredFacts.length > 0 && (
            <div className="rounded bg-sky-50 px-2 py-1 text-xs text-sky-900">
              Resumer needs these facts to continue: {requiredFacts.map((k) => (
                <code key={k} className="mx-1 rounded bg-white px-1 py-0.5 font-mono">{k}</code>
              ))}
            </div>
          )}
          {factKeys.map((k) => {
            const isRequired = requiredFacts.includes(k);
            return (
              <label key={k} className="flex items-center gap-2 text-sm">
                <span className={`w-56 shrink-0 font-mono text-xs ${isRequired ? 'text-sky-700' : 'text-slate-700'}`}>
                  {k}{isRequired && <span className="ml-0.5 text-red-500">*</span>}
                </span>
                <input
                  value={factsRecord[k] ?? ''}
                  onChange={(e) =>
                    setFactsRecord((v) => ({ ...v, [k]: e.target.value }))
                  }
                  className="flex-1 rounded border border-slate-300 px-2 py-1 text-sm"
                />
              </label>
            );
          })}
          <div className="border-t border-slate-100 pt-2">
            <button
              type="button"
              onClick={() => setShowAdvanced((v) => !v)}
              className="text-xs text-slate-500 hover:text-slate-700"
            >
              {showAdvanced ? '▾' : '▸'} 高级：自定义字段
            </button>
            {showAdvanced && (
              <div className="mt-2 space-y-2 rounded border border-slate-200 bg-slate-50/60 p-2">
                <div className="rounded bg-white px-2 py-1.5 text-[11px] leading-snug text-slate-700">
                  <div className="font-medium text-slate-800">这里是做什么的？</div>
                  <div className="mt-0.5 text-slate-600">
                    上方"答复"是给 AI 看的<strong>自然语言</strong>。
                    这里则是给系统记账的<strong>结构化字段</strong>——
                    比如新的报价上限、物流单号、签约条款等。
                  </div>
                  <div className="mt-0.5 text-slate-500">
                    AI 已经需要的字段会在上方<span className="font-mono">*</span>号行自动出现。
                    只有当你想<strong>主动补充</strong> AI 没问到、但后续会用到的事实时才需要在这里加字段。
                  </div>
                </div>
                <div className="text-[11px] leading-snug text-slate-700">
                  <div className="mb-1 font-medium">字段名必须以下列前缀之一开头：</div>
                  <ul className="space-y-0.5 pl-1">
                    {NAMESPACE_HELP.map((n) => (
                      <li key={n.prefix}>
                        <code className="rounded bg-white px-1 py-0.5 font-mono text-[10.5px]">{n.prefix}</code>
                        <span className="ml-1 text-slate-600">{n.label}</span>
                        <span className="ml-1 text-slate-400">— {n.hint}</span>
                      </li>
                    ))}
                  </ul>
                  <div className="mt-1 text-slate-500">
                    多个字段用逗号分隔。下方输入框支持自动补全常用字段（点击或下拉选择）。
                  </div>
                </div>
                <label className="block text-sm">
                  <input
                    list="extra-fact-keys-list"
                    value={factKeysText}
                    onChange={(e) => setFactKeysText(e.target.value)}
                    className="w-full rounded border border-slate-300 px-2 py-1 font-mono text-xs"
                    placeholder="approval.paid_ceiling_override, offer.agreed_terms"
                  />
                  <datalist id="extra-fact-keys-list">
                    {COMMON_FACT_KEYS.map((k) => (
                      <option key={k} value={k} />
                    ))}
                  </datalist>
                </label>
                {(() => {
                  const raw = factKeysText.split(/[,\s]+/).map((s) => s.trim()).filter(Boolean);
                  const bad = raw.filter((k) => !isValidFactKey(k));
                  if (bad.length === 0) return null;
                  return (
                    <div className="rounded bg-rose-50 px-2 py-1 text-[11px] text-rose-800">
                      下列字段名不合法，已忽略：{' '}
                      {bad.map((k) => (
                        <code key={k} className="mx-1 rounded bg-white px-1 py-0.5 font-mono">{k}</code>
                      ))}
                      <div className="text-rose-700">必须形如 <code className="font-mono">namespace.key_name</code>，且 namespace 在上方列表中。</div>
                    </div>
                  );
                })()}
              </div>
            )}
          </div>
          <div className="grid grid-cols-1 gap-2 pt-1 md:grid-cols-3">
            <div className="rounded border border-sky-200 bg-sky-50/50 p-2">
              <button
                disabled={busy}
                onClick={previewDraft}
                className="w-full rounded border border-sky-600 px-3 py-1 text-sm text-sky-700 hover:bg-sky-100 disabled:opacity-50"
              >
                生成邮件草稿
              </button>
              <p className="mt-1 text-[11px] leading-snug text-slate-600">
                让 AI 根据你的答复先<strong>试起草</strong>一封回信，结果出现在 <strong>Approvals</strong> 页面供审核。
                <span className="text-slate-500"> 本 escalation 保持打开；每个 escalation 同一时间只会保留一份待审草稿。</span>
              </p>
            </div>
            <div className="rounded border border-emerald-200 bg-emerald-50/50 p-2">
              <button
                disabled={busy}
                onClick={() => submit('resume')}
                className="w-full rounded bg-emerald-600 px-3 py-1 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
              >
                提交并恢复
              </button>
              <p className="mt-1 text-[11px] leading-snug text-slate-600">
                把答复交给 AI 继续推进，并把此 escalation 标记为<strong>已处理</strong>。
                <span className="block text-slate-500">
                  若此 escalation 来自入站 KOL 回信且<strong>尚未</strong>有待审草稿，会自动顺带起草一份；若已点过"生成邮件草稿"产出待审草稿，则不会重复起草。
                </span>
              </p>
            </div>
            <div className="rounded border border-red-200 bg-red-50/50 p-2">
              <button
                disabled={busy}
                onClick={() => submit('terminate')}
                className="w-full rounded bg-red-600 px-3 py-1 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-50"
              >
                直接终止
              </button>
              <p className="mt-1 text-[11px] leading-snug text-slate-600">
                放弃此目标，把 escalation 标为 <code className="font-mono">{finalState}</code>。
                <span className="text-slate-500"> 不再继续处理。</span>
              </p>
            </div>
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
