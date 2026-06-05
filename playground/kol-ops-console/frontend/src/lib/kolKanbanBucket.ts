import type { GoalState, Lane } from '../api';
import { GOAL_COLUMNS, goalRank } from '../components/GoalProgressBar';

const LANES: Lane[] = ['commerce', 'fulfillment', 'publish'];
const IN_PROGRESS = new Set(['active', 'blocked']);

/**
 * Pick the kanban column for a KOL card.
 *
 * Uses the leftmost in-progress goal in pipeline order (not the last lane
 * chip). Otherwise operators with both commerce + fulfillment active see
 * cards only in a far-right column when「全部」columns are shown.
 */
export function resolveKanbanColumnGoal(
  goals: Partial<Record<Lane, GoalState | null>>,
): (typeof GOAL_COLUMNS)[number]['goal'] {
  let bestGoal: string | null = null;
  let bestRank = Number.POSITIVE_INFINITY;

  for (const lane of LANES) {
    const chip = goals[lane];
    if (!chip?.goal || !IN_PROGRESS.has(chip.state)) continue;
    const rank = goalRank(chip.goal);
    if (rank >= 0 && rank < bestRank) {
      bestRank = rank;
      bestGoal = chip.goal;
    }
  }

  if (bestGoal && (GOAL_COLUMNS as readonly { goal: string }[]).some((c) => c.goal === bestGoal)) {
    return bestGoal as (typeof GOAL_COLUMNS)[number]['goal'];
  }

  for (const lane of LANES) {
    const chip = goals[lane];
    if (chip?.goal) {
      return chip.goal as (typeof GOAL_COLUMNS)[number]['goal'];
    }
  }

  return 'outreach';
}
