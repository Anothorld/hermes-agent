/**
 * Inline badge marking repeat collaborators. Tooltip surfaces the last
 * collaboration outcome so operators can prioritise warm KOLs.
 */
export function RepeatKolBadge({
  count,
  lastOutcome,
}: {
  count: number;
  lastOutcome?: string | null;
}) {
  if (!count) return null;
  const tone =
    lastOutcome === 'success'
      ? 'bg-emerald-100 text-emerald-800'
      : lastOutcome === 'declined' || lastOutcome === 'aborted'
      ? 'bg-amber-100 text-amber-800'
      : 'bg-sky-100 text-sky-800';
  const tip = lastOutcome
    ? `Repeat KOL — ${count}× collaborated · last outcome: ${lastOutcome}`
    : `Repeat KOL — ${count}× collaborated`;
  return (
    <span
      className={`ml-1 rounded px-1.5 py-0.5 text-[10px] font-medium ${tone}`}
      title={tip}
    >
      ⟳{count}
    </span>
  );
}
