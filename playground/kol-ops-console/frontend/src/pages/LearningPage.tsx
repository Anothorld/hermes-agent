import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import DiscoveryLearningPanel from '../components/DiscoveryLearningPanel';
import DraftEditDiffPanel from '../components/DraftEditDiffPanel';
import { LearningChannelTrends } from '../components/LearningChannelTrends';
import { LearningNextBatchPreview } from '../components/LearningNextBatchPreview';
import OutcomePromotionPanel from '../components/OutcomePromotionPanel';
import StrategyPromotionPanel from '../components/StrategyPromotionPanel';
import { LearningManualTriggerSection } from '../components/LearningManualTriggerSection';
import {
  LearningEmptySamplesHint,
  LearningWorkflowStepper,
} from '../components/LearningWorkflowStepper';
import { ErrorAlert } from '../components/feedback/ErrorAlert';
import { TimeAgo } from '../components/inputs/TimeAgo';
import { dialog } from '../components/dialogs/useDialog';
import { toast, useEnvStore } from '../lib/store';
import { errorSummary } from '../lib/errors';
import { usePollingFallback } from '../hooks/usePollingFallback';
import { isAsyncLearningJob, pollLearningJob } from '../lib/learningJobs';
import {
  formatRunSummary,
  goalLabel,
  jobLabel,
  jobStatusLabel,
  policyScopeLabel,
  promoteReasonLabel,
} from '../constants/domainLabels';
import { REJECT_TAG_LABELS, type RejectTag } from '../constants/rejectTags';

type Overview = {
  env: string;
  jobs_disabled: boolean;
  style_in_hints: boolean;
  batch_threshold: number;
  edit_stats: {
    total_events: number;
    unconsumed: number;
    edited_unconsumed: number;
    edited_available?: number;
    edited_queued_in_pending?: number;
    consumed: number;
    ready_for_distill: boolean;
  };
  pending_style_proposals: Array<{
    scope: string;
    identity_id?: number;
    sample_count?: number;
    batch_threshold?: number;
    llm_used?: boolean;
    captured_at?: string;
  }>;
  approved_style_proposals: number;
  policy_versions: Record<
    string,
    { version: number | null; updated_at: string | null; content_chars: number }
  >;
  promote_eligibility: Array<{
    goal: string;
    skill: string;
    eligible: boolean;
    reason: string;
    approvals: number;
    age_days: number;
  }>;
  last_runs: JobRun[];
  run_summary: Record<string, number>;
  edit_distance_trend?: EditDistanceTrend;
  edit_stats_by_scope?: Array<{
    scope: string;
    owner_user_id?: number | null;
    edited_available: number;
    edited_queued_in_pending: number;
    ready_for_distill: boolean;
    has_pending_proposal: boolean;
  }>;
  convergence_alert?: {
    worsening: boolean;
    delta: number | null;
    threshold: number;
    guard_basis?: 'after_last_approval' | 'recent_vs_prior';
    hint: string;
  };
  outcome_learning?: {
    total_retros: number;
    fresh_retros: number;
    fresh_available?: number;
    fresh_queued_in_pending?: number;
    by_class: { failure?: number; success?: number; partial?: number };
    ready_for_synthesis: boolean;
    has_pending_proposal: boolean;
    total: number;
    failures: number;
    batch_size: number;
    min_failures: number;
  };
  style_approval_markers?: Array<{
    at?: string;
    scope?: string;
    sample_count?: number;
  }>;
  channel_trends?: {
    edits?: EditDistanceTrend;
    rejects?: { buckets?: Array<{ bucket: string; count: number }> };
    outcome_retros?: { buckets?: Array<{ bucket: string; count: number }> };
    shortlist_decisions?: { buckets?: Array<{ bucket: string; count: number }> };
  };
  promote_outcome_eligibility?: Overview['promote_eligibility'];
  discovery_learning?: import('../components/DiscoveryLearningPanel').DiscoveryProgress;
};

type EditDistanceBucket = {
  bucket: string;
  count: number;
  edited_count: number;
  was_edited_rate: number | null;
  avg_edit_distance: number | null;
  p50_edit_distance: number | null;
  p90_edit_distance: number | null;
};

type EditDistanceTrend = {
  overall: {
    count: number;
    edited_count: number;
    was_edited_rate: number | null;
    avg_edit_distance: number | null;
  };
  buckets: EditDistanceBucket[];
  recent_vs_prior: {
    recent_avg: number | null;
    prior_avg: number | null;
    delta: number | null;
  };
};

