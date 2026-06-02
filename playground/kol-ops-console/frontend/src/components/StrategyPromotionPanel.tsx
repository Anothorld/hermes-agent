import { useCallback, useState } from 'react';
import { api } from '../api';
import {
  GOAL_LABELS,
  PROMOTABLE_GOALS,
  goalLabel,
  promoteReasonLabel,
} from '../constants/domainLabels';
import { toast, useEnvStore } from '../lib/store';
import { errorSummary } from '../lib/errors';

type PromoteResult = {
  goal: string;
  skill: string;
  env: string;
  target_path: string;
  eligible: boolean;
  reason: string;
  approvals: number;
  age_days: number;
  changed: boolean;
  proposed_markdown: string;
  dry_run?: boolean;
  written?: boolean;
  needs_sync_skills?: boolean;
};

const GOALS = PROMOTABLE_GOALS.map((id) => ({
  id,
  label: GOAL_LABELS[id] ?? id,
}));

export default function StrategyPromotionPanel({
  defaultOpen = false,
}: {
  defaultOpen?: boolean;
}) {
  const env = useEnvStore((s) => s.env);
  const [open, setOpen] = useState(defaultOpen);
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [goal, setGoal] = useState<string>(GOALS[0].id);
  const [minApprovals, setMinApprovals] = useState(2);
  const [minAgeDays, setMinAgeDays] = useState(7);
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<PromoteResult | null>(null);

  const run = useCallback(
    async (dryRun: boolean) => {
      setBusy(true);
      try {
        const out = await api.post<PromoteResult>('/learning/promote-strategy', {
          goal,
          env,
          min_approvals: minApprovals,
          min_age_days: minAgeDays,
          dry_run: dryRun,
        });
        setPreview(out);
        if (dryRun) setStep(2);
        if (!dryRun) {
          setStep(3);
          if (out.written) {
            toast.success(
              '已写入技能参考',
              '请运行 sync skills 推送到 kol-orchestrator',
            );
          } else {
            toast.info('无变化', '该阶段的策略与现有参考一致，未改写文件。');
          }
        }
      } catch (ex) {
        toast.error('操作失败', errorSummary(ex));
      } finally {
        setBusy(false);
      }
    },
    [goal, env, minApprovals, minAgeDays],
  );

  return (
    <section className="rounded border border-slate-200 bg-white">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between border-b border-slate-100 bg-slate-50 px-3 py-2 text-sm"
      >
        <span className="font-medium">策略反哺：稳定策略 → 技能参考文件</span>
        <span className="text-xs text-slate-500">{open ? '▼' : '▶'}</span>
      </button>
      {open && (
        <div className="space-y-3 p-3 text-sm">
          <ul className="list-disc space-y-1 pl-4 text-xs text-slate-600">
            <li>将已反复批准、在 policy 中稳定的「回信策略」段落写入对应技能的参考文档（advisory）。</li>
            <li>不会覆盖技能中的 HARD 规则、事实归属、定价引擎与升级门控。</li>
            <li>写入后需运行 <code className="rounded bg-slate-100 px-1">python playground/learning/sync_skills.py</code> 同步到编排器。</li>
          </ul>

          <ol className="flex flex-wrap gap-2 text-[11px]">
            {[
              [1, '选择阶段'],
              [2, '预览'],
              [3, '升格写入'],
            ].map(([n, label]) => (
              <li
                key={n}
                className={
                  'rounded px-2 py-0.5 ' +
                  (step === n
                    ? 'bg-violet-100 font-medium text-violet-900'
                    : 'bg-slate-100 text-slate-500')
                }
              >
                {n}. {label}
              </li>
            ))}
          </ol>

          <div className="flex flex-wrap items-end gap-3">
            <label className="flex flex-col gap-1">
              <span className="text-xs text-slate-600">谈判阶段</span>
              <select
                value={goal}
                onChange={(e) => {
                  setGoal(e.target.value);
                  setPreview(null);
                  setStep(1);
                }}
                className="rounded border border-slate-300 px-2 py-1 text-sm"
              >
                {GOALS.map((g) => (
                  <option key={g.id} value={g.id}>
                    {g.label}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              disabled={busy}
              onClick={() => run(true)}
              className="rounded border border-slate-300 px-3 py-1 text-xs hover:bg-slate-50 disabled:opacity-40"
            >
              {busy ? '处理中…' : '② 预览（必做）'}
            </button>
            <button
              type="button"
              disabled={busy || !preview?.eligible}
              onClick={() => run(false)}
              className="rounded bg-emerald-600 px-3 py-1 text-xs text-white hover:bg-emerald-700 disabled:opacity-40"
              title={!preview?.eligible ? '请先预览并确认满足升格门槛' : undefined}
            >
              ③ 升格写入
            </button>
          </div>

          <details className="text-xs">
            <summary className="cursor-pointer text-slate-600">高级：升格门槛</summary>
            <div className="mt-2 flex flex-wrap gap-3">
              <label className="flex flex-col gap-1">
                <span className="text-slate-500">最少 policy 版本数</span>
                <input
                  type="number"
                  min={1}
                  value={minApprovals}
                  onChange={(e) => setMinApprovals(Number(e.target.value))}
                  className="w-24 rounded border border-slate-300 px-2 py-1"
                />
              </label>
              <label className="flex flex-col gap-1">
                <span className="text-slate-500">最少稳定天数</span>
                <input
                  type="number"
                  min={0}
                  value={minAgeDays}
                  onChange={(e) => setMinAgeDays(Number(e.target.value))}
                  className="w-24 rounded border border-slate-300 px-2 py-1"
                />
              </label>
            </div>
          </details>

          {preview && (
            <div className="space-y-2">
              <div className="flex flex-wrap items-center gap-2 text-xs">
                <span
                  className={`rounded px-2 py-0.5 ${
                    preview.eligible
                      ? 'bg-emerald-100 text-emerald-800'
                      : 'bg-amber-100 text-amber-800'
                  }`}
                >
                  {preview.eligible ? '可升格' : promoteReasonLabel(preview.reason)}
                </span>
                <span className="text-slate-600">
                  {goalLabel(preview.goal)} · 版本 {preview.approvals} 次 · 存活 {preview.age_days} 天
                </span>
              </div>
              <div className="text-[11px] text-slate-500" title={preview.target_path}>
                目标：{preview.skill} / references/learned/{preview.goal}.md
              </div>
              {preview.proposed_markdown ? (
                <pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded border border-slate-200 bg-slate-50 p-2 text-[11px] text-slate-800">
                  {preview.proposed_markdown}
                </pre>
              ) : (
                <div className="italic text-slate-500">（该阶段暂无策略段落）</div>
              )}
              {preview.needs_sync_skills && (
                <div className="rounded bg-amber-50 px-2 py-1 text-xs text-amber-800">
                  已写入文件 — 请运行 sync skills 同步到 kol-orchestrator。
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
