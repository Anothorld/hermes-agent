export type EscalationWorkflowStep = {
  n: string;
  title: string;
  hint: string;
  status: 'done' | 'active' | 'pending';
};

export function EscalationWorkflowStepper({
  steps,
  activeStep,
}: {
  steps: EscalationWorkflowStep[];
  activeStep: number;
}) {
  return (
    <nav
      aria-label="升级处理步骤"
      className="rounded border border-slate-200 bg-gradient-to-r from-slate-50 to-white p-3"
    >
      <p className="mb-2 text-xs text-slate-600">
        当前在第 <strong>{activeStep}</strong> 步。
        入站升级请在本页完成全流程，无需跳转待审批。
      </p>
      <ol className="flex flex-wrap gap-2">
        {steps.map((s) => {
          const n = Number(s.n);
          const isActive = s.status === 'active' || n === activeStep;
          const isDone = s.status === 'done';
          return (
            <li key={s.n}>
              <div
                className={
                  'flex min-w-[8.5rem] flex-col rounded border px-2 py-1.5 text-left ' +
                  (isActive
                    ? 'border-sky-400 bg-sky-50 ring-1 ring-sky-200'
                    : isDone
                      ? 'border-emerald-200 bg-emerald-50/60'
                      : 'border-slate-200 bg-white')
                }
              >
                <span className="text-[10px] font-medium text-slate-500">
                  步骤 {s.n}
                  {isDone ? ' ✓' : ''}
                </span>
                <span className="text-xs font-semibold text-slate-900">{s.title}</span>
                <span className="mt-0.5 text-[10px] leading-snug text-slate-500">{s.hint}</span>
              </div>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
