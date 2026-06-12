/**
 * Operator-facing recap of prior thread context on pending reply drafts.
 * Content is agent-generated at draft time and stored on the approval fact
 * (`conversation_summary.bullets`) — not sent in the Gmail body.
 */

export function parseConversationSummaryBullets(
  context: Record<string, unknown> | null | undefined,
): string[] {
  if (!context || typeof context !== 'object') return [];
  const summary = context.conversation_summary;
  if (!summary || typeof summary !== 'object' || Array.isArray(summary)) {
    return [];
  }
  const raw = (summary as Record<string, unknown>).bullets;
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((item): item is string => typeof item === 'string')
    .map((s) => s.trim())
    .filter(Boolean);
}

export default function ConversationSummaryCard({
  bullets,
}: {
  bullets: string[];
}) {
  if (bullets.length === 0) return null;

  return (
    <div className="rounded border border-indigo-200 bg-indigo-50/50 px-3 py-2.5">
      <div className="mb-1.5">
        <div className="text-xs font-semibold text-indigo-900">沟通历史要点</div>
        <div className="text-[10px] text-indigo-700/80">
          供快速浏览过往沟通，不会随邮件发送；请以来信原文为准核对。
        </div>
      </div>
      <ul className="list-inside list-disc space-y-1 text-[12px] leading-relaxed text-indigo-950">
        {bullets.map((line, i) => (
          <li key={`${i}-${line.slice(0, 24)}`} className="break-words">
            {line}
          </li>
        ))}
      </ul>
    </div>
  );
}
