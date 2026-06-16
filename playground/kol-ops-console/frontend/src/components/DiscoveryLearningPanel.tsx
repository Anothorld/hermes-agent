import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import { TimeAgo } from './inputs/TimeAgo';
import { errorSummary } from '../lib/errors';
import { toast } from '../lib/store';
import type { DecisionTag } from './dialogs/ShortlistDecisionFeedbackDialog';

type FailedCapture = {
  audit_id: number;
  ts?: string;
  capture_action?: string;
  error?: string;
  decision_count?: number;
  sku?: string | null;
  campaign_id?: string;
  env?: string;
  replayed_at?: string;
};

type PendingProposal = {
  identity_id?: number;
  captured_at?: string;
  scope?: string;
  value?: {
    scope?: string;
    group_kind?: string;
    group_key?: string;
    sample_count?: number;
    sample_identity_count?: number;
    action_mix?: Record<string, number>;
  };
};

type DecisionEvent = {
  id: number;
  ts?: string;
  payload?: {
    action?: string;
    sku?: string | null;
    category?: string | null;
    reason_tags?: string[];
    comment?: string;
  };
};

type CategoryRow = {
  sku: string;
  category: string;
  source: string;
  updated_at?: string;
};

export type DiscoveryProgress = {
  fresh_decisions?: number;
  batch_threshold?: number;
  pending_proposals?: number;
  last_distill_job?: {
    run_id?: number;
    status?: string;
    started_at?: string;
    finished_at?: string;
    triggered_by?: string;
    error_summary?: string | null;
  } | null;
  groups?: Array<{
    group_kind: string;
    group_key: string;
    scope: string;
    fresh_samples: number;
    batch_threshold: number;
    ready_for_distill: boolean;
    has_pending_proposal: boolean;
  }>;
};

type LearnedCriteria = {
  sku?: string | null;
  category?: string | null;
  spu_md?: string;
  category_md?: string;
};

/**
 * Discovery 决策学习面板（学习页）。
 *
 * 展示：待审批的发现标准提案（跳转审批中心）、挖掘出的新标签提案
 * （可在此直接批准/拒绝）、SKU 品类映射、最近的操作员决策样本。
 */
