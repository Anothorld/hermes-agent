import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, EscalationRow } from '../api';
import ApprovalContextCard from '../components/ApprovalContextCard';
import ApprovalDetailPanel from '../components/ApprovalDetailPanel';
import DraftEditDiffPanel from '../components/DraftEditDiffPanel';
import { FactKeyChip } from '../components/inputs/FactKeyChip';
import { TimeAgo } from '../components/inputs/TimeAgo';
import { ErrorAlert } from '../components/feedback/ErrorAlert';
import { useEnvStore, toast } from '../lib/store';
import { useUnreadStore } from '../lib/unread';
import { errorSummary } from '../lib/errors';
import { dialog } from '../components/dialogs/useDialog';
import {
  parseConflictBody,
  startedAtMs,
  useInflightLock,
} from '../useInflightLock';
import { REJECT_TAGS } from '../constants/rejectTags';
import { policyScopeLabel } from '../constants/domainLabels';

type ApprovalTypeFilter =
  | 'all'
  | 'reply_draft'
  | 'style_learning'
  | 'outcome_learning'
  | 'other';

const TYPE_FILTER_LABEL: Record<ApprovalTypeFilter, string> = {
  all: '全部',
  reply_draft: '回信草稿',
  style_learning: '学习提案',
  outcome_learning: '合作复盘',
  other: '其他',
};

function approvalTypeOf(factPath: string): ApprovalTypeFilter {
  if (factPath === 'approval.reply_draft') return 'reply_draft';
  if (factPath === 'approval.style_learning_proposal') return 'style_learning';
  if (factPath === 'approval.outcome_learning_proposal') return 'outcome_learning';
  return 'other';
}

const STYLE_LEARNING_FACT = 'approval.style_learning_proposal';
const OUTCOME_LEARNING_FACT = 'approval.outcome_learning_proposal';
const LEARNING_FACTS = [STYLE_LEARNING_FACT, OUTCOME_LEARNING_FACT];

/** Learning proposals are cross-KOL; group by policy scope, not anchor identity. */
function approvalGroupKey(row: ApprovalRow): string {
  if (LEARNING_FACTS.includes(row.fact_path)) {
    const ctx = row.context ?? {};
    const scope = String(ctx.scope ?? 'company_style');
    const owner = ctx.owner_user_id;
    return `learning::${row.fact_path}::${scope}::${owner ?? 'none'}`;
  }
  return `${row.identity_id}::${row.campaign_id}`;
}

function isStyleLearningGroup(items: ApprovalRow[]): boolean {
  return (
    items.length > 0 && items.every((r) => LEARNING_FACTS.includes(r.fact_path))
  );
}

function styleLearningGroupTitle(ctx: Record<string, unknown>, factPath?: string): string {
  const scope = String(ctx.scope ?? 'company_style');
  const sampleCount = ctx.sample_count;
  const kolCount = ctx.sample_identity_count;
  const isOutcome = factPath === OUTCOME_LEARNING_FACT;
  const parts = [isOutcome ? '跨 KOL 合作复盘' : '跨 KOL 编辑学习'];
  parts.push(policyScopeLabel(scope));
  if (typeof kolCount === 'number' && kolCount > 0) {
    parts.push(`${kolCount} 位 KOL`);
  }
  if (typeof sampleCount === 'number' && sampleCount > 0) {
    parts.push(`${sampleCount} 条样本`);
  }
  return parts.join(' · ');
}
import { usePollingFallback } from '../hooks/usePollingFallback';
import { useDataChannel } from '../hooks/useDataChannel';

// Cross-cutting approvals page. Renders all pending approval.* facts
// surfaced by the bridge (e.g. compensation_cap_breach, reply_draft)
// with KOL + campaign + namespace path + context, plus 批准 / 驳回 / 优化
// actions. Reply-draft rows expose prior revisions through the
// collapsible 历史版本 block.
export type ApprovalRow = {
  identity_id: number;
  campaign_id: string;
  fact_path: string;
  status?: 'pending' | 'approved' | 'rejected' | string | null;
  env?: 'TEST' | 'LIVE' | string | null;
  namespace: 'identity' | 'offer' | 'fulfillment' | 'approval';
  context: Record<string, unknown> | null;
  opened_by: string | null;
  opened_at: string;
  linked_escalation_id?: number | null;
  handle?: string | null;
};

