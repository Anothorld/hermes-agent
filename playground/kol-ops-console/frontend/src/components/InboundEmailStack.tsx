import { useState } from 'react';
import InboundEmailCard, { type InboundEmail } from './InboundEmailCard';

export type PendingInbound = InboundEmail & {
  role?: string | null;
  label?: string | null;
};

type Props = {
  items: PendingInbound[];
  defaultExpanded?: boolean;
};

/**
 * Collapsible stack of KOL inbounds awaiting operator action on an escalation
 * (trigger + follow-ups during awaiting_answer).
 */
export default function InboundEmailStack({
  items,
  defaultExpanded = false,
}: Props) {
  const [open, setOpen] = useState(defaultExpanded);
  if (items.length === 0) {
    return (
      <div className="rounded border border-dashed border-slate-300 p-3 text-sm text-slate-500">
        暂无关联 KOL 回信记录。
      </div>
    );
  }
  if (items.length === 1) {
    const one = items[0];
    return (
      <InboundEmailCard
        inbound={one}
        title={one.label ?? 'KOL 回信'}
        variant="rose"
      />
    );
  }
  const latest = items[items.length - 1];
  return (
    <div className="rounded border border-rose-200 bg-rose-50/30">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm"
      >
        <span className="font-medium text-rose-900">
          待处理 KOL 回信（{items.length} 封）
        </span>
        <span className="text-xs text-rose-700">
          {open ? '收起' : '展开查看全部'}
        </span>
      </button>
      {!open && latest && (
        <div className="border-t border-rose-100 px-3 py-2 text-xs text-rose-800">
          <span className="font-medium">{latest.label ?? '最新'}：</span>
          {latest.subject ? `「${latest.subject}」` : null}
          {latest.snippet ? (
            <span className="ml-1 text-rose-700">{latest.snippet.slice(0, 120)}</span>
          ) : null}
        </div>
      )}
      {open && (
        <ul className="space-y-2 border-t border-rose-100 p-3">
          {items.map((item, idx) => (
            <li key={`${item.message_id ?? idx}-${item.ts ?? idx}`}>
              <InboundEmailCard
                inbound={item}
                title={item.label ?? `回信 ${idx + 1}`}
                variant="rose"
              />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
