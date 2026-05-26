import { useEffect, useRef } from 'react';
import type { ConfirmOptions } from './useDialog';

interface Props extends ConfirmOptions {
  onCancel: () => void;
  onConfirm: () => void;
}

export function ConfirmDialog({
  title,
  description,
  confirmLabel = '确定',
  cancelLabel = '取消',
  variant = 'info',
  liveWarning = false,
  onCancel,
  onConfirm,
}: Props) {
  const confirmBtn = useRef<HTMLButtonElement | null>(null);
  // Auto-focus the primary action so Enter/Space confirms by default
  // (matches the muscle memory of window.confirm).
  useEffect(() => {
    confirmBtn.current?.focus();
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCancel();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onCancel]);

  const isDanger = variant === 'danger';
  const confirmCls = isDanger
    ? 'bg-red-600 hover:bg-red-700 focus:ring-red-400'
    : 'bg-emerald-600 hover:bg-emerald-700 focus:ring-emerald-400';

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 px-4 py-6"
      role="dialog"
      aria-modal="true"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onCancel();
      }}
    >
      <div className="w-full max-w-md rounded-lg bg-white shadow-xl">
        <div className="border-b border-slate-200 px-4 py-3 text-sm font-semibold text-slate-800">
          {title}
        </div>
        <div className="space-y-2 px-4 py-3 text-sm text-slate-700">
          {description && <div className="whitespace-pre-wrap">{description}</div>}
          {liveWarning && (
            <div className="rounded border border-red-300 bg-red-50 px-2 py-1.5 text-xs text-red-800">
              <span className="font-semibold">LIVE 环境</span>
              ：此操作会写入正式数据，<strong>不可撤销</strong>。请确认无误。
            </div>
          )}
        </div>
        <div className="flex items-center justify-end gap-2 border-t border-slate-200 bg-slate-50 px-4 py-2">
          <button
            type="button"
            className="rounded border border-slate-300 bg-white px-3 py-1 text-sm text-slate-700 hover:bg-slate-100"
            onClick={onCancel}
          >
            {cancelLabel}
          </button>
          <button
            type="button"
            ref={confirmBtn}
            className={`rounded px-3 py-1 text-sm font-medium text-white focus:outline-none focus:ring-2 ${confirmCls}`}
            onClick={onConfirm}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
