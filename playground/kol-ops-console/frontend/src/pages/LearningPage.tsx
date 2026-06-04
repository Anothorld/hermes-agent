import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import DraftEditDiffPanel from '../components/DraftEditDiffPanel';
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
    if ((overview.pending_style_proposals?.length ?? 0) > 0) return 3;
    if (overview.edit_stats?.ready_for_distill) return 2;
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
      toast.success(dryRun ? '预览完成' : '任务已提交', JSON.stringify(out.summary ?? out.ok));
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
      const out = await api.post<Record<string, unknown>>('/learning/propose-edit-policy', {
        env,
        scope: 'company_style',
        limit: 200,
      });
      if (out.skipped) {
        toast.info('已跳过', String(out.reason ?? 'unknown'));
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
  const batchPct = stats
    ? Math.min(100, Math.round((stats.edited_unconsumed / overview!.batch_threshold) * 100))
    : 0;
  const pendingCount = overview?.pending_style_proposals.length ?? 0;

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
            label="编辑批次进度"
            value={
              stats
                ? `${stats.edited_unconsumed} / ${overview?.batch_threshold}`
                : '—'
            }
            hint={stats?.ready_for_distill ? '已达蒸馏阈值，可生成提案' : '未达阈值'}
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
          <div className="mt-2 h-2 overflow-hidden rounded bg-slate-100" title={`${batchPct}%`}>
            <div
              className="h-full bg-violet-500 transition-all"
              style={{ width: `${batchPct}%` }}
            />
          </div>
        )}
        {pendingCount === 0 && stats && !stats.ready_for_distill && (
          <div className="mt-3">
            <LearningEmptySamplesHint />
          </div>
        )}
        {overview?.promote_eligibility?.length ? (
          <div className="mt-3 text-xs text-slate-600">
            <span className="font-medium text-slate-700">策略升格资格</span>
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
          editedUnconsumed={stats?.edited_unconsumed}
          batchThreshold={overview?.batch_threshold}
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

      <section
        id="policies"
        className="space-y-3 rounded border border-slate-200 bg-white p-3 scroll-mt-4"
      >
        <h2 className="text-sm font-medium text-slate-800">4. 沉淀与反哺</h2>

        <div id="promote" className="scroll-mt-4">
          <StrategyPromotionPanel defaultOpen />
        </div>

        <div>
          <div className="mb-2 flex flex-wrap gap-2">
            <span className="text-xs font-medium text-slate-700">Policy 预览</span>
            {(['reply_strategy', 'reply_learning', 'company_style'] as const).map((scope) => (
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