export default function DiscoveryLearningPanel({
  env,
  progress,
}: {
  env: string;
  progress?: DiscoveryProgress | null;
}) {
  const [proposals, setProposals] = useState<PendingProposal[]>([]);
  const [proposedTags, setProposedTags] = useState<DecisionTag[]>([]);
  const [categories, setCategories] = useState<CategoryRow[]>([]);
  const [events, setEvents] = useState<DecisionEvent[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [busyTag, setBusyTag] = useState<string | null>(null);
  const [criteriaSku, setCriteriaSku] = useState<string | null>(null);
  const [criteria, setCriteria] = useState<LearnedCriteria | null>(null);
  const [criteriaErr, setCriteriaErr] = useState<string | null>(null);
  const [failedCaptures, setFailedCaptures] = useState<FailedCapture[]>([]);
  const [replayBusy, setReplayBusy] = useState<number | null>(null);

  const viewableSkus = useMemo(() => {
    const skus = new Set<string>();
    for (const g of progress?.groups ?? []) {
      if (g.group_kind === 'spu' && g.group_key) skus.add(g.group_key);
    }
    for (const e of events) {
      const s = e.payload?.sku;
      if (s) skus.add(String(s));
    }
    for (const c of categories) {
      if (c.sku) skus.add(c.sku);
    }
    return [...skus].sort();
  }, [progress?.groups, events, categories]);

  const viewCriteria = async (sku: string) => {
    if (criteriaSku === sku) {
      setCriteriaSku(null);
      setCriteria(null);
      return;
    }
    setCriteriaSku(sku);
    setCriteria(null);
    setCriteriaErr(null);
    try {
      const r = await api.get<LearnedCriteria>(
        `/learning/discovery-criteria?sku=${encodeURIComponent(sku)}&env=${env}`,
      );
      setCriteria(r);
    } catch (ex) {
      setCriteriaErr(errorSummary(ex));
    }
  };

  const refresh = useCallback(async () => {
    try {
      const [prop, tags, cats, evts, failed] = await Promise.all([
        api.get<{ proposals: PendingProposal[] }>(
          `/learning/pending-discovery-proposals?env=${env}`,
        ),
        api.get<{ tags: DecisionTag[] }>('/learning/discovery-tags?status=proposed'),
        api.get<{ categories: CategoryRow[] }>('/learning/product-categories'),
        api.get<{ events: DecisionEvent[] }>(
          `/learning/shortlist-decision-events?env=${env}&limit=20`,
        ),
        api.get<{ items: FailedCapture[] }>(
          '/learning/failed-shortlist-captures?limit=20',
        ),
      ]);
      setProposals(prop.proposals ?? []);
      setProposedTags(tags.tags ?? []);
      setCategories(cats.categories ?? []);
      setEvents(evts.events ?? []);
      setFailedCaptures(failed.items ?? []);
      setErr(null);
    } catch (ex) {
      setErr(errorSummary(ex));
    }
  }, [env]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const replayCapture = async (auditId: number) => {
    setReplayBusy(auditId);
    try {
      const r = await api.post<{ already_replayed?: boolean; recorded?: number }>(
        '/learning/replay-shortlist-capture',
        { audit_id: auditId },
      );
      if (r.already_replayed) {
        toast.success('该样本此前已补录', `audit #${auditId}`);
      } else {
        toast.success('学习样本已补录', `写入 ${r.recorded ?? 0} 条事件`);
      }
      await refresh();
    } catch (ex) {
      toast.error('补录失败', errorSummary(ex));
    } finally {
      setReplayBusy(null);
    }
  };

  const decideTag = async (tag: string, decision: 'approved' | 'rejected') => {
    setBusyTag(tag);
    try {
      await api.post('/learning/discovery-tags/decide', { tag, decision });
      toast.success(
        decision === 'approved' ? '标签已启用' : '标签提案已拒绝',
        tag,
      );
      await refresh();
    } catch (ex) {
      toast.error('标签操作失败', errorSummary(ex));
    } finally {
      setBusyTag(null);
    }
  };

  const lastDistillFailed =
    progress?.last_distill_job?.status === 'error' &&
    Boolean(progress.last_distill_job.error_summary);

  return (
    <section className="rounded border border-slate-200 bg-white p-3">
      <h2 className="text-sm font-medium text-slate-800">Discovery 决策学习</h2>
      <p className="mt-1 text-xs text-slate-500">
        批准 / 移除 / 转移 shortlist 时填写的标签与评论会在夜间汇总成「KOL
        评判标准」（按产品与品类两条线），审批通过后自动用于下一轮 KOL 发现。
      </p>
      {lastDistillFailed && (
        <div className="mt-2 rounded border border-amber-300 bg-amber-50 px-2 py-1.5 text-[11px] text-amber-900">
          <div className="font-medium">上次夜间蒸馏未成功</div>
          <div className="mt-0.5">{progress?.last_distill_job?.error_summary}</div>
          {progress?.last_distill_job?.finished_at && (
            <div className="mt-0.5 text-amber-800">
              时间：<TimeAgo iso={progress.last_distill_job.finished_at} />
            </div>
          )}
          <div className="mt-1 text-amber-800">
            样本已满时标签会显示「待蒸馏」；修复 LLM 后可在学习页手动运行「distill」套件。
          </div>
        </div>
      )}
      {err && (
        <div className="mt-2 rounded border border-rose-300 bg-rose-50 px-2 py-1 text-[11px] text-rose-800">
          {err}
        </div>
      )}

      {progress && (progress.groups?.length ?? 0) > 0 && (
        <div className="mt-2 rounded border border-slate-100 bg-slate-50/60 p-2">
          <div className="text-xs font-medium text-slate-700">
            蒸馏批次进度（每组满 {progress.batch_threshold ?? '?'} 条样本生成一份提案）
          </div>
          <ul className="mt-1 flex flex-wrap gap-1.5 text-[11px]">
            {(progress.groups ?? []).map((g) => (
              <li
                key={g.scope}
                className={`rounded-full border px-2 py-0.5 ${
                  g.has_pending_proposal
                    ? 'border-amber-300 bg-amber-50 text-amber-900'
                    : g.ready_for_distill
                      ? 'border-emerald-300 bg-emerald-50 text-emerald-900'
                      : 'border-slate-200 bg-white text-slate-600'
                }`}
                title={g.scope}
              >
                {g.group_kind === 'category' ? '品类' : '产品'} {g.group_key} ·{' '}
                {g.fresh_samples}/{g.batch_threshold}
                {g.has_pending_proposal
                  ? ' · 待审批'
                  : g.ready_for_distill
                    ? lastDistillFailed
                      ? ' · 待蒸馏'
                      : ' · 今夜可蒸馏'
                    : ''}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-2">
        <div className="rounded border border-slate-100 p-2">
          <div className="text-xs font-medium text-slate-700">
            待审批的发现标准提案（{proposals.length}）
          </div>
          {proposals.length === 0 ? (
            <div className="mt-1 text-[11px] italic text-slate-400">
              暂无 — 样本积累到批次阈值后由夜间任务生成。
            </div>
          ) : (
            <ul className="mt-1 space-y-1 text-[11px]">
              {proposals.map((p, i) => {
                const v = p.value ?? {};
                return (
                  <li key={`${v.scope ?? i}`} className="rounded bg-amber-50 px-2 py-1">
                    <span className="font-medium text-amber-900">
                      {v.group_kind === 'category' ? '品类' : '产品'} {v.group_key}
                    </span>
                    <span className="ml-1 text-slate-600">
                      · {v.sample_count ?? '?'} 条样本
                    </span>
                    {p.captured_at && <TimeAgo iso={p.captured_at} className="ml-1 text-slate-400" />}
                  </li>
                );
              })}
            </ul>
          )}
          <Link
            to="/approvals"
            className="mt-2 inline-block rounded border border-emerald-300 bg-emerald-50 px-2 py-0.5 text-[11px] text-emerald-800 hover:bg-emerald-100"
          >
            去审批中心处理 →
          </Link>
        </div>

        <div className="rounded border border-slate-100 p-2">
          <div className="text-xs font-medium text-slate-700">
            新标签提案（评论高频原因，{proposedTags.length}）
          </div>
          {proposedTags.length === 0 ? (
            <div className="mt-1 text-[11px] italic text-slate-400">
              暂无 — 当某个原因在评论中反复出现时，AI 会建议把它变成快捷标签。
            </div>
          ) : (
            <ul className="mt-1 space-y-1 text-[11px]">
              {proposedTags.map((t) => (
                <li
                  key={t.tag}
                  className="flex items-center justify-between rounded bg-sky-50 px-2 py-1"
                >
                  <span>
                    <span className="font-medium text-sky-900">{t.label_zh}</span>
                    <span className="ml-1 font-mono text-slate-400">{t.tag}</span>
                  </span>
                  <span className="flex gap-1">
                    <button
                      type="button"
                      disabled={busyTag === t.tag}
                      onClick={() => void decideTag(t.tag, 'approved')}
                      className="rounded bg-emerald-600 px-2 py-0.5 text-white hover:bg-emerald-700 disabled:opacity-50"
                    >
                      启用
                    </button>
                    <button
                      type="button"
                      disabled={busyTag === t.tag}
                      onClick={() => void decideTag(t.tag, 'rejected')}
                      className="rounded border border-slate-300 bg-white px-2 py-0.5 text-slate-600 hover:bg-slate-50 disabled:opacity-50"
                    >
                      拒绝
                    </button>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="rounded border border-slate-100 p-2">
          <div className="text-xs font-medium text-slate-700">
            产品品类映射（{categories.length}）
          </div>
          {categories.length === 0 ? (
            <div className="mt-1 text-[11px] italic text-slate-400">
              暂无 — 夜间任务会为有学习样本的产品自动归类，也可在产品页手动设置。
            </div>
          ) : (
            <ul className="mt-1 space-y-0.5 text-[11px]">
              {categories.map((c) => (
                <li key={c.sku}>
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-slate-700">{c.sku}</span>
                    <span className="rounded bg-indigo-50 px-1.5 py-0.5 font-mono text-indigo-800">
                      {c.category}
                    </span>
                    <span className="text-slate-400">
                      {c.source === 'operator' ? '人工' : 'AI'}
                    </span>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="rounded border border-slate-100 p-2">
          <div className="text-xs font-medium text-slate-700">
            已学标准（按产品，{viewableSkus.length}）
          </div>
          {viewableSkus.length === 0 ? (
            <div className="mt-1 text-[11px] italic text-slate-400">
              暂无 — 有决策样本的产品会出现在此，可查看已批准的标准。
            </div>
          ) : (
            <ul className="mt-1 space-y-0.5 text-[11px]">
              {viewableSkus.map((sku) => (
                <li key={sku}>
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-slate-700">{sku}</span>
                    <button
                      type="button"
                      onClick={() => void viewCriteria(sku)}
                      className="rounded border border-slate-300 bg-white px-1.5 py-0.5 text-[10px] text-slate-600 hover:bg-slate-50"
                    >
                      {criteriaSku === sku ? '收起标准' : '查看已学标准'}
                    </button>
                  </div>
                  {criteriaSku === sku && (
                    <div className="mt-1 space-y-1">
                      {criteriaErr ? (
                        <div className="text-rose-700">{criteriaErr}</div>
                      ) : criteria == null ? (
                        <div className="text-slate-400">加载中…</div>
                      ) : !criteria.spu_md && !criteria.category_md ? (
                        <div className="italic text-slate-400">
                          该产品暂无已批准的发现标准 — 样本够数并审批通过后出现。
                        </div>
                      ) : (
                        <>
                          {criteria.spu_md && (
                            <pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded border border-slate-200 bg-white p-1.5 text-[10px] text-slate-700">
                              {`【产品级标准】\n${criteria.spu_md}`}
                            </pre>
                          )}
                          {criteria.category_md && (
                            <pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded border border-slate-200 bg-white p-1.5 text-[10px] text-slate-700">
                              {`【品类级标准（${criteria.category ?? ''}）】\n${criteria.category_md}`}
                            </pre>
                          )}
                        </>
                      )}
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>

        {failedCaptures.length > 0 && (
          <div className="rounded border border-rose-200 bg-rose-50/40 p-2 md:col-span-2">
            <div className="text-xs font-medium text-rose-900">
              待补录的学习样本（{failedCaptures.length}）
            </div>
            <p className="mt-0.5 text-[11px] text-rose-800">
              以下操作已成功，但学习通道当时不可用。点击「补录」写入 Bridge，无需重试原操作。
            </p>
            <ul className="mt-1 space-y-1 text-[11px]">
              {failedCaptures.map((f) => (
                <li
                  key={f.audit_id}
                  className="flex flex-wrap items-center justify-between gap-2 rounded border border-rose-100 bg-white px-2 py-1"
                >
                  <span className="text-slate-700">
                    {f.capture_action === 'approve'
                      ? '批准'
                      : f.capture_action === 'remove'
                        ? '移除'
                        : f.capture_action === 'transfer'
                          ? '转移'
                          : f.capture_action}
                    {f.sku ? ` · ${f.sku}` : ''}
                    {f.decision_count != null && ` · ${f.decision_count} 条`}
                    {f.ts && <TimeAgo iso={f.ts} className="ml-1 text-slate-400" />}
                  </span>
                  <button
                    type="button"
                    disabled={replayBusy === f.audit_id}
                    onClick={() => void replayCapture(f.audit_id)}
                    className="rounded bg-rose-600 px-2 py-0.5 text-white hover:bg-rose-700 disabled:opacity-50"
                  >
                    {replayBusy === f.audit_id ? '补录中…' : '补录'}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}

        <div className="rounded border border-slate-100 p-2">
          <div className="text-xs font-medium text-slate-700">最近决策样本（20 条内）</div>
          {events.length === 0 ? (
            <div className="mt-1 text-[11px] italic text-slate-400">
              暂无 — 在产品页 shortlist 上批准 / 移除 / 转移 KOL 即开始积累。
            </div>
          ) : (
            <ul className="mt-1 max-h-48 space-y-1 overflow-y-auto text-[11px]">
              {events.map((e) => {
                const p = e.payload ?? {};
                const actionLabel =
                  p.action === 'approve' ? '批准' : p.action === 'remove' ? '移除' : '转移';
                return (
                  <li key={e.id} className="rounded bg-slate-50 px-2 py-1">
                    <span
                      className={
                        p.action === 'approve' ? 'text-emerald-700' : 'text-rose-700'
                      }
                    >
                      {actionLabel}
                    </span>
                    <span className="ml-1 text-slate-600">
                      {p.sku ?? ''}
                      {(p.reason_tags ?? []).length > 0 && ` · ${(p.reason_tags ?? []).join(', ')}`}
                    </span>
                    {p.comment && (
                      <span className="ml-1 text-slate-500">「{p.comment.slice(0, 60)}」</span>
                    )}
                    {e.ts && <TimeAgo iso={e.ts} className="ml-1 text-slate-400" />}
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </div>
    </section>
  );
}