type StatusFilter = 'pending' | 'approved' | 'rejected' | 'all';
type SlaFilter = 'all' | 'at_risk' | 'breached';
type RejectionTag = 'tone' | 'fact' | 'offer' | 'risk' | 'other';

const STATUS_LABEL: Record<StatusFilter, string> = {
  pending: '待审批',
  approved: '已通过',
  rejected: '已驳回',
  all: '全部',
};

const REJECTION_TAGS: ReadonlyArray<{ id: RejectionTag; label: string }> = [
  { id: 'tone', label: '语气' },
  { id: 'fact', label: '事实错误' },
  { id: 'offer', label: '报价/条款' },
  { id: 'risk', label: '风险控制' },
  { id: 'other', label: '其他' },
];

const SLA_LABEL: Record<SlaFilter, string> = {
  all: '全部',
  at_risk: '30 分钟+',
  breached: '2 小时+',
};

const rowKey = (r: ApprovalRow) =>
  `${r.identity_id}::${r.campaign_id}::${r.fact_path}`;

function rowActionKey(r: ApprovalRow): string {
  return `${rowKey(r)}::${r.opened_at}`;
}

function ageMs(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const ts = Date.parse(iso);
  if (!Number.isFinite(ts)) return null;
  return Date.now() - ts;
}

function slaLevel(iso: string | null | undefined): 'normal' | 'at_risk' | 'breached' {
  const ms = ageMs(iso);
  if (ms == null) return 'normal';
  if (ms >= 2 * 60 * 60 * 1000) return 'breached';
  if (ms >= 30 * 60 * 1000) return 'at_risk';
  return 'normal';
}

function parseRejectionTags(raw: string): RejectionTag[] {
  const seen = new Set<RejectionTag>();
  const lower = raw.toLowerCase();
  for (const tag of REJECTION_TAGS) {
    const tokens = [`[${tag.id}]`, tag.id];
    if (tokens.some((tok) => lower.includes(tok))) seen.add(tag.id);
  }
  for (const tag of REJECT_TAGS) {
    if (lower.includes(tag)) seen.add(tag as RejectionTag);
  }
  return [...seen];
}

type PreviousDraft = {
  subject?: string | null;
  body?: string | null;
  to?: string | null;
  [k: string]: unknown;
};

// Match the FastAPI captured_at format (ISO-8601 UTC). Returns null when
// unparseable so callers can fall back instead of treating NaN as "fresh".
function capturedAtMs(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  return Number.isFinite(t) ? t : null;
}

type RefinementHistoryEntry = {
  prompt?: string;
  at?: string;
  by?: string;
};

