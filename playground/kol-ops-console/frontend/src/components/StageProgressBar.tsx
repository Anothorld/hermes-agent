export const STAGES = [
  'discovered',
  'outreach',
  'product_pick',
  'negotiation',
  'contract',
  'logistics',
  'content_delivery',
  'closed',
] as const;

export type Stage = (typeof STAGES)[number];

export function StageProgressBar({ stage }: { stage: Stage | string | null | undefined }) {
  const idx = STAGES.indexOf((stage ?? 'discovered') as Stage);
  return (
    <ol className="flex flex-wrap gap-1 rounded-md border border-slate-200 bg-white p-2 text-xs">
      {STAGES.map((s, i) => {
        const done = i < idx;
        const active = i === idx;
        return (
          <li
            key={s}
            className={
              'rounded px-2 py-1 ' +
              (active
                ? 'bg-emerald-500 text-white'
                : done
                ? 'bg-emerald-100 text-emerald-900'
                : 'bg-slate-100 text-slate-500')
            }
          >
            {i + 1}. {s}
          </li>
        );
      })}
    </ol>
  );
}
