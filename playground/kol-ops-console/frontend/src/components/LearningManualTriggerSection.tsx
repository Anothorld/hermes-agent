import { Link } from 'react-router-dom';
import {
  SUITE_OPERATOR_HINTS,
  suiteOptionLabel,
} from '../constants/domainLabels';

const SUITES = [
  'capture',
  'distill',
  'pricing',
  'audit',
  'quality',
  'nightly',
  'all',
] as const;

export type LearningManualTriggerProps = {
  suite: string;
  onSuiteChange: (suite: string) => void;
  dryRun: boolean;
  onDryRunChange: (dryRun: boolean) => void;
  env: string;
  busyRunJobs: boolean;
  busyPropose: boolean;
  onRunJobs: () => void;
  onPropose: () => void;
  editedUnconsumed: number | undefined;
  batchThreshold: number | undefined;
  editedQueuedInPending?: number;
  readyForDistill: boolean | undefined;
  pendingProposalCount: number;
};

export function LearningManualTriggerSection({
  suite,
  onSuiteChange,
  dryRun,
  onDryRunChange,
  env,
  busyRunJobs,
  busyPropose,
  onRunJobs,
  onPropose,
  editedUnconsumed,
  batchThreshold,
  editedQueuedInPending,
  readyForDistill,
  pendingProposalCount,
}: LearningManualTriggerProps) {
  const suiteHint = SUITE_OPERATOR_HINTS[suite] ?? '';
  const batchLabel =
    editedUnconsumed != null && batchThreshold != null
      ? `${editedUnconsumed} / ${batchThreshold} 条可蒸馏样本`
      : '—';
  const queuedNote =
    editedQueuedInPending != null && editedQueuedInPending > 0
      ? `（${editedQueuedInPending} 条已在待审批提案中）`
      : '';
  const proposeBlocked = pendingProposalCount > 0;
  const proposeReady = readyForDistill === true && !proposeBlocked;

  return (
    <section id="trigger" className="scroll-mt-4 space-y-3">
      <div>
        <h2 className="text-sm font-medium text-slate-800">2. 手动操作</h2>
        <p className="mt-1 text-xs text-slate-600">
          左边跑<strong>一整套</strong>定时任务；右边<strong>只生成</strong>跨 KOL 学习提案。批准仍在
          <Link to="/approvals" className="mx-0.5 text-sky-700 hover:underline">
            待审批
          </Link>
          完成。
        </p>
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        <div className="flex flex-col rounded border border-violet-200 bg-violet-50/40 p-3">
          <div className="text-sm font-medium text-violet-950">运行套件</div>
          <p className="mt-1 text-xs leading-relaxed text-violet-900/90">
            批量执行定时任务（采集、驳回学习、定价校准等），按所选套件而定。
          </p>
          <ul className="mt-2 list-disc space-y-0.5 pl-4 text-[11px] text-violet-900/80">
            <li>
              <strong>固定 LIVE</strong>（只用生产数据，与夜间 cron 一致）
            </li>
            <li>
              建议先勾选<strong>仅预览</strong>，确认后再取消预览正式执行
            </li>
            <li>不会直接改邮件风格 policy（学习提案需单独批准）</li>
          </ul>
          {suiteHint ? (
            <p className="mt-2 rounded border border-violet-100 bg-white/70 px-2 py-1 text-[11px] text-slate-700">
              当前套件：{suiteHint}
            </p>
          ) : null}
          <div className="mt-auto flex flex-wrap items-end gap-3 pt-3">
            <label className="flex flex-col gap-1 text-xs text-slate-700">
              任务套件
              <select
                value={suite}
                onChange={(e) => onSuiteChange(e.target.value)}
                className="min-w-[10rem] rounded border border-slate-300 bg-white px-2 py-1 text-sm"
              >
                {SUITES.map((s) => (
                  <option key={s} value={s}>
                    {suiteOptionLabel(s)}
                  </option>
                ))}
              </select>
            </label>
            <label
              className="flex items-center gap-2 text-sm text-slate-700"
              title="勾选后只显示会做什么，不写入数据库"
            >
              <input
                type="checkbox"
                checked={dryRun}
                onChange={(e) => onDryRunChange(e.target.checked)}
              />
              仅预览
            </label>
            <button
              type="button"
              disabled={busyRunJobs}
              onClick={onRunJobs}
              className="rounded bg-violet-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-violet-700 disabled:opacity-40"
            >
              {busyRunJobs ? '执行中…' : dryRun ? '预览套件' : '执行套件'}
            </button>
          </div>
        </div>

        <div className="flex flex-col rounded border border-amber-200 bg-amber-50/50 p-3">
          <div className="text-sm font-medium text-amber-950">生成学习提案</div>
          <p className="mt-1 text-xs leading-relaxed text-amber-900/90">
            只做一件事：把够批次的「编辑后发送」样本蒸馏成<strong>待审批</strong>提案（公司风格 +
            回信策略），不直接生效。
          </p>
          <p className="mt-1 text-[11px] leading-relaxed text-amber-900/70">
            阈值按<strong>编辑事件条数</strong>计（默认 5 条），不是「5 封邮件」或「5 个 KOL」；每条含该 KOL
            的会话时间线，可能来自多位 KOL。生成时<strong>必须成功调用 LLM</strong>，失败会报错而不会生成规则拼接稿。
          </p>
          <ul className="mt-2 list-disc space-y-0.5 pl-4 text-[11px] text-amber-900/80">
            <li>
              使用页顶环境 <strong>{env}</strong>（可与 LIVE 定时任务不同）
            </li>
            <li>
              样本进度：{batchLabel}
              {queuedNote}
            </li>
            <li>通常需 1–3 分钟，请勿重复点击（超时已放宽至 5 分钟）</li>
            {proposeBlocked ? (
              <li className="text-amber-800">
                已有待审批提案，请先
                <Link to="/approvals" className="text-sky-700 hover:underline">
                  批准或驳回
                </Link>
                后再生成
              </li>
            ) : proposeReady ? (
              <li className="text-emerald-800">已达批次阈值，可以生成</li>
            ) : (
              <li>未达批次阈值时会跳过（请先积累编辑样本或跑「采集」套件）</li>
            )}
          </ul>
          <p className="mt-2 text-[11px] text-slate-600">
            选「蒸馏 / 夜间」套件且<strong>非预览</strong>时，也会顺带做本操作。
          </p>
          <div className="mt-auto pt-3">
            <button
              type="button"
              disabled={busyPropose || proposeBlocked}
              onClick={onPropose}
              className="rounded border border-amber-400 bg-amber-100 px-3 py-1.5 text-sm font-medium text-amber-950 hover:bg-amber-200 disabled:opacity-40"
              title={
                proposeBlocked
                  ? '已有待审批学习提案'
                  : proposeReady
                    ? '生成待审批学习提案'
                    : '编辑样本未达批次阈值'
              }
            >
              {busyPropose ? '生成中…' : '生成学习提案'}
            </button>
          </div>
        </div>
      </div>
    </section>
  );
}
