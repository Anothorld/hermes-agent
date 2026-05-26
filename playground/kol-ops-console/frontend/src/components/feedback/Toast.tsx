import { useEffect } from 'react';
import type { ToastItem } from '../../lib/store';
import { useToastStore } from '../../lib/store';

const KIND_CLS: Record<ToastItem['kind'], string> = {
  success: 'border-emerald-300 bg-emerald-50 text-emerald-900',
  error: 'border-rose-300 bg-rose-50 text-rose-900',
  info: 'border-sky-300 bg-sky-50 text-sky-900',
  progress: 'border-slate-300 bg-slate-50 text-slate-700',
};

const KIND_ICON: Record<ToastItem['kind'], string> = {
  success: '✓',
  error: '✕',
  info: 'i',
  progress: '⌛',
};

export function Toast({ item }: { item: ToastItem }) {
  const dismiss = useToastStore((s) => s.dismiss);

  useEffect(() => {
    if (!item.durationMs || item.durationMs <= 0) return;
    const t = setTimeout(() => dismiss(item.id), item.durationMs);
    return () => clearTimeout(t);
  }, [dismiss, item.durationMs, item.id]);

  return (
    <div
      role="status"
      className={`pointer-events-auto flex w-full max-w-sm items-start gap-2 rounded border px-3 py-2 text-sm shadow ${KIND_CLS[item.kind]}`}
    >
      <span className="mt-0.5 flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-full bg-white/70 text-xs font-bold">
        {KIND_ICON[item.kind]}
      </span>
      <div className="min-w-0 flex-1">
        <div className="font-medium leading-snug">{item.title}</div>
        {item.detail && (
          <div className="mt-0.5 break-words text-xs leading-snug opacity-80">
            {item.detail}
          </div>
        )}
      </div>
      <button
        type="button"
        onClick={() => dismiss(item.id)}
        className="ml-1 flex-shrink-0 rounded px-1 text-xs opacity-60 hover:bg-white/40 hover:opacity-100"
        aria-label="dismiss"
      >
        ✕
      </button>
    </div>
  );
}