export function ApprovalsPage() {
  const env = useEnvStore((s) => s.env);
  const [rows, setRows] = useState<ApprovalRow[]>([]);
  const [status, setStatus] = useState<StatusFilter>('pending');
  const [typeFilter, setTypeFilter] = useState<ApprovalTypeFilter>('all');
  const [sla, setSla] = useState<SlaFilter>('all');
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [refining, setRefining] = useState<string | null>(null);
  const [refinementText, setRefinementText] = useState('');
  const [refineHint, setRefineHint] = useState<Record<string, string>>({});
  const [historyOpen, setHistoryOpen] = useState<Record<string, boolean>>({});
  const [lastActionAt, setLastActionAt] = useState<Record<string, string>>({});
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [escalationMap, setEscalationMap] = useState<Record<number, EscalationRow>>({});

  const markSeen = useUnreadStore((s) => s.markSeen);
  const refresh = useCallback(async () => {
    try {
      const qs = `?status=${status}&env=${env}`;
      const fetched = await api.get<ApprovalRow[]>(`/approvals${qs}`);
      setRows(fetched);
      setErr(null);
      // The operator is looking at the approvals list, so anything in
      // it now counts as "seen" — clear the global red dot. Use the
      // max opened_at so a brand-new item that lands between this
      // refresh and the next legitimately re-fires the dot.
      if (status === 'pending') {
        let latest: number = Date.now();
        for (const r of fetched) {
          if (!r.opened_at) continue;
          const t = new Date(r.opened_at).getTime();
          if (Number.isFinite(t) && t > latest) latest = t;
        }
        markSeen('approvals.global', latest);
      }
    } catch (ex) {
      setErr(ex);
    }
  }, [env, status, markSeen]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    let alive = true;
    api
      .get<EscalationRow[]>(`/escalations?state=awaiting_answer&env=${env}`)
      .then((list) => {
        if (!alive) return;
        const map: Record<number, EscalationRow> = {};
        for (const row of list) map[row.id] = row;
        setEscalationMap(map);
      })
      .catch(() => {
        if (!alive) return;
        setEscalationMap({});
      });
    return () => {
      alive = false;
    };
  }, [env]);

  // Live channel + slower polling fallback (gated on editor focus).
  useDataChannel({ onMatch: refresh });
  usePollingFallback(refresh, 20_000);

  const decide = useCallback(
    async (row: ApprovalRow, decision: 'approve' | 'reject') => {
      const isReplyDraft = row.fact_path === 'approval.reply_draft';
      let note = '';
      let reasonTags: RejectionTag[] = [];
      if (decision === 'reject') {
        const isStyleLearning = LEARNING_FACTS.includes(row.fact_path);
        const reason = await dialog.prompt({
          title: '驳回理由',
          description: isStyleLearning
            ? '学习提案驳回不会开启升级。请说明为何不采纳本次沉淀（下次蒸馏会参考此理由）。'
            : '请说明驳回原因。建议使用结构化标签（与回信驳回相同）：tone_too_salesy、premature_pricing、factual_error 等；非回信草稿可能仍会派生升级。',
          placeholder: isStyleLearning
            ? '例：策略段落过于笼统，需更具体的报价节奏说明 …'
            : '[tone_too_salesy][factual_error] 例：称呼不对，且漏了运费条件 …',
          required: true,
          multiline: true,
          confirmLabel: '提交驳回',
          variant: 'danger',
          liveWarning: env === 'LIVE',
        });
        if (reason === null) return;
        note = reason;
        reasonTags = parseRejectionTags(reason);
      } else {
        const ok = await dialog.confirm({
          title: isReplyDraft ? '批准并创建 Gmail 草稿？' : '批准此审批？',
          description: isReplyDraft
            ? '影响：1) 立即创建 Gmail draft；2) 不会自动发送；3) campaign 将继续推进到下一步。'
            : '影响：该审批通过后会恢复对应 run，并按已批准值继续执行。',
          confirmLabel: '批准',
          cancelLabel: '取消',
          variant: 'info',
          liveWarning: env === 'LIVE',
        });
        if (!ok) return;
      }
      setBusy(row.fact_path);
      try {
        await api.post(`/approvals/${row.fact_path}/${decision}`, {
          identity_id: row.identity_id,
          campaign_id: row.campaign_id,
          decided_by: 'console-user',
          env,
          note: note || undefined,
          reason_tags: reasonTags.length ? reasonTags : undefined,
        });
        toast.success(decision === 'approve' ? '已批准' : '已驳回');
        setLastActionAt((m) => ({ ...m, [rowActionKey(row)]: new Date().toISOString() }));
        await refresh();
      } catch (ex) {
        setErr(ex);
        toast.error('提交失败', errorSummary(ex));
      } finally {
        setBusy(null);
      }
    },
    [env, refresh],
  );

  const submitRefine = useCallback(
    async (
      row: ApprovalRow,
      acquireLock: (runId: string | null, startedAtMsArg?: number) => void,
    ) => {
      const prompt = refinementText.trim();
      if (!prompt) return;
      const key = rowKey(row);
      setBusy(row.fact_path);
      try {
        const out = await api.post<{ ok: boolean; hint?: string; run_id?: string }>(
          `/approvals/${row.fact_path}/refine`,
          {
            identity_id: row.identity_id,
            campaign_id: row.campaign_id,
            refinement_prompt: prompt,
            env,
            if_captured_at: row.opened_at,
          },
        );
        acquireLock(out?.run_id ?? null);
        setRefining(null);
        setRefinementText('');
        const hint = out?.hint ?? 'AI 正在重新生成 … 30–60s 后自动刷新。';
        setRefineHint((m) => ({ ...m, [key]: hint }));
        setLastActionAt((m) => ({ ...m, [rowActionKey(row)]: new Date().toISOString() }));
        toast.progress('草稿生成中…', hint, { groupKey: `refine-${key}` });
        await refresh();
      } catch (ex) {
        const conflict = parseConflictBody(ex);
        if (conflict?.error === 'refine_already_in_flight') {
          acquireLock(
            conflict.run_id ?? null,
            startedAtMs(conflict.started_at),
          );
          const m = conflict.message ?? '已有一次优化在进行中。';
          setRefineHint((mm) => ({ ...mm, [key]: m }));
          toast.info('优化已在进行', m);
          setRefining(null);
          setRefinementText('');
        } else if (conflict?.error === 'stale_draft') {
          const m = conflict.message ?? '草稿已变化，请刷新后重试。';
          setRefineHint((mm) => ({ ...mm, [key]: m }));
          toast.error('草稿已过期', m);
          await refresh();
        } else {
          setErr(ex);
          toast.error('请求失败', errorSummary(ex));
        }
      } finally {
        setBusy(null);
      }
    },
    [refresh, env, refinementText],
  );

  const visibleRows = useMemo(() => {
    return rows.filter((r) => {
      if (typeFilter !== 'all' && approvalTypeOf(r.fact_path) !== typeFilter) {
        return false;
      }
      if (sla === 'all') return true;
      const level = slaLevel(r.opened_at);
      if (sla === 'at_risk') return level === 'at_risk' || level === 'breached';
      return level === 'breached';
    });
  }, [rows, sla, typeFilter]);

  const grouped = useMemo(() => {
    const out: Record<string, ApprovalRow[]> = {};
    for (const r of visibleRows) {
      const key = approvalGroupKey(r);
      (out[key] ||= []).push(r);
    }
    return out;
  }, [visibleRows]);

  const selectedRows = useMemo(
    () => visibleRows.filter((r) => selected[rowActionKey(r)]),
    [visibleRows, selected],
  );

  const toggleAllVisible = useCallback(() => {
    const next = !visibleRows.every((r) => selected[rowActionKey(r)]);
    setSelected((prev) => {
      const out = { ...prev };
      for (const r of visibleRows) {
        if (status !== 'pending' || r.status !== 'pending') continue;
        out[rowActionKey(r)] = next;
      }
      return out;
    });
  }, [visibleRows, selected, status]);

  const batchReject = useCallback(async () => {
    const targets = selectedRows.filter((r) => r.status === 'pending');
    if (!targets.length) return;
    const reason = await dialog.prompt({
      title: `批量驳回 ${targets.length} 项`,
      description: '仅用于低风险批量处理。请输入统一驳回原因（可附标签 [tone]/[fact]/[offer]/[risk]/[other]）。',
      placeholder: '[fact] 例：事实未核验完成，先补资料再重提',
      required: true,
      multiline: true,
      confirmLabel: '下一步',
      variant: 'danger',
      liveWarning: env === 'LIVE',
    });
    if (reason == null) return;
    const ok = await dialog.confirm({
      title: `确认批量驳回 ${targets.length} 项？`,
      description: '影响：每条都会写入 reject 决策，并派生对应 escalation。建议仅在规则明确时使用。',
      confirmLabel: '确认批量驳回',
      cancelLabel: '取消',
      variant: 'danger',
      liveWarning: env === 'LIVE',
    });
    if (!ok) return;
    const tags = parseRejectionTags(reason);
    let okCount = 0;
    for (const row of targets) {
      try {
        await api.post(`/approvals/${row.fact_path}/reject`, {
          identity_id: row.identity_id,
          campaign_id: row.campaign_id,
          decided_by: 'console-user',
          env,
          note: reason,
          reason_tags: tags.length ? tags : undefined,
        });
        okCount += 1;
        setLastActionAt((m) => ({ ...m, [rowActionKey(row)]: new Date().toISOString() }));
      } catch {
        // Best-effort batch. Failed rows stay pending and visible.
      }
    }
    setSelected({});
    await refresh();
    toast.success(`批量驳回完成`, `成功 ${okCount}/${targets.length}`);
  }, [selectedRows, env, refresh]);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="text-lg font-semibold">待审批</h1>
        <div className="flex flex-wrap gap-1 rounded border border-slate-200 bg-slate-50 p-0.5 text-xs">
          {(Object.keys(TYPE_FILTER_LABEL) as ApprovalTypeFilter[]).map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => setTypeFilter(t)}
              className={
                'rounded px-2 py-0.5 ' +
                (typeFilter === t
                  ? 'bg-white font-medium text-slate-900 shadow-sm'
                  : 'text-slate-600 hover:text-slate-900')
              }
            >
              {TYPE_FILTER_LABEL[t]}
            </button>
          ))}
        </div>
        <select
          value={status}
          onChange={(e) => setStatus(e.target.value as StatusFilter)}
          className="rounded border border-slate-300 px-2 py-1 text-sm"
        >
          {(Object.keys(STATUS_LABEL) as StatusFilter[]).map((s) => (
            <option key={s} value={s}>{STATUS_LABEL[s]}</option>
          ))}
        </select>
        <select
          value={sla}
          onChange={(e) => setSla(e.target.value as SlaFilter)}
          className="rounded border border-slate-300 px-2 py-1 text-sm"
          title="按超时等级筛选"
        >
          {(Object.keys(SLA_LABEL) as SlaFilter[]).map((s) => (
            <option key={s} value={s}>{`SLA ${SLA_LABEL[s]}`}</option>
          ))}
        </select>
        <button
          onClick={refresh}
          className="rounded border border-slate-300 px-2 py-1 text-sm hover:bg-slate-50"
        >
          刷新
        </button>
        {status === 'pending' && (
          <>
            <button
              type="button"
              onClick={toggleAllVisible}
              className="rounded border border-slate-300 px-2 py-1 text-xs hover:bg-slate-50"
            >
              {visibleRows.length > 0 && visibleRows.every((r) => selected[rowActionKey(r)])
                ? '取消全选'
                : '全选当前'}
            </button>
            <button
              type="button"
              onClick={() => {
                void batchReject();
              }}
              disabled={!selectedRows.length || env !== 'TEST'}
              className="rounded bg-rose-600 px-2 py-1 text-xs text-white disabled:opacity-40"
              title={env !== 'TEST' ? '批量仅在 TEST 开放' : undefined}
            >
              批量驳回（TEST）
            </button>
          </>
        )}
      </div>
      {typeFilter === 'style_learning' && (
        <div className="rounded border border-violet-300 bg-violet-50 px-3 py-2 text-sm text-violet-900">
          <div className="font-medium">学习提案说明</div>
          <ul className="mt-1 list-disc pl-4 text-xs leading-relaxed">
            <li>
              <strong>批准后</strong>写入公司邮件风格（company_style）与回信策略（reply_strategy）policy，供后续 AI 回信参考。
            </li>
            <li>不会直接修改技能 SKILL 文件；稳定策略的「升格」请在{' '}
              <Link to="/learning#promote" className="underline">自主学习 → 策略反哺</Link> 操作。
            </li>
            <li><strong>驳回</strong>不会开启升级（escalation），可继续在「自主学习」页重新生成提案。</li>
          </ul>
        </div>
      )}
      {typeFilter === 'all' && (
        <p className="text-xs text-slate-500">
          学习提案由{' '}
          <Link to="/learning" className="text-sky-700 hover:underline">自主学习</Link>{' '}
          页在编辑批次达阈值后触发蒸馏。
        </p>
      )}
      {!!err && <ErrorAlert error={err} onRetry={refresh} />}
      {Object.keys(grouped).length === 0 && (
        <div className="rounded border border-dashed border-slate-300 p-6 text-center text-sm text-slate-500">
          没有 {STATUS_LABEL[status]} 的审批。
        </div>
      )}
      {Object.entries(grouped).map(([key, items]) => {
        const styleGroup = isStyleLearningGroup(items);
        const ctx = (items[0]?.context ?? {}) as Record<string, unknown>;
        const [identityId, campaignId] = key.split('::');
        const handle = items[0]?.handle;
        return (
          <section
            key={key}
            className={
              'rounded border bg-white ' +
              (styleGroup ? 'border-violet-200' : 'border-slate-200')
            }
          >
            <header
              className={
                'flex items-center justify-between border-b px-3 py-2 text-sm ' +
                (styleGroup
                  ? 'border-violet-100 bg-violet-50'
                  : 'border-slate-100 bg-slate-50')
              }
            >
              <div>
                {styleGroup ? (
                  <>
                    <span className="font-medium text-violet-900">
                      {styleLearningGroupTitle(ctx, items[0]?.fact_path)}
                    </span>
                    <span className="ml-2 text-[11px] text-violet-700">
                      汇总多位 KOL 的编辑与会话，批准后写入全局 policy
                    </span>
                    <Link
                      to="/learning"
                      className="ml-2 text-[11px] text-sky-700 hover:underline"
                    >
                      查看学习进度
                    </Link>
                  </>
                ) : (
                  <>
                    <Link
                      to={`/kols/${identityId}?campaign_id=${encodeURIComponent(campaignId)}`}
                      className="font-medium text-sky-700 hover:underline"
                    >
                      {handle ? `@${handle}` : `KOL #${identityId}`}
                    </Link>
                    <span className="ml-2 text-slate-500">
                      {campaignId && campaignId !== 'null'
                        ? `campaign ${campaignId}`
                        : '全局 / 无 campaign'}
                    </span>
                  </>
                )}
              </div>
              <span className="text-xs text-slate-500">{items.length} 项待处理</span>
            </header>
            <ul className="divide-y divide-slate-100">
              {items.map((r) => (
                <ApprovalRowItem
                  key={r.fact_path}
                  row={r}
                  env={env}
                  busy={busy}
                  status={status}
                  refining={refining}
                  refinementText={refinementText}
                  refineHintText={refineHint[rowKey(r)]}
                  lastActionAt={lastActionAt[rowActionKey(r)]}
                  isHistoryOpen={!!historyOpen[rowKey(r)]}
                  selected={!!selected[rowActionKey(r)]}
                  escalation={r.linked_escalation_id != null
                    ? escalationMap[r.linked_escalation_id]
                    : undefined}
                  onToggleHistory={() =>
                    setHistoryOpen((m) => ({ ...m, [rowKey(r)]: !m[rowKey(r)] }))
                  }
                  onToggleSelected={() =>
                    setSelected((m) => ({ ...m, [rowActionKey(r)]: !m[rowActionKey(r)] }))
                  }
                  onSetRefiningKey={(key) => setRefining(key)}
                  onChangeRefinementText={setRefinementText}
                  onSubmitRefine={submitRefine}
                  onDecide={decide}
                  onRefresh={refresh}
                />
              ))}
            </ul>
          </section>
        );
      })}
    </div>
  );
}

