import { Link } from 'react-router-dom';

const STEPS: ReadonlyArray<{
  n: number;
  title: string;
  hint: string;
  to: string;
  hash?: string;
}> = [
  {
    n: 1,
    title: '采集样本',
    hint: '审批回信、shortlist 决策、编辑后发送',
    to: '/metrics',
  },
  {
    n: 2,
    title: '生成提案',
    hint: '编辑或发现样本达阈值后触发',
    to: '/learning',
    hash: '#trigger',
  },
  {
    n: 3,
    title: '待审批批准',
    hint: 'style / 发现标准 / 策略沉淀',
    to: '/approvals',
  },
  {
    n: 4,
    title: '沉淀 policy',
    hint: '写入 company_style / reply_strategy',
    to: '/learning',
    hash: '#policies',
  },
  {
    n: 5,
    title: '升格技能参考',
    hint: '稳定策略写入技能 references',
    to: '/learning',
    hash: '#promote',
  },
];

export function LearningWorkflowStepper({ activeStep = 1 }: { activeStep?: number }) {
  return (
    <nav
      aria-label="学习闭环步骤"
      className="rounded border border-slate-200 bg-gradient-to-r from-slate-50 to-white p-3"
    >
      <p className="mb-2 text-xs text-slate-600">
        日常只需：<strong>待审批回信</strong>、<strong>shortlist 决策</strong> → 偶尔查看本页批次进度 → 批准学习提案。下方为完整闭环。
      </p>
      <ol className="flex flex-wrap gap-2">
        {STEPS.map((s) => {
          const isActive = s.n === activeStep;
          const href = `${s.to}${s.hash ?? ''}`;
          return (
            <li key={s.n}>
              <Link
                to={href}
                className={
                  'flex min-w-[9rem] flex-col rounded border px-2 py-1.5 text-left transition-colors ' +
                  (isActive
                    ? 'border-violet-400 bg-violet-50 ring-1 ring-violet-200'
                    : 'border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50')
                }
              >
                <span className="text-[10px] font-medium text-slate-500">步骤 {s.n}</span>
                <span className="text-xs font-semibold text-slate-900">{s.title}</span>
                <span className="mt-0.5 text-[10px] leading-snug text-slate-500">{s.hint}</span>
              </Link>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

export function LearningEmptySamplesHint() {
  return (
    <div className="rounded border border-dashed border-slate-300 bg-slate-50/80 px-3 py-2 text-xs text-slate-600">
      <div className="font-medium text-slate-700">如何产生第一批学习样本？</div>
      <ul className="mt-1 list-disc pl-4 space-y-0.5">
        <li>在「待审批」批准或驳回 AI 回信草稿（驳回请选标签）</li>
        <li>在产品页 shortlist 上批准 / 移除 / 转移 KOL，并填写原因标签与评论</li>
        <li>在 Gmail 发送后由系统对齐终稿，记录编辑差异</li>
        <li>编辑或发现样本达到阈值后，在本页点击「生成学习提案」或等待夜间任务</li>
      </ul>
    </div>
  );
}