type JobRun = {
  id?: number;
  job_name: string;
  env?: string;
  status: string;
  triggered_by?: string;
  started_at?: string;
  finished_at?: string;
  duration_ms?: number;
  output?: Record<string, unknown>;
  error_message?: string;
};

type EditEvent = {
  id?: number;
  identity_id?: number;
  campaign_id?: string;
  goal?: string;
  ts?: string;
  payload?: Record<string, unknown>;
};

type RejectEvent = {
  id?: number;
  identity_id?: number;
  campaign_id?: string;
  goal?: string;
  ts?: string;
  payload?: Record<string, unknown>;
};

const JOB_OPTIONS = [
  'reconcile_sent',
  'apply_reject_policy',
  'apply_edit_policy',
  'apply_pricing_calibration_policy',
  'auto_pricing_campaigns',
  'snapshot_fact_corrections',
  'sync_failure_examples',
  'classifier_eval_deterministic',
];

function rejectTagLabel(tag: string): string {
  if (tag in REJECT_TAG_LABELS) {
    return REJECT_TAG_LABELS[tag as RejectTag];
  }
  return tag;
}

export function LearningPage() {
  const env = useEnvStore((s) => s.env);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [jobRuns, setJobRuns] = useState<JobRun[]>([]);
  const [editEvents, setEditEvents] = useState<EditEvent[]>([]);
  const [rejectEvents, setRejectEvents] = useState<RejectEvent[]>([]);
  const [policyPreview, setPolicyPreview] = useState<{
    scope: string;
    md: string;
  } | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [suite, setSuite] = useState<string>('nightly');
  const [dryRun, setDryRun] = useState(true);
  const [expandedRun, setExpandedRun] = useState<number | null>(null);
  const [statusFilter, setStatusFilter] = useState<string>('');

  const refreshOverview = useCallback(async () => {
    try {
      const ov = await api.get<Overview>(`/learning/overview?env=${env}&runs_limit=25`);
      setOverview(ov);
      setJobRuns(ov.last_runs ?? []);
      setErr(null);
    } catch (ex) {
      setErr(ex);
    }
  }, [env]);

  const refreshSignals = useCallback(async () => {
    try {
      const [ed, rej] = await Promise.all([
        api.get<{ events: EditEvent[] }>(`/learning/edit-events?env=${env}&limit=30`),
        api.get<{ events: RejectEvent[] }>(`/learning/reject-events?env=${env}&limit=30`),
      ]);
      setEditEvents(ed.events ?? []);
      setRejectEvents(rej.events ?? []);
    } catch (ex) {
      setErr(ex);
    }
  }, [env]);

  const refresh = useCallback(async () => {
    await Promise.all([refreshOverview(), refreshSignals()]);
  }, [refreshOverview, refreshSignals]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  usePollingFallback(refreshOverview, 20_000);

  const filteredRuns = useMemo(() => {
    if (!statusFilter) return jobRuns;
    return jobRuns.filter((r) => r.status === statusFilter);
  }, [jobRuns, statusFilter]);

  const activeWorkflowStep = useMemo(() => {
    if (!overview) return 1;
    const pendingStyle = overview.pending_style_proposals?.length ?? 0;
    const pendingDiscovery = overview.discovery_learning?.pending_proposals ?? 0;
    if (pendingStyle > 0 || pendingDiscovery > 0) return 3;
    if (overview.edit_stats?.ready_for_distill) return 2;
    const discoveryReady = (overview.discovery_learning?.groups ?? []).some(
      (g) => g.ready_for_distill && !g.has_pending_proposal,
    );
    if (discoveryReady) return 2;
    return 1;
  }, [overview]);

  async function runJobs() {
    if (!dryRun) {
      const ok = await dialog.confirm({
        title: dryRun ? '预览任务套件？' : '在 LIVE 执行任务套件？',
        description: dryRun
          ? `将模拟「${suite}」套件会做什么，不写入数据库。`
          : `将按「${suite}」套件在 LIVE 真实执行（可能写入驳回策略、定价配置等；若含编辑蒸馏且样本够数，也会创建学习提案）。建议日常先预览。`,
        confirmLabel: dryRun ? '开始预览' : '确认执行',
        cancelLabel: '取消',
        variant: dryRun ? 'info' : 'danger',
        liveWarning: !dryRun,
      });
      if (!ok) return;
    }
    setBusy('run-jobs');
    try {
      const out = await api.post<Record<string, unknown>>('/learning/run-jobs', {
        env: 'LIVE',
        suite,
        dry_run: dryRun,
        triggered_by: 'console:learning',
      });
      let result = out;
      if (isAsyncLearningJob(out)) {
        toast.progress('后台执行中…', '学习任务在后台运行，请稍候', {
          groupKey: 'learning-jobs',
        });
        result = await pollLearningJob(out.job_id);
      }
      toast.success(
        dryRun ? '预览完成' : '任务已提交',
        JSON.stringify(result.summary ?? result.ok ?? '完成'),
      );
      await refresh();
    } catch (ex) {
      toast.error('执行失败', errorSummary(ex));
      setErr(ex);
    } finally {
      setBusy(null);
    }
  }

  async function proposeEditPolicy() {
    const ok = await dialog.confirm({
      title: '生成学习提案？',
      description:
        '达到编辑批次阈值后，创建「学习提案」待审批项（不直接写 policy）。请在「待审批」页批准 style 与策略段落。',
      confirmLabel: '生成提案',
      cancelLabel: '取消',
      liveWarning: env === 'LIVE',
    });
    if (!ok) return;
    setBusy('propose');
    toast.progress('生成中…', 'LLM 蒸馏约需 1–3 分钟，请稍候', { groupKey: 'learning-propose' });
    try {
      let out = await api.post<Record<string, unknown>>('/learning/propose-edit-policy', {
        env,
        scope: 'company_style',
        limit: 200,
      });
      if (isAsyncLearningJob(out)) {
        out = await pollLearningJob(out.job_id);
      }
      if (out.skipped) {
        toast.info(
          '已跳过',
          String(out.reason_label ?? out.reason ?? 'unknown'),
        );
      } else if (out.pending) {
        toast.success('提案已创建', '请到「待审批」页批准');
      } else {
        toast.success('完成', JSON.stringify(out));
      }
      await refresh();
    } catch (ex) {
      toast.error('失败', errorSummary(ex));
      setErr(ex);
    } finally {
      setBusy(null);
    }
  }

  async function loadPolicy(scope: string) {
    setBusy(`policy-${scope}`);
    try {
      const resp = await api.get<{ policy: { content_md?: string } | null }>(
        `/learning/policies/${scope}?env=${env}`,
      );
      setPolicyPreview({
        scope,
        md: resp.policy?.content_md ?? '(empty)',
      });
    } catch (ex) {
      toast.error('加载 policy 失败', errorSummary(ex));
    } finally {
      setBusy(null);
    }
  }

  const stats = overview?.edit_stats;
  const editedAvailable =
    stats?.edited_available ?? stats?.edited_unconsumed ?? 0;
  const editedQueued = stats?.edited_queued_in_pending ?? 0;
  const batchThreshold = overview?.batch_threshold ?? 5;
  const nextBatchSize = Math.min(editedAvailable, batchThreshold);
  const batchPct = stats
    ? Math.min(
        100,
        Math.round((nextBatchSize / Math.max(1, batchThreshold)) * 100),
      )
    : 0;
  const pendingCount = overview?.pending_style_proposals.length ?? 0;
  const backlogHigh = editedAvailable > batchThreshold * 3;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="text-lg font-semibold">自主学习</h1>
        <span className="rounded bg-rose-100 px-2 py-0.5 text-xs font-medium text-rose-800">
          定时任务仅 LIVE
        </span>
        <span className="rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-700">
          当前环境: {env}
        </span>
        <Link
          to="/metrics"
          className="text-xs text-sky-700 hover:underline"
        >
          门禁指标 →
        </Link>
        <button
          type="button"
          onClick={() => void refresh()}
          className="ml-auto rounded border border-slate-300 px-2 py-1 text-sm hover:bg-slate-50"
        >
          刷新
        </button>
      </div>

      <LearningWorkflowStepper activeStep={activeWorkflowStep} />

      <LearningChannelsExplainer />

      {!!err && <ErrorAlert error={err} onRetry={refresh} />}

      {overview?.jobs_disabled && (
        <div className="rounded border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          学习任务总开关已关闭（环境变量 KOL_LEARNING_JOBS_DISABLED）— Bridge 定时任务将跳过执行。
        </div>
      )}

      <section className="rounded border border-slate-200 bg-white p-3">
        <h2 className="text-sm font-medium text-slate-800">1. 状态总览</h2>
        <div className="mt-2 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard
            label="待审批学习提案"
            value={String(pendingCount || '—')}
            hint={
              pendingCount > 0 ? (
                <Link to="/approvals" className="text-sky-700 hover:underline">
                  去待审批 →
                </Link>
              ) : (
                '无待批项'
              )
            }
          />
          <StatCard
            label="待蒸馏样本积压"
            value={stats ? `${editedAvailable} 条` : '—'}
            hint={
              editedQueued > 0
                ? `单份提案取 ${batchThreshold} 条 · 另有 ${editedQueued} 条在待审批提案中`
                : stats?.ready_for_distill
                  ? `单份提案取 ${batchThreshold} 条 · 下一批已满，可生成提案`
                  : `单份提案取 ${batchThreshold} 条 · 未达下一批阈值`
            }
          />
          <StatCard
            label="最近定时任务"
            value={formatRunSummary(overview?.run_summary)}
            hint="近几次 cron 汇总"
          />
          <StatCard
            label="已批准学习批次"
            value={String(overview?.approved_style_proposals ?? '—')}
            hint={
              overview?.style_in_hints
                ? '公司风格已注入运行时 hints'
                : '公司风格未注入 hints'
            }
          />
        </div>
        {stats && (
          <div
            className="mt-2 h-2 overflow-hidden rounded bg-slate-100"
            title={`下一批 ${nextBatchSize}/${batchThreshold}（积压 ${editedAvailable} 条）`}
          >
            <div
              className="h-full bg-violet-500 transition-all"
              style={{ width: `${batchPct}%` }}
            />
          </div>
        )}
        {backlogHigh && (
          <div className="mt-2 rounded border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] leading-relaxed text-amber-950">
            当前积压 <strong>{editedAvailable}</strong> 条「编辑后发送」样本，不等于单份学习提案的条数。
            每生成/批准一份提案只消费 <strong>{batchThreshold}</strong> 条；待审批页卡片上的「编辑样本 N 条」才是该提案实际规模。
            积压突增常见于 Gmail 批量对齐补录，或提案批准较慢（驳回的样本会回到队列）。
          </div>
        )}
        {(overview?.edit_stats_by_scope?.length ?? 0) > 0 && (
          <div className="mt-3 overflow-x-auto">
            <table className="w-full min-w-[320px] text-left text-[11px] text-slate-700">
              <thead>
                <tr className="border-b border-slate-200 text-slate-500">
                  <th className="py-1 pr-2 font-medium">蒸馏范围</th>
                  <th className="py-1 pr-2 font-medium">积压 / 单批阈值</th>
                  <th className="py-1 font-medium">状态</th>
                </tr>
              </thead>
              <tbody>
                {(overview?.edit_stats_by_scope ?? []).map((row) => {
                  const label =
                    row.scope === 'user_style' && row.owner_user_id != null
                      ? `${policyScopeLabel('user_style')} · 操作员 #${row.owner_user_id}`
                      : policyScopeLabel(row.scope);
                  const th = overview?.batch_threshold ?? 0;
                  return (
                    <tr
                      key={`${row.scope}-${row.owner_user_id ?? 'co'}`}
                      className="border-b border-slate-100"
                    >
                      <td className="py-1 pr-2">{label}</td>
                      <td className="py-1 pr-2">
                        {row.edited_available} / {th}
                        {row.edited_queued_in_pending > 0 && (
                          <span className="text-slate-500">
                            {' '}
                            （{row.edited_queued_in_pending} 条在待批提案中）
                          </span>
                        )}
                      </td>
                      <td className="py-1">
                        {row.has_pending_proposal ? (
                          <span className="text-amber-700">有待批提案</span>
                        ) : row.ready_for_distill ? (
                          <span className="text-emerald-700">可生成</span>
                        ) : (
                          <span className="text-slate-500">未达阈值</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <p className="mt-1 text-[10px] text-slate-500">
              左侧数字为各范围<strong>待蒸馏总积压</strong>，右侧为单份提案批次阈值（非整批样本数）。
            </p>
          </div>
        )}
        {pendingCount === 0 && stats && !stats.ready_for_distill && (
          <div className="mt-3">
            <LearningEmptySamplesHint />
          </div>
        )}
        {overview?.promote_outcome_eligibility?.length ? (
          <div className="mt-3 text-xs text-slate-600">
            <span className="font-medium text-emerald-800">合作复盘升格资格</span>
            <ul className="mt-1 list-disc pl-4">
              {overview.promote_outcome_eligibility.map((p) => (
                <li key={`out-${p.goal}`}>
                  <strong>{goalLabel(p.goal)}</strong>：
                  {p.eligible ? (
                    <span className="text-emerald-700">可升格</span>
                  ) : (
                    promoteReasonLabel(p.reason)
                  )}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        {overview?.promote_eligibility?.length ? (
          <div className="mt-3 text-xs text-slate-600">
            <span className="font-medium text-slate-700">回信策略升格资格</span>
            <span className="ml-1 text-slate-500">（policy 版本稳定度，非待审批次数）</span>
            <ul className="mt-1 list-disc pl-4">
              {overview.promote_eligibility.map((p) => (
                <li key={p.goal} title={`${p.skill} · v${p.approvals} · ${p.age_days}d`}>
                  <strong>{goalLabel(p.goal)}</strong>：
                  {p.eligible ? (
                    <span className="text-emerald-700">可升格</span>
                  ) : (
                    promoteReasonLabel(p.reason)
                  )}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </section>

      <EditDistanceTrendCard
        trend={overview?.edit_distance_trend}
        alert={overview?.convergence_alert}
        markers={overview?.style_approval_markers}
      />

      <LearningChannelTrends trends={overview?.channel_trends} />

      <LearningNextBatchPreview env={env} />

      <OutcomeLearningCard outcome={overview?.outcome_learning} />

      <div className="rounded border border-slate-200 bg-white p-3">
        <LearningManualTriggerSection
          suite={suite}
          onSuiteChange={setSuite}
          dryRun={dryRun}
          onDryRunChange={setDryRun}
          env={env}
          busyRunJobs={busy === 'run-jobs'}
          busyPropose={busy === 'propose'}
          onRunJobs={() => void runJobs()}
          onPropose={() => void proposeEditPolicy()}
          editedUnconsumed={editedAvailable}
          batchThreshold={overview?.batch_threshold}
          editedQueuedInPending={editedQueued}
          readyForDistill={stats?.ready_for_distill}
          pendingProposalCount={pendingCount}
        />
        <details className="mt-3 border-t border-slate-100 pt-2 text-xs text-slate-600">
          <summary className="cursor-pointer text-slate-500">工程师：CLI 单任务名</summary>
          <ul className="mt-1 list-disc pl-4">
            {JOB_OPTIONS.map((j) => (
              <li key={j}>
                <span className="font-medium">{jobLabel(j)}</span>
                <span className="ml-1 font-mono text-slate-400">{j}</span>
              </li>
            ))}
          </ul>
        </details>
      </div>

      <section className="rounded border border-slate-200 bg-white p-3">
        <h2 className="text-sm font-medium text-slate-800">3. 任务审计</h2>
        <div className="mt-2 flex flex-wrap gap-2">
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="rounded border border-slate-300 px-2 py-1 text-xs"
          >
            <option value="">全部状态</option>
            <option value="ok">成功</option>
            <option value="skipped">跳过</option>
            <option value="error">失败</option>
            <option value="running">运行中</option>
          </select>
        </div>
        <div className="mt-2 overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="border-b border-slate-200 text-slate-600">
                <th className="py-1 pr-2">任务</th>
                <th className="py-1 pr-2">状态</th>
                <th className="py-1 pr-2">开始</th>
                <th className="py-1 pr-2">耗时</th>
                <th className="py-1 pr-2">触发者</th>
                <th className="py-1">详情</th>
              </tr>
            </thead>
            <tbody>
              {filteredRuns.map((run) => (
                <tr
                  key={run.id ?? `${run.job_name}-${run.started_at}`}
                  className={
                    run.status === 'error'
                      ? 'border-b border-slate-100 bg-rose-50/50'
                      : 'border-b border-slate-100'
                  }
                >
                  <td className="py-1 pr-2" title={run.job_name}>
                    {jobLabel(run.job_name)}
                  </td>
                  <td className="py-1 pr-2">{jobStatusLabel(run.status)}</td>
                  <td className="py-1 pr-2">
                    {run.started_at ? <TimeAgo iso={run.started_at} /> : '—'}
                  </td>
                  <td className="py-1 pr-2">
                    {run.duration_ms != null ? `${run.duration_ms}ms` : '—'}
                  </td>
                  <td className="py-1 pr-2 max-w-[120px] truncate" title={run.triggered_by}>
                    {run.triggered_by ?? '—'}
                  </td>
                  <td className="py-1">
                    <button
                      type="button"
                      className="text-sky-700 hover:underline"
                      onClick={() =>
                        setExpandedRun(expandedRun === run.id ? null : (run.id ?? null))
                      }
                    >
                      {expandedRun === run.id ? '收起' : '展开'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {filteredRuns.length === 0 && (
            <div className="py-4 text-center text-sm text-slate-500">暂无任务记录</div>
          )}
        </div>
        {expandedRun != null && (
          <div className="mt-2 space-y-1">
            {filteredRuns.find((r) => r.id === expandedRun)?.status === 'error' && (
              <p className="text-[11px] text-amber-800">
                常见原因：学习任务总开关关闭、未达批次阈值、LIVE 环境未配置、或 Bridge 内部错误。
              </p>
            )}
            <pre className="max-h-64 overflow-auto rounded border border-slate-200 bg-slate-50 p-2 text-[11px]">
              {JSON.stringify(
                filteredRuns.find((r) => r.id === expandedRun)?.output ?? {},
                null,
                2,
              )}
            </pre>
          </div>
        )}
      </section>

      <DiscoveryLearningPanel env={env} progress={overview?.discovery_learning} />

      <section
        id="policies"
        className="space-y-3 rounded border border-slate-200 bg-white p-3 scroll-mt-4"
      >
        <h2 className="text-sm font-medium text-slate-800">4. 沉淀与反哺</h2>

        <div id="promote" className="scroll-mt-4 space-y-3">
          <StrategyPromotionPanel defaultOpen />
          <OutcomePromotionPanel />
        </div>

        <div>
          <div className="mb-2 flex flex-wrap gap-2">
            <span className="text-xs font-medium text-slate-700">Policy 预览</span>
            {(
              ['reply_strategy', 'reply_learning', 'company_style', 'outcome_strategy'] as const
            ).map((scope) => (
              <button
                key={scope}
                type="button"
                disabled={busy === `policy-${scope}`}
                onClick={() => void loadPolicy(scope)}
                className="rounded border border-slate-300 px-2 py-0.5 text-xs hover:bg-slate-50"
                title={scope}
              >
                {policyScopeLabel(scope)}
              </button>
            ))}
          </div>
          {policyPreview && (
            <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded border border-slate-200 bg-slate-50 p-2 text-[11px]">
              {policyPreview.md}
            </pre>
          )}
        </div>

        <details open>
          <summary className="cursor-pointer text-xs font-medium text-slate-700">
            编辑信号 ({editEvents.length})
          </summary>
          <ul className="mt-2 space-y-2">
            {editEvents.slice(0, 10).map((ev) => {
              const p = (ev.payload ?? {}) as Record<string, unknown>;
              return (
                <li
                  key={ev.id}
                  className="rounded border border-slate-100 bg-slate-50 p-2 text-xs"
                >
                  <div className="text-slate-600">
                    KOL {ev.identity_id} · {ev.campaign_id} · {goalLabel(ev.goal)}
                    {ev.ts && (
                      <>
                        {' '}
                        · <TimeAgo iso={ev.ts} />
                      </>
                    )}
                  </div>
                  {p.was_edited === true && (
                    <DraftEditDiffPanel
                      editLearning={{
                        was_edited: true,
                        edit_distance: p.edit_distance as number | undefined,
                        normalized_agent_body: String(p.normalized_agent_body ?? ''),
                        normalized_sent_body: String(p.normalized_sent_body ?? ''),
                      }}
                    />
                  )}
                </li>
              );
            })}
          </ul>
        </details>

        <details>
          <summary className="cursor-pointer text-xs font-medium text-slate-700">
            驳回信号 ({rejectEvents.length})
          </summary>
          <ul className="mt-2 space-y-1 text-xs">
            {rejectEvents.slice(0, 15).map((ev) => {
              const p = (ev.payload ?? {}) as Record<string, unknown>;
              const tags = Array.isArray(p.tags)
                ? (p.tags as string[]).map(rejectTagLabel).join('、')
                : '';
              return (
                <li key={ev.id} className="rounded border border-slate-100 px-2 py-1">
                  {goalLabel(ev.goal)} · KOL {ev.identity_id}: {String(p.note ?? '')}
                  {tags ? ` [${tags}]` : ''}
                </li>
              );
            })}
          </ul>
        </details>
      </section>
    </div>
  );
}

// Three learning channels — clarify which operator action feeds which policy.
function LearningChannelsExplainer() {
  const channels: Array<{ action: string; policy: string; cls: string; text: string }> = [
    {
      action: '驳回回信',
      policy: '回信策略（reply_learning）',
      cls: 'border-amber-200 bg-amber-50/50',
      text: 'text-amber-900',
    },
    {
      action: '编辑终稿后发送',
      policy: '邮件风格 + 回信策略（company/user_style · reply_strategy）',
      cls: 'border-violet-200 bg-violet-50/50',
      text: 'text-violet-900',
    },
    {
      action: '合作达成/失败归档',
      policy: '合作结局指导（outcome_strategy）',
      cls: 'border-emerald-200 bg-emerald-50/50',
      text: 'text-emerald-900',
    },
  ];
  return (
    <div className="rounded border border-slate-200 bg-white p-3">
      <div className="text-sm font-medium text-slate-800">三条学习通道（你的哪个动作影响哪个 policy）</div>
      <div className="mt-2 grid gap-2 sm:grid-cols-3">
        {channels.map((c) => (
          <div key={c.action} className={`rounded border p-2 ${c.cls}`}>
            <div className={`text-[12px] font-medium ${c.text}`}>{c.action}</div>
            <div className="mt-0.5 text-[11px] text-slate-600">→ {c.policy}</div>
          </div>
        ))}
      </div>
      <p className="mt-2 text-[11px] text-slate-500">
        三者都经「待审批」人工门控；稳定后可在下方「策略反哺」升格进对应技能（advisory）。
      </p>
    </div>
  );
}

// Post-collaboration root-cause learning: won/lost reasons → forward guidance.
function OutcomeLearningCard({
  outcome,
}: {
  outcome: Overview['outcome_learning'];
}) {
  if (!outcome) return null;
  const {
    by_class,
    total_retros,
    fresh_retros,
    fresh_available,
    fresh_queued_in_pending,
    failures,
    batch_size,
    min_failures,
  } = outcome;
  const avail = fresh_available ?? fresh_retros;
  return (
    <div className="rounded border border-emerald-200 bg-white p-3">
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="text-sm font-medium text-emerald-900">合作复盘学习</span>
        <span className="text-[11px] text-slate-500">
          合作达成/失败后做根因分析，沉淀「结局指导」供后续外联/谈判参考
        </span>
        {outcome.has_pending_proposal ? (
          <Link to="/approvals?type=outcome_learning" className="ml-auto text-[11px] text-sky-700 underline">
            有待审批复盘提案 →
          </Link>
        ) : outcome.ready_for_synthesis ? (
          <span className="ml-auto rounded bg-emerald-100 px-2 py-0.5 text-[11px] text-emerald-800">
            可生成复盘提案
          </span>
        ) : (
          <span className="ml-auto text-[11px] text-slate-500">
            未达阈值（≥{batch_size} 次 或 ≥{min_failures} 失败）
          </span>
        )}
      </div>
      <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4">
        <StatCard label="复盘总数" value={String(total_retros)} hint="已分析的合作" />
        <StatCard
          label="待综合"
          value={String(avail)}
          hint={
            fresh_queued_in_pending
              ? `另有 ${fresh_queued_in_pending} 条在待审批提案中`
              : '未并入提案'
          }
        />
        <StatCard
          label="失败案例"
          value={String(by_class.failure ?? 0)}
          hint={`成功 ${by_class.success ?? 0} · 部分 ${by_class.partial ?? 0}`}
        />
        <StatCard
          label="本批失败"
          value={String(failures)}
          hint={`阈值 ≥${min_failures} 提前触发`}
        />
      </div>
      <p className="mt-2 text-[11px] text-slate-500">
        触发：合作归档时立即单案复盘（同步）+ job 补漏（<code>analyze_collab_outcome</code>）；够批次后综合为提案（job{' '}
        <code>apply_outcome_policy</code>），在「待审批 → 合作复盘」批准。
      </p>
    </div>
  );
}

function StatCard({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: ReactNode;
}) {
  return (
    <div className="rounded border border-slate-100 bg-slate-50/80 p-2">
      <div className="text-[11px] text-slate-500">{label}</div>
      <div className="text-sm font-semibold text-slate-900 leading-snug">{value}</div>
      <div className="text-[11px] text-slate-600">{hint}</div>
    </div>
  );
}

function pct(value: number | null | undefined): string {
  if (value == null) return '—';
  return `${Math.round(value * 100)}%`;
}

// Convergence chart: lower bars = operator edits AI drafts less = learning works.
function bucketFromIso(at: string | undefined, bucketKey: string): string | null {
  if (!at) return null;
  const d = new Date(at);
  if (Number.isNaN(d.getTime())) return null;
  if (bucketKey.includes('W')) {
    const onejan = new Date(d.getFullYear(), 0, 1);
    const week = Math.ceil(
      ((d.getTime() - onejan.getTime()) / 86400000 + onejan.getDay() + 1) / 7,
    );
    return `${d.getFullYear()}-W${String(week).padStart(2, '0')}`;
  }
  return d.toISOString().slice(0, 10);
}

function EditDistanceTrendCard({
  trend,
  alert,
  markers,
}: {
  trend: EditDistanceTrend | undefined;
  alert?: Overview['convergence_alert'];
  markers?: Overview['style_approval_markers'];
}) {
  if (!trend || trend.overall.count === 0) {
    return (
      <div className="rounded border border-slate-200 bg-white p-3">
        <div className="text-sm font-medium text-slate-800">编辑幅度趋势</div>
        <p className="mt-1 text-xs text-slate-500">
          暂无「编辑后发送」样本。操作员在 Gmail 修改 AI 草稿并发出后，这里会显示编辑幅度随时间的变化。
        </p>
      </div>
    );
  }
  const buckets = trend.buckets.slice(-12);
  const markerByBucket = new Map<string, number>();
  for (const m of markers ?? []) {
    const key = bucketFromIso(m.at, trend.buckets[0]?.bucket ?? '');
    if (key) markerByBucket.set(key, (markerByBucket.get(key) ?? 0) + 1);
  }
  const delta = trend.recent_vs_prior.delta;
  const improving = delta != null && delta < 0;
  const worsening = delta != null && delta > 0;
  return (
    <div className="rounded border border-slate-200 bg-white p-3">
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="text-sm font-medium text-slate-800">编辑幅度趋势</span>
        <span className="text-[11px] text-slate-500">
          越低越好 = 操作员越来越少改 AI 草稿（近 90 天 · 按周）
        </span>
        {delta != null && (
          <span
            className={
              'ml-auto rounded px-2 py-0.5 text-[11px] font-medium ' +
              (improving
                ? 'bg-emerald-100 text-emerald-800'
                : worsening
                  ? 'bg-rose-100 text-rose-800'
                  : 'bg-slate-100 text-slate-600')
            }
            title={
              alert?.guard_basis === 'after_last_approval'
                ? '最近一次学习批准后 vs 批准前的平均编辑幅度'
                : '最近一周平均编辑幅度 vs 此前各周平均'
            }
          >
            {improving ? '↓ 改善' : worsening ? '↑ 变差' : '持平'} {pct(Math.abs(delta))}
            {alert?.guard_basis === 'after_last_approval' && (
              <span className="ml-1 font-normal text-slate-600">（批准后）</span>
            )}
          </span>
        )}
      </div>
      <div className="relative mt-2" style={{ height: 64 }}>
        <div className="flex h-full items-end gap-1">
          {buckets.map((b) => {
            const v = b.avg_edit_distance ?? 0;
            const h = Math.max(2, Math.round(v * 60));
            return (
              <div
                key={b.bucket}
                className="flex h-full flex-1 flex-col justify-end"
                title={`${b.bucket} · 平均 ${pct(b.avg_edit_distance)} · 编辑率 ${pct(
                  b.was_edited_rate,
                )} · ${b.count} 封${
                  markerByBucket.get(b.bucket)
                    ? ` · ${markerByBucket.get(b.bucket)} 次学习批准`
                    : ''
                }`}
              >
                <div
                  className="w-full rounded-t bg-violet-400"
                  style={{ height: `${h}px` }}
                />
              </div>
            );
          })}
        </div>
        <div
          className="pointer-events-none absolute inset-0 flex gap-1"
          aria-hidden
        >
          {buckets.map((b) => (
            <div key={`line-${b.bucket}`} className="flex flex-1 justify-center">
              {markerByBucket.get(b.bucket) ? (
                <div
                  className="h-full w-0.5 bg-emerald-600 opacity-90"
                  title="学习提案已批准"
                />
              ) : null}
            </div>
          ))}
        </div>
      </div>
      <div className="mt-2 grid grid-cols-3 gap-2 text-[11px] text-slate-600">
        <div>
          总体平均编辑幅度{' '}
          <span className="font-semibold text-slate-900">
            {pct(trend.overall.avg_edit_distance)}
          </span>
        </div>
        <div>
          编辑率{' '}
          <span className="font-semibold text-slate-900">
            {pct(trend.overall.was_edited_rate)}
          </span>
        </div>
        <div>样本 {trend.overall.count} 封</div>
      </div>
      {(markers?.length ?? 0) > 0 && (
        <p className="mt-1 text-[11px] text-slate-500">
          绿色竖线 = 该周有学习提案批准（共 {markers?.length} 次）
        </p>
      )}
      {alert?.worsening && (
        <div className="mt-2 rounded border border-rose-200 bg-rose-50 px-2 py-1 text-[11px] text-rose-800">
          {alert.hint || '最近编辑幅度上升，可能某次批准的 policy 反而变差，建议检查并回滚。'}
          <Link to="/policies" className="ml-1 underline">
            去 policy 历史
          </Link>
        </div>
      )}
    </div>
  );
}
