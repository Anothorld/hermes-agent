/**
 * v2.4 goal vocabulary, reused by Kanban + KolDetail + EscalationConsole.
 *
 * Goals are organised into 9 visible commerce/fulfillment/publish columns
 * plus an implicit `meta` lane for archival.
 */
export const GOAL_COLUMNS = [
  { goal: 'outreach', label: 'Outreach', lane: 'commerce' },
  { goal: 'interest_qualification', label: 'Interest', lane: 'commerce' },
  { goal: 'product_selection', label: 'Product', lane: 'commerce' },
  { goal: 'deliverables_scope', label: 'Deliverables', lane: 'commerce' },
  { goal: 'compensation_negotiation', label: 'Compensation', lane: 'commerce' },
  { goal: 'contract_signing', label: 'Contract', lane: 'commerce' },
  { goal: 'logistics', label: 'Logistics', lane: 'fulfillment' },
  { goal: 'content_production', label: 'Production', lane: 'fulfillment' },
  { goal: 'content_review_and_golive', label: 'Review/Golive', lane: 'publish' },
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
          <li key={goal} className={`rounded px-2 py-1 ${cls}`}>
            {i + 1}. {label}
          </li>
        );
      })}
    </ol>
  );
}
