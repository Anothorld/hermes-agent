export type QueueSortMode = 'priority' | 'time_asc' | 'time_desc';

export const QUEUE_SORT_LABEL: Record<QueueSortMode, string> = {
  priority: '智能优先级',
  time_asc: '等待最久',
  time_desc: '最新到达',
};

export const QUEUE_SORT_MODES: QueueSortMode[] = [
  'priority',
  'time_asc',
  'time_desc',
];

export function queueSortQueryParams(
  mode: QueueSortMode,
): { sort: string; order: string } {
  if (mode === 'time_asc') return { sort: 'time', order: 'asc' };
  if (mode === 'time_desc') return { sort: 'time', order: 'desc' };
  return { sort: 'priority', order: 'asc' };
}

/** Client-side time sort when rows were filtered after fetch. */
export function sortByIsoTime<T>(
  rows: T[],
  mode: QueueSortMode,
  field: (row: T) => string | null | undefined,
): T[] {
  if (mode === 'priority') return rows;
  const dir = mode === 'time_asc' ? 1 : -1;
  return [...rows].sort((a, b) => {
    const ta = Date.parse(field(a) ?? '') || 0;
    const tb = Date.parse(field(b) ?? '') || 0;
    return (ta - tb) * dir;
  });
}