type ApprovalRowItemProps = {
  row: ApprovalRow;
  env: 'TEST' | 'LIVE';
  busy: string | null;
  status: StatusFilter;
  refining: string | null;
  refinementText: string;
  refineHintText: string | undefined;
  lastActionAt: string | undefined;
  isHistoryOpen: boolean;
  selected: boolean;
  escalation?: EscalationRow;
  onToggleHistory: () => void;
  onToggleSelected: () => void;
  onSetRefiningKey: (key: string | null) => void;
  onChangeRefinementText: (text: string) => void;
  onSubmitRefine: (
    row: ApprovalRow,
    acquireLock: (runId: string | null, startedAtMsArg?: number) => void,
  ) => Promise<void>;
  onDecide: (row: ApprovalRow, decision: 'approve' | 'reject') => Promise<void>;
  onRefresh: () => Promise<void>;
};

function ApprovalRowItem({
  row,
  env,
  busy,
  status,
  refining,
  refinementText,
  refineHintText,
  lastActionAt,
  isHistoryOpen,
  selected,
  escalation,
  onToggleHistory,
  onToggleSelected,
  onSetRefiningKey,
  onChangeRefinementText,
  onSubmitRefine,
  onDecide,
  onRefresh,
}: ApprovalRowItemProps) {
  const k = rowKey(row);
  const isReplyDraft = row.fact_path === 'approval.reply_draft';
  const isStyleLearning = LEARNING_FACTS.includes(row.fact_path);
  const useStructuredPanel =
    isReplyDraft && status === 'pending' && row.status === 'pending';
  const ctx = (row.context ?? {}) as Record<string, unknown>;
  const previousDrafts = Array.isArray(ctx.previous_drafts)
    ? (ctx.previous_drafts as PreviousDraft[])
    : [];
  const refinementHistory = Array.isArray(ctx.refinement_history)
    ? (ctx.refinement_history as RefinementHistoryEntry[])
    : [];
  const refineLock = useInflightLock(
    `draft.lock.refine:${row.identity_id}:${row.campaign_id}`,
  );

  type EditLearning = {
    was_edited?: boolean;
    edit_distance?: number;
    normalized_agent_body?: string;
    normalized_sent_body?: string;
  };
  const [editLearning, setEditLearning] = useState<EditLearning | null>(null);

  useEffect(() => {
    if (!isReplyDraft) {
      setEditLearning(null);
      return;
    }
    let alive = true;
    const params = new URLSearchParams({
      env,
      identity_id: String(row.identity_id),
      campaign_id: row.campaign_id,
      limit: '1',
    });
    api
      .get<{ events: Array<{ payload?: EditLearning }> }>(`/learning/edit-events?${params}`)
      .then((res) => {
        if (!alive) return;
        setEditLearning(res.events?.[0]?.payload ?? null);
      })
      .catch(() => {
        if (!alive) return;
        setEditLearning(null);
      });
    return () => {
      alive = false;
    };
  }, [isReplyDraft, row.identity_id, row.campaign_id, env, row.opened_at, lastActionAt]);

  // Release the refine lock as soon as a newer draft revision lands —
  // otherwise the "优化生成中…" banner sticks for the full 5-min TTL
  // even though the new draft is already visible above.
  const rowCapturedAtMs = capturedAtMs(row.opened_at);
  const { release: releaseRefineLock } = refineLock;
  useEffect(() => {
    if (!refineLock.locked || refineLock.startedAtMs == null) return;
    if (rowCapturedAtMs == null) return;
    if (rowCapturedAtMs > refineLock.startedAtMs) releaseRefineLock();
  }, [
    refineLock.locked,
    refineLock.startedAtMs,
    rowCapturedAtMs,
    releaseRefineLock,
  ]);

  return (
    <li
      className={
        'flex flex-wrap items-start gap-3 p-3 text-sm ' +
        (isStyleLearning ? 'border-l-4 border-l-violet-400 bg-violet-50/30' : '')
      }
    >
      {status === 'pending' && row.status === 'pending' && (
        <input
          type="checkbox"
          checked={selected}
          onChange={onToggleSelected}
          className="mt-1"
          title="加入批量操作"
        />
      )}
      <FactKeyChip factKey={row.fact_path} variant="filled" />
      {isStyleLearning && (
        <span className="rounded bg-violet-200 px-2 py-0.5 text-[11px] font-medium text-violet-900">
          学习提案
        </span>
      )}
      {slaLevel(row.opened_at) === 'at_risk' && (
        <span className="rounded bg-amber-100 px-2 py-0.5 text-[11px] text-amber-800">
          SLA 30m+
        </span>
      )}
      {slaLevel(row.opened_at) === 'breached' && (
        <span className="rounded bg-rose-100 px-2 py-0.5 text-[11px] text-rose-800">
          SLA 2h+
        </span>
      )}
      <TimeAgo iso={row.opened_at} prefix="提交于" className="text-[11px] text-slate-500" />
      {row.linked_escalation_id != null && (
        <Link
          to={`/escalations/${row.linked_escalation_id}`}
          className="rounded bg-rose-100 px-2 py-0.5 text-xs text-rose-700 hover:bg-rose-200"
        >
          升级 #{row.linked_escalation_id}
        </Link>
      )}
      <div className="flex w-full flex-col gap-2">
        {useStructuredPanel ? (
          <ApprovalDetailPanel
            factPath={row.fact_path}
            context={row.context}
            identityId={row.identity_id}
            campaignId={row.campaign_id}
            env={env}
            decidedBy="console-user"
            editLearning={editLearning}
            approveButtonLabel="批准并创建 Gmail 草稿"
            onApproved={() => {
              void onRefresh();
            }}
            onRejected={() => {
              void onRefresh();
            }}
          />
        ) : (
          <ApprovalContextCard
            factPath={row.fact_path}
            context={row.context}
            identityId={row.identity_id}
            campaignId={row.campaign_id}
            env={env}
          />
        )}
        {!useStructuredPanel && isReplyDraft && editLearning?.was_edited && (
          <DraftEditDiffPanel
            agentBody={
              typeof ctx.draft === 'object' && ctx.draft !== null
                ? String((ctx.draft as Record<string, unknown>).body ?? '')
                : ''
            }
            editLearning={editLearning}
          />
        )}
        {escalation && (
          <div className="rounded border border-rose-200 bg-rose-50 px-2 py-1 text-xs text-rose-900">
            <div className="font-medium">升级上下文：{escalation.reason}</div>
            {escalation.suggested_question && (
              <div className="mt-0.5 line-clamp-2">{escalation.suggested_question}</div>
            )}
          </div>
        )}
        {refineHintText && (
          <div className="rounded bg-amber-50 px-2 py-1 text-xs text-amber-800">
            {refineHintText}
          </div>
        )}
        {lastActionAt && (
          <div className="rounded bg-slate-100 px-2 py-1 text-xs text-slate-600">
            最近动作：<TimeAgo iso={lastActionAt} />
          </div>
        )}
        {refineLock.locked && (
          <div className="rounded bg-sky-50 px-2 py-1 text-xs text-sky-800">
            优化生成中… 约 {refineLock.remainingSeconds}s 后可再次操作；
            刷新或换 tab 也不会重复触发。
          </div>
        )}
        {(previousDrafts.length > 0 || refinementHistory.length > 0) && (
          <div className="rounded border border-slate-200 bg-slate-50 px-2 py-1">
            <button
              type="button"
              onClick={onToggleHistory}
              className="text-xs text-slate-700 hover:underline"
            >
              {isHistoryOpen ? '▼' : '▶'} 历史版本 ({previousDrafts.length})
            </button>
            {isHistoryOpen && (
              <ol className="mt-1 space-y-2 text-xs text-slate-700">
                {previousDrafts.map((d, i) => {
                  const refEntry = refinementHistory[i];
                  return (
                    <li
                      key={i}
                      className="rounded border border-slate-200 bg-white p-2"
                    >
                      <div className="font-mono text-[11px] text-slate-500">
                        v-{previousDrafts.length - i}
                        {refEntry?.at && <> · <TimeAgo iso={refEntry.at} /></>}
                        {refEntry?.by ? ` · ${refEntry.by}` : ''}
                      </div>
                      {refEntry?.prompt && (
                        <div className="mt-1 text-[11px] italic text-slate-600">
                          优化指令：{refEntry.prompt}
                        </div>
                      )}
                      {d.subject != null && (
                        <div className="mt-1">
                          <span className="text-slate-500">主题：</span>{' '}
                          {String(d.subject)}
                        </div>
                      )}
                      {d.body != null && (
                        <pre className="mt-1 whitespace-pre-wrap break-words">
                          {String(d.body).length > 600
                            ? `${String(d.body).slice(0, 600)}…`
                            : String(d.body)}
                        </pre>
                      )}
                    </li>
                  );
                })}
              </ol>
            )}
          </div>
        )}
        {refining === k && (
          <div data-editing className="rounded border border-sky-300 bg-sky-50 p-2">
            <textarea
              rows={4}
              value={refinementText}
              onChange={(e) => onChangeRefinementText(e.target.value)}
              placeholder="告诉 AI 改什么：语气、加什么、删什么、强调哪个事实，等等。"
              className="w-full rounded border border-sky-300 bg-white p-2 text-xs"
            />
            <div className="mt-1 flex gap-2">
              <button
                disabled={
                  busy === row.fact_path
                  || refineLock.locked
                  || !refinementText.trim()
                }
                onClick={() => onSubmitRefine(row, refineLock.acquire)}
                className="rounded bg-sky-600 px-3 py-1 text-xs text-white hover:bg-sky-700 disabled:opacity-40"
              >
                提交
              </button>
              <button
                disabled={busy === row.fact_path}
                onClick={() => {
                  onSetRefiningKey(null);
                  onChangeRefinementText('');
                }}
                className="rounded border border-slate-300 bg-white px-3 py-1 text-xs hover:bg-slate-50"
              >
                取消
              </button>
            </div>
          </div>
        )}
      </div>
      <div className="flex flex-shrink-0 gap-2">
        {isReplyDraft && status === 'pending' && row.status === 'pending' && (
          <button
            disabled={busy === row.fact_path || refineLock.locked}
            onClick={() => {
              onSetRefiningKey(refining === k ? null : k);
              onChangeRefinementText('');
            }}
            className="rounded bg-sky-600 px-3 py-1 text-xs text-white hover:bg-sky-700 disabled:opacity-40"
          >
            {refineLock.locked
              ? `生成中… (${refineLock.remainingSeconds}s)`
              : '优化 / 重写'}
          </button>
        )}
        {!useStructuredPanel && (
          <>
            <button
              disabled={busy === row.fact_path || status !== 'pending'}
              onClick={() => onDecide(row, 'approve')}
              className="rounded bg-emerald-600 px-3 py-1 text-xs text-white hover:bg-emerald-700 disabled:opacity-40"
            >
              {isReplyDraft ? '批准并创建 Gmail 草稿' : '批准'}
            </button>
            <button
              disabled={busy === row.fact_path || status !== 'pending'}
              onClick={() => onDecide(row, 'reject')}
              className="rounded bg-rose-600 px-3 py-1 text-xs text-white hover:bg-rose-700 disabled:opacity-40"
            >
              驳回
            </button>
          </>
        )}
      </div>
    </li>
  );
}
