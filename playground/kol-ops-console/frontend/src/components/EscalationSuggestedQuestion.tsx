const FOLLOWUP_SPLIT = /\n\n---\n/;
const FOLLOWUP_HEAD = '【KOL 追信';

type Props = {
  text: string;
  compact?: boolean;
};

/**
 * Renders escalation ``suggested_question`` with amber highlight on
 * auto-appended KOL follow-up blocks.
 */
export default function EscalationSuggestedQuestion({ text, compact }: Props) {
  const parts = text.split(FOLLOWUP_SPLIT).filter((p) => p.trim());
  if (parts.length <= 1 && !text.includes(FOLLOWUP_HEAD)) {
    return (
      <div
        className={
          (compact ? 'line-clamp-2 ' : '') +
          'whitespace-pre-wrap text-sm leading-relaxed text-slate-700'
        }
        title={compact ? text : undefined}
      >
        {text}
      </div>
    );
  }
  const base = parts[0];
  const followups = parts.slice(1).filter((p) => p.includes(FOLLOWUP_HEAD));
  return (
    <div className="space-y-2 text-sm leading-relaxed">
      {base.trim() && (
        <div className={compact ? 'line-clamp-2 text-slate-700' : 'text-slate-800'}>
          {base.trim()}
        </div>
      )}
      {followups.map((block) => (
        <div
          key={block.slice(0, 48)}
          className={
            'rounded border border-amber-300 bg-amber-50 px-2 py-1.5 text-amber-950 ' +
            (compact ? 'line-clamp-3' : 'whitespace-pre-wrap')
          }
          title={compact ? block : undefined}
        >
          {block.trim()}
        </div>
      ))}
    </div>
  );
}
