import { useEffect, useRef, useState } from 'react';
import type { PromptOptions } from './useDialog';

interface Props extends PromptOptions {
  onCancel: () => void;
  onSubmit: (value: string) => void;
}

export function PromptDialog({
  title,
  description,
  confirmLabel = '提交',
  cancelLabel = '取消',
  variant = 'info',
  liveWarning = false,
  placeholder,
  defaultValue = '',
  multiline = true,
  required = false,
  onCancel,
  onSubmit,
}: Props) {
  const [value, setValue] = useState(defaultValue);
  const inputRef = useRef<HTMLTextAreaElement | HTMLInputElement | null>(null);

  useEffect(() => {
    inputRef.current?.focus();
    // Select the prefilled text so the user can type to replace.
    if (inputRef.current && defaultValue) {
      inputRef.current.select?.();
    }
  }, [defaultValue]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCancel();
      // Ctrl/Cmd+Enter submits when multiline; plain Enter would just
      // newline the textarea.
      if (multiline && (e.ctrlKey || e.metaKey) && e.key === 'Enter') {
        if (!required || value.trim().length > 0) onSubmit(value);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [multiline, onCancel, onSubmit, required, value]);

  const disabled = required && value.trim().length === 0;
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
      <form
        className="w-full max-w-lg rounded-lg bg-white shadow-xl"
        onSubmit={(e) => {
          e.preventDefault();
          if (!disabled) onSubmit(value);
        }}
      >
        <div className="border-b border-slate-200 px-4 py-3 text-sm font-semibold text-slate-800">
          {title}
        </div>
        <div className="space-y-2 px-4 py-3 text-sm text-slate-700">
          {description && <div className="whitespace-pre-wrap">{description}</div>}
          {liveWarning && (
            <div className="rounded border border-red-300 bg-red-50 px-2 py-1.5 text-xs text-red-800">
              <span className="font-semibold">LIVE 环境</span>
              ：此操作会写入正式数据，<strong>不可撤销</strong>。
            </div>
          )}
          {multiline ? (
            <textarea
              ref={inputRef as React.RefObject<HTMLTextAreaElement>}
              value={value}
              placeholder={placeholder}
              onChange={(e) => setValue(e.target.value)}
              rows={4}
              className="w-full rounded border border-slate-300 px-2 py-1 text-sm focus:border-emerald-400 focus:outline-none"
            />
          ) : (
            <input
              ref={inputRef as React.RefObject<HTMLInputElement>}
              value={value}
              placeholder={placeholder}
              onChange={(e) => setValue(e.target.value)}
              className="w-full rounded border border-slate-300 px-2 py-1 text-sm focus:border-emerald-400 focus:outline-none"
            />
          )}
          {multiline && (
            <div className="text-[11px] text-slate-400">
              Ctrl/Cmd + Enter 快捷提交，Esc 取消
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
            type="submit"
            disabled={disabled}
            className={`rounded px-3 py-1 text-sm font-medium text-white focus:outline-none focus:ring-2 disabled:opacity-40 ${confirmCls}`}
          >
            {confirmLabel}
          </button>
        </div>
      </form>
    </div>
  );
}
