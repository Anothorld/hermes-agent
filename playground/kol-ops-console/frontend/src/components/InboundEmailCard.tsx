import { useState } from 'react';

export type InboundEmail = {
  event_id?: number | null;
  ts?: string | null;
  from_addr?: string | null;
  subject?: string | null;
  body?: string | null;
  snippet?: string | null;
  date?: string | null;
  message_id?: string | null;
  thread_id?: string | null;
};

const COLLAPSED_BODY_CHARS = 1200;

function formatTs(ts: string | null | undefined): string {
  if (!ts) return '';
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts.slice(0, 19);
  // ISO without seconds drift, in local tz.
  return d.toLocaleString();
}

/**
 * Compact email-rendering card used by both EscalationConsolePage and
 * ApprovalsPage to surface the inbound message that triggered the row.
 *
 * Renders subject + from + date as the header; body (or snippet when no
 * body was persisted) inside a scrollable region, truncated to ~1200
 * chars unless the operator clicks "show full". Falls back to a muted
 * "no inbound on file" state so callers don't need conditional logic.
 */
export default function InboundEmailCard({
  inbound,
  title = 'Inbound email that triggered this',
  variant = 'rose',
}: {
  inbound: InboundEmail | null | undefined;
  title?: string;
  // The card lives inside differently-themed sections (rose for escalation,
  // sky for approval). Match the parent so it doesn't feel pasted in.
  variant?: 'rose' | 'sky' | 'slate';
}) {
  const [expanded, setExpanded] = useState(false);
  const tone =
    variant === 'sky'
      ? 'border-sky-200 bg-sky-50/40'
      : variant === 'slate'
        ? 'border-slate-200 bg-slate-50'
        : 'border-rose-200 bg-rose-50/40';
  const labelTone =
    variant === 'sky'
      ? 'text-sky-800'
      : variant === 'slate'
        ? 'text-slate-700'
        : 'text-rose-800';

  if (!inbound) {
    return (
      <div className={`rounded border px-3 py-2 text-xs ${tone}`}>
        <div className={`text-[11px] font-medium uppercase tracking-wide ${labelTone}`}>
          {title}
        </div>
        <div className="mt-0.5 italic text-slate-500">
          (CAL 中没有匹配的 inbound 邮件 — 可能此 escalation 来自 discovery 或配置阶段，并非由 KOL 回信触发)
        </div>
      </div>
    );
  }

  const body = (inbound.body && inbound.body.trim()) || (inbound.snippet && inbound.snippet.trim()) || '';
  const bodyTruncated = !expanded && body.length > COLLAPSED_BODY_CHARS;
  const bodyDisplay = bodyTruncated ? `${body.slice(0, COLLAPSED_BODY_CHARS)}…` : body;
  const onlySnippet = (!inbound.body || !inbound.body.trim()) && Boolean(inbound.snippet);

  return (
    <div className={`rounded border ${tone} text-sm`}>
      <div className="border-b border-current/10 px-3 py-2">
        <div className={`text-[11px] font-semibold uppercase tracking-wide ${labelTone}`}>
          {title}
        </div>
        <div className="mt-1 space-y-0.5 text-xs text-slate-700">
          <div className="flex flex-wrap items-baseline gap-1">
            <span className="font-medium text-slate-500">From:</span>
            <span className="font-mono">{inbound.from_addr || '(unknown sender)'}</span>
          </div>
          {inbound.subject && (
            <div className="flex flex-wrap items-baseline gap-1">
              <span className="font-medium text-slate-500">Subject:</span>
              <span className="font-medium text-slate-800">{inbound.subject}</span>
            </div>
          )}
          {(inbound.date || inbound.ts) && (
            <div className="flex flex-wrap items-baseline gap-1">
              <span className="font-medium text-slate-500">Date:</span>
              <span>{inbound.date || formatTs(inbound.ts)}</span>
            </div>
          )}
          {inbound.message_id && (
            <div className="text-[10px] text-slate-400">
              msg-id: <span className="font-mono">{inbound.message_id}</span>
            </div>
          )}
        </div>
      </div>
      <div className="max-h-72 overflow-y-auto px-3 py-2">
        {body ? (
          <pre className="whitespace-pre-wrap break-words font-sans text-[12.5px] leading-relaxed text-slate-800">
            {bodyDisplay}
          </pre>
        ) : (
          <div className="italic text-slate-500">(邮件正文未捕获)</div>
        )}
        {onlySnippet && body && (
          <div className="mt-1 text-[10px] italic text-slate-500">
            (仅 Gmail snippet — 完整正文为本次升级前的旧事件，无法回填)
          </div>
        )}
        {bodyTruncated && (
          <button
            type="button"
            onClick={() => setExpanded(true)}
            className="mt-1 text-[11px] text-sky-700 hover:underline"
          >
            展开全文（{body.length} 字符）
          </button>
        )}
        {!bodyTruncated && body.length > COLLAPSED_BODY_CHARS && (
          <button
            type="button"
            onClick={() => setExpanded(false)}
            className="mt-1 text-[11px] text-sky-700 hover:underline"
          >
            折叠
          </button>
        )}
      </div>
    </div>
  );
}
