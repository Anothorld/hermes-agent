import { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams, Link, useSearchParams } from 'react-router-dom';
import { api, EscalationRow } from '../api';
import { parseConflictBody, startedAtMs, useInflightLock } from '../useInflightLock';
import InboundEmailCard, { type InboundEmail } from '../components/InboundEmailCard';
import { FactInput } from '../components/inputs/FactInput';
import { FactKeyChip } from '../components/inputs/FactKeyChip';
import { TimeAgo } from '../components/inputs/TimeAgo';
import { ErrorAlert } from '../components/feedback/ErrorAlert';
import { useEnvStore, toast } from '../lib/store';
import { errorSummary } from '../lib/errors';
import { dialog } from '../components/dialogs/useDialog';
import { usePollingFallback } from '../hooks/usePollingFallback';
import { useDataChannel } from '../hooks/useDataChannel';

// Escalation operator console.
// - List view (no :id) shows open escalations with parent-id chain.
// - Detail view (with :id) lets the operator answer + provide facts +
//   choose resume (default state) or terminate.
export function EscalationConsolePage() {
  const { id } = useParams();
  if (id) return <EscalationDetail id={Number(id)} />;
  return <EscalationList />;
}

function EscalationList() {
  const env = useEnvStore((s) => s.env);
  const [rows, setRows] = useState<EscalationRow[]>([]);
  const [state, setState] = useState<
    'awaiting_answer' | 'answered' | 'resolved' | 're_escalated' | 'aborted' | 'all'
  >('awaiting_answer');
  const [err, setErr] = useState<unknown>(null);
  const [busyId, setBusyId] = useState<number | null>(null);

  const refresh = useCallback(async () => {
    try {
      const params = new URLSearchParams({ env });
      if (state !== 'all') params.set('state', state);
      const qs = `?${params.toString()}`;
      setRows(await api.get<EscalationRow[]>(`/escalations${qs}`));
      setErr(null);
    } catch (ex) {
      setErr(ex);
    }
  }, [env, state]);

  const terminate = useCallback(
    async (rowId: number) => {
      const ok = await dialog.confirm({
        title: `终止 escalation #${rowId}？`,
        description: '此操作会把该目标标记为 aborted，AI 将不再尝试推进。不可撤销。',
        confirmLabel: '终止',
        cancelLabel: '保留',
        variant: 'danger',
        liveWarning: env === 'LIVE',
      });
      if (!ok) return;
      setBusyId(rowId);
      try {
        await api.patch(`/escalations/${rowId}`, {
          decision: 'terminate',
          final_state: 'aborted',
          operator_answer: '',
          operator_facts: {},
          env,
        });
        toast.success(`escalation #${rowId} 已终止`);
        await refresh();
      } catch (ex) {
        toast.error('终止失败', errorSummary(ex));
        setErr(ex);
      } finally {
        setBusyId(null);
      }
    },
    [env, refresh],
  );

  useEffect(() => {
    refresh();
  }, [refresh]);

  useDataChannel({ onMatch: refresh });
  usePollingFallback(refresh, 20_000);

  const STATE_LABELS: Record<typeof state, string> = {
    awaiting_answer: '等待答复',
    answered: '已答复',
    resolved: '已解决',
    re_escalated: '已再升级',
    aborted: '已终止',
    all: '全部',
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <h1 className="text-lg font-semibold">升级队列</h1>
        <select
          value={state}
          onChange={(e) => setState(e.target.value as typeof state)}
          className="rounded border border-slate-300 px-2 py-1 text-sm"
        >
          {(Object.keys(STATE_LABELS) as Array<typeof state>).map((s) => (
            <option key={s} value={s}>{STATE_LABELS[s]}</option>
          ))}
        </select>
      </div>
      {!!err && <ErrorAlert error={err} onRetry={refresh} />}
      <table className="w-full text-sm">
        <thead className="text-left text-xs uppercase tracking-wide text-slate-500">
          <tr>
            <th className="p-2">id</th>
            <th className="p-2">KOL</th>
            <th className="p-2">campaign</th>
            <th className="p-2">规则</th>
            <th className="p-2">原因</th>
            <th className="p-2">父级</th>
            <th className="p-2">创建</th>
            <th className="p-2">操作</th>
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
                  <div className="text-xs text-slate-800">{r.reason}</div>
                  {missing.length > 0 && (
                    <div className="mt-1 flex flex-wrap gap-1">
                      {missing.map((f) => (
                        <FactKeyChip key={f} factKey={f} variant="missing" />
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
                <td className="p-2 text-xs text-slate-500">
                  <TimeAgo iso={r.created_at} />
                </td>
                <td className="p-2">
                  {r.state === 'awaiting_answer' ? (
                    <button
                      type="button"
                      disabled={busyId === r.id}
                      onClick={() => terminate(r.id)}
                      className="rounded border border-red-300 px-2 py-0.5 text-xs text-red-700 hover:bg-red-50 disabled:opacity-50"
                      title="放弃此目标，将 escalation 标为 aborted"
                    >
                      {busyId === r.id ? '终止中…' : '终止'}
                    </button>
                  ) : (
                    <span className="text-xs text-slate-400">—</span>
                  )}
                </td>
              </tr>
            );
          })}
          {rows.length === 0 && (
            <tr>
              <td colSpan={8} className="p-6 text-center text-sm text-slate-500">
                没有 {STATE_LABELS[state]} 的升级。
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

// Pull "what's missing" out of an escalation's resume_context or, as a
// fallback, scrape obvious snake_case identifiers out of
// suggested_question.
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

function contextText(r: EscalationRow, key: string): string | null {
  const ctx = (r.resume_context ?? {}) as Record<string, unknown>;
  const value = ctx[key];
  return typeof value === 'string' && value.trim() ? value.trim() : null;
}

function reasonDetails(r: EscalationRow): string[] {
  const details = [
    contextText(r, 'operator_summary'),
    contextText(r, 'source_reason'),
    contextText(r, 'reason'),
  ].filter((value): value is string => Boolean(value));
  return [...new Set(details)];
}

const CONFIG_FIELD_BLOCKLIST = new Set<string>([
  'campaign_config', 'campaign_id', 'identity_id', 'test_mode',
  'deliverables_scope', 'fact_path', 'goal_state', 'kol_bridge_tool',
  'human_takeover_hint', 'required_facts_to_resume',
]);

const NAMESPACE_HELP: ReadonlyArray<{ prefix: string; label: string; hint: string }> = [
  { prefix: 'approval.', label: '审批 / 操作员决定', hint: '例：approval.paid_ceiling_override（提价上限）' },
  { prefix: 'offer.', label: '报价 / 合作条款', hint: '例：offer.compensation_amount, offer.agreed_terms' },
  { prefix: 'fulfillment.', label: '物流 / 内容履约', hint: '例：fulfillment.tracking_no, fulfillment.shipping_address' },
  { prefix: 'identity.', label: 'KOL 身份信息', hint: '例：identity.outreach_path' },
];

const NAMESPACE_PREFIXES: ReadonlyArray<string> = NAMESPACE_HELP.map((n) => n.prefix);

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
  // URL ?env= wins on first load (deep-link); otherwise use the store.
  const storeEnv = useEnvStore((s) => s.env);
  const env = (searchParams.get('env') === 'LIVE' ? 'LIVE'
    : searchParams.get('env') === 'TEST' ? 'TEST'
    : storeEnv) as 'TEST' | 'LIVE';
  const [row, setRow] = useState<EscalationRow | null>(null);
  const [answer, setAnswer] = useState('');
  const [factKeysText, setFactKeysText] = useState('');
  const [factsRecord, setFactsRecord] = useState<Record<string, unknown>>({});
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [done, setDone] = useState<string | null>(null);
  const [showAdvanced, setShowAdvanced] = useState(false);

  const [inbound, setInbound] = useState<InboundEmail | null>(null);
  const [inboundLoaded, setInboundLoaded] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const all = await api.get<EscalationRow[]>(`/escalations?env=${env}`);
      setRow(all.find((r) => r.id === id) ?? null);
      setErr(null);
    } catch (ex) {
      setErr(ex);
    }
  }, [env, id]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    let alive = true;
    setInboundLoaded(false);
    setInbound(null);
    api
      .get<{ inbound: InboundEmail | null }>(
        `/escalations/${id}/inbound-context?env=${env}`,
      )
      .then((r) => {
        if (!alive) return;
        setInbound(r.inbound ?? null);
      })
      .catch(() => {
        // Non-fatal — InboundEmailCard renders fallback.
      })
      .finally(() => alive && setInboundLoaded(true));
    return () => {
      alive = false;
    };
  }, [id, env]);

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

  const detailedReasons = useMemo(() => (row ? reasonDetails(row) : []), [row]);

  function collectFacts(): Record<string, unknown> {
    const facts: Record<string, unknown> = {};
    for (const k of factKeys) {
      const v = factsRecord[k];
      if (v !== undefined && v !== '' && v !== null) facts[k] = v;
    }
    return facts;
  }

  async function submit(decision: 'resume' | 'terminate') {
    if (decision === 'terminate') {
      const ok = await dialog.confirm({
        title: '终止此目标？',
        description: '将 escalation 标为 aborted，AI 不再尝试推进。',
        confirmLabel: '终止',
        cancelLabel: '取消',
        variant: 'danger',
        liveWarning: env === 'LIVE',
      });
      if (!ok) return;
    }
    setBusy(true);
    setErr(null);
    try {
      const body: Record<string, unknown> = {
        decision,
        operator_answer: answer,
        operator_facts: collectFacts(),
        env,
      };
      if (decision === 'terminate') body.final_state = 'aborted';
      await api.patch(`/escalations/${id}`, body);
      const msg = decision === 'resume' ? '已提交并恢复' : '已终止';
      setDone(msg);
      toast.success(msg);
      refresh();
    } catch (ex) {
      setErr(ex);
      toast.error('提交失败', errorSummary(ex));
    } finally {
      setBusy(false);
    }
  }

  const draftLock = useInflightLock(`draft.lock.escalation:${id}`);

  async function previewDraft() {
    setBusy(true);
    setErr(null);
    try {
      const resp = await api.post<{ run_id: string; hint: string }>(
        `/escalations/${id}/preview-draft`,
        { operator_answer: answer, operator_facts: collectFacts(), env },
      );
      draftLock.acquire(resp.run_id ?? null);
      toast.progress(
        '草稿生成中…',
        `约 30–60s 后在待审批页面可见 (run ${resp.run_id?.slice(0, 8) ?? '?'}…)`,
        { groupKey: `escalation-preview-${id}` },
      );
      setDone('草稿生成请求已发出，30–60s 后查看待审批页面。');
    } catch (ex) {
      const conflict = parseConflictBody(ex);
      if (conflict?.error === 'draft_already_in_flight') {
        draftLock.acquire(
          conflict.run_id ?? null,
          startedAtMs(conflict.started_at),
        );
        toast.info('已有草稿在生成', conflict.message ?? undefined);
        setDone(conflict.message ?? 'A draft for this escalation is already being generated.');
      } else {
        setErr(ex);
        toast.error('请求失败', errorSummary(ex));
      }
    } finally {
      setBusy(false);
    }
  }

  if (err && !row) return <ErrorAlert error={err} onRetry={refresh} />;
  if (!row) return <div className="text-sm text-slate-500">加载中…</div>;

  return (
    <div className="space-y-3">
      <Link to="/escalations" className="text-xs text-sky-700 hover:underline">
        ← 返回升级队列
      </Link>
      <h1 className="text-lg font-semibold">升级 #{row.id}</h1>
      {takeoverHint && (
        <div className="rounded border border-amber-300 bg-amber-50 p-2 text-sm text-amber-900">
          ⚠️ 已达 max_escalation_depth（attempts_count={row.attempts_count ?? '?'}）。
          建议人工接管或直接终止本目标，避免循环升级。
        </div>
      )}
      <div className="rounded border border-slate-200 bg-white p-3 text-sm">
        <div>
          KOL：
          <Link
            to={`/kols/${row.identity_id}?campaign_id=${encodeURIComponent(row.campaign_id)}`}
            className="text-sky-700 hover:underline"
          >
            {row.identity_id}
          </Link>
        </div>
        <div>Campaign：{row.campaign_id}</div>
        <div>
          规则：{row.rule_id ?? '—'} · 状态：{row.state} · 创建于 <TimeAgo iso={row.created_at} />
        </div>
        <div className="mt-2 rounded bg-slate-50 p-2">
          <div className="text-xs uppercase tracking-wide text-slate-500">原因代码</div>
          <div className="mt-0.5 font-mono text-xs text-slate-800">{row.reason}</div>
          {detailedReasons.length > 0 && (
            <div className="mt-2 space-y-1">
              <div className="text-xs uppercase tracking-wide text-slate-500">为什么会出现这个升级</div>
              {detailedReasons.map((detail) => (
                <div key={detail} className="text-sm text-slate-700">{detail}</div>
              ))}
            </div>
          )}
        </div>
        {row.suggested_question && (
          <div className="mt-2 rounded border border-sky-200 bg-sky-50 p-2 text-sky-950">
            <div className="text-xs font-semibold uppercase tracking-wide text-sky-700">
              请求操作员答复
            </div>
            <div className="mt-1 whitespace-pre-wrap text-sm leading-relaxed">
              {row.suggested_question}
            </div>
          </div>
        )}
        {extractMissingFields(row).length > 0 && (
          <div className="mt-1 flex flex-wrap items-center gap-1">
            <span className="text-xs text-slate-500">缺：</span>
            {extractMissingFields(row).map((f) => (
              <FactKeyChip key={f} factKey={f} variant="missing" />
            ))}
          </div>
        )}
        {row.parent_id && (
          <div className="text-xs text-slate-500">
            父级升级：{' '}
            <Link to={`/escalations/${row.parent_id}`} className="hover:underline">
              #{row.parent_id}
            </Link>
          </div>
        )}
      </div>

      {inboundLoaded && (
        <InboundEmailCard
          inbound={inbound}
          title="触发此升级的 KOL 回信"
          variant="rose"
        />
      )}

      {row.state !== 'awaiting_answer' ? (
        <div className="rounded border border-slate-200 bg-white p-3 text-sm text-slate-600">
          已 {row.state}。操作员答复：{' '}
          <em>{row.operator_answer || '(空)'}</em>
        </div>
      ) : (
        <div data-editing className="space-y-2 rounded border border-slate-200 bg-white p-3">
          {rejectedDrafts.length > 0 && (
            <div className="rounded border border-amber-300 bg-amber-50 px-2 py-1.5 text-xs text-amber-900">
              <div className="font-medium">
                之前生成的草稿被驳回 {rejectedDrafts.length} 次，未关闭此升级。请补充答复或终止此目标。
              </div>
              <ul className="mt-1 space-y-0.5">
                {rejectedDrafts.slice(-3).map((d, idx) => (
                  <li key={`${d.decided_at}-${idx}`} className="text-[11px]">
                    · <TimeAgo iso={d.decided_at} /> {d.decided_by ? `(${d.decided_by})` : ''}
                    {d.note ? ` — ${d.note}` : ' — 无理由'}
                  </li>
                ))}
              </ul>
            </div>
          )}
          <label className="block text-sm">
            <span className="text-xs uppercase tracking-wide text-slate-500">
              操作员答复
            </span>
            <textarea
              value={answer}
              onChange={(e) => setAnswer(e.target.value)}
              className="mt-1 w-full rounded border border-slate-300 px-2 py-1 text-sm"
              rows={3}
              placeholder="给 AI 看的自然语言答复 — 比如：把上限调到 5000；接受对方方案；要 KOL 提供地址 ..."
            />
          </label>
          {requiredFacts.length > 0 && (
            <div className="rounded bg-sky-50 px-2 py-1 text-xs text-sky-900">
              恢复执行需要补充以下字段：{requiredFacts.map((k) => (
                <FactKeyChip key={k} factKey={k} variant="neutral" className="mx-1" />
              ))}
            </div>
          )}
          {factKeys.map((k) => {
            const isRequired = requiredFacts.includes(k);
            return (
              <div key={k} className="flex items-start gap-2">
                <FactKeyChip
                  factKey={k}
                  variant="filled"
                  className={`mt-1 w-44 shrink-0 truncate ${isRequired ? 'border-sky-300 text-sky-800' : ''}`}
                  prefix={isRequired ? '★ ' : ''}
                />
                <div className="flex-1">
                  <FactInput
                    factKey={k}
                    value={factsRecord[k] ?? ''}
                    onChange={(v) => setFactsRecord((m) => ({ ...m, [k]: v }))}
                    bare
                  />
                </div>
              </div>
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
                    AI 已经需要的字段会在上方★号行自动出现。
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
                    多个字段用逗号分隔。下方输入框支持自动补全常用字段。
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

          {!!err && <ErrorAlert error={err} compact />}

          {/* Restructured action zone: explicit hierarchy — primary
              (提交并恢复) on the right, secondary (生成草稿) inline link
              above, danger (终止) small + low-contrast on the left. */}
          <div className="border-t border-slate-100 pt-3">
            <div className="mb-2 flex items-center gap-2 text-xs">
              <button
                type="button"
                disabled={busy || draftLock.locked}
                onClick={previewDraft}
                className="rounded border border-sky-300 px-2 py-1 text-sky-700 hover:bg-sky-50 disabled:opacity-50"
              >
                {draftLock.locked
                  ? `草稿生成中… (${draftLock.remainingSeconds}s)`
                  : '让 AI 先试写一封草稿'}
              </button>
              <span className="text-[11px] text-slate-500">
                结果出现在<Link to="/approvals" className="ml-0.5 text-sky-700 hover:underline">待审批</Link>页面供审核；不会关闭本升级。
              </span>
            </div>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <button
                type="button"
                disabled={busy}
                onClick={() => submit('terminate')}
                className="rounded border border-red-300 bg-white px-2 py-1 text-xs text-red-700 hover:bg-red-50 disabled:opacity-50"
                title="放弃此目标，标为 aborted"
              >
                直接终止
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => submit('resume')}
                className="rounded bg-emerald-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
                title="把答复交给 AI 继续推进，并把此升级标为已处理"
              >
                提交并恢复 →
              </button>
            </div>
            <p className="mt-1 text-[11px] text-slate-500">
              "提交并恢复"会把答复交给 AI 继续推进；若此升级来自入站 KOL 回信且尚未有待审草稿，会自动顺带起草一份。
            </p>
          </div>
          {done && <div className="text-sm text-emerald-700">{done}</div>}
        </div>
      )}
    </div>
  );
}
