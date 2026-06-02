/**
 * v2.4 goal vocabulary, reused by Kanban + KolDetail + EscalationConsole.
 */
import { GOAL_LABELS, goalLabel } from '../constants/domainLabels';

export { goalLabel };

export const GOAL_COLUMNS = [
  { goal: 'outreach', label: GOAL_LABELS.outreach, lane: 'commerce' as const },
  { goal: 'interest_qualification', label: GOAL_LABELS.interest_qualification, lane: 'commerce' as const },
  { goal: 'product_selection', label: GOAL_LABELS.product_selection, lane: 'commerce' as const },
  { goal: 'deliverables_scope', label: GOAL_LABELS.deliverables_scope, lane: 'commerce' as const },
  { goal: 'compensation_negotiation', label: GOAL_LABELS.compensation_negotiation, lane: 'commerce' as const },
  { goal: 'contract_signing', label: GOAL_LABELS.contract_signing, lane: 'commerce' as const },
  { goal: 'logistics', label: GOAL_LABELS.logistics, lane: 'fulfillment' as const },
  { goal: 'content_production', label: GOAL_LABELS.content_production, lane: 'fulfillment' as const },
  { goal: 'content_review_and_golive', label: GOAL_LABELS.content_review_and_golive, lane: 'publish' as const },
] as const;

export type GoalName = (typeof GOAL_COLUMNS)[number]['goal'];
export const GOAL_NAMES: GoalName[] = GOAL_COLUMNS.map((c) => c.goal);

export function goalRank(goal: string | null | undefined): number {
  if (!goal) return -1;
  const idx = (GOAL_NAMES as string[]).indexOf(goal);
  return idx;
}

export function GoalProgressBar({
  active,
  completed = [],
  blocked = false,
}: {
  active: string | null;
  completed?: string[];
  blocked?: boolean;
}) {
  const idx = goalRank(active);
  const completedSet = new Set(completed);
  return (
    <ol className="flex flex-wrap gap-1 rounded-md border border-slate-200 bg-white p-2 text-xs">
      {GOAL_COLUMNS.map(({ goal, label }, i) => {
        const isDone = completedSet.has(goal) || (idx >= 0 && i < idx);
        const isActive = i === idx;
        const cls = isActive
          ? blocked
            ? 'bg-amber-500 text-white'
            : 'bg-emerald-500 text-white'
          : isDone
          ? 'bg-emerald-100 text-emerald-900'
          : 'bg-slate-100 text-slate-500';
        return (
          <li key={goal} className={`rounded px-2 py-1 ${cls}`} title={goal}>
            {i + 1}. {label}
          </li>
        );
      })}
    </ol>
  );
}
