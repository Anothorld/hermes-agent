export type EscalationTopicCard = {
  id: string;
  label: string;
  summary: string;
  status: 'needs_decision' | 'auto_reply';
  status_label: string;
};

const STATUS_CLASS: Record<EscalationTopicCard['status'], string> = {
  needs_decision: 'border-rose-200 bg-rose-50',
  auto_reply: 'border-emerald-200 bg-emerald-50/80',
};

const BADGE_CLASS: Record<EscalationTopicCard['status'], string> = {
  needs_decision: 'bg-rose-100 text-rose-900',
  auto_reply: 'bg-emerald-100 text-emerald-900',
};

export function EscalationTopicCards({ cards }: { cards: EscalationTopicCard[] }) {
  if (!cards.length) return null;
  const multi = cards.length > 1;
  return (
    <section className="rounded border border-slate-200 bg-white p-3">
      <h2 className="text-sm font-semibold text-slate-900">
        {multi ? '本封来信包含多个话题' : '来信话题'}
      </h2>
      {multi && (
        <p className="mt-1 text-xs text-slate-600">
          标红话题需你在下方「操作员答复」中明确决定；绿色话题可由预览稿自动回复。
        </p>
      )}
      <ul className="mt-2 space-y-2">
        {cards.map((card) => (
          <li
            key={card.id}
            className={`rounded border p-2 text-sm ${STATUS_CLASS[card.status]}`}
          >
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium text-slate-900">{card.label}</span>
              <span
                className={
                  'rounded px-1.5 py-0.5 text-[10px] font-medium ' +
                  BADGE_CLASS[card.status]
                }
              >
                {card.status_label}
              </span>
            </div>
            <p className="mt-1 text-xs leading-relaxed text-slate-700">{card.summary}</p>
          </li>
        ))}
      </ul>
    </section>
  );
}
