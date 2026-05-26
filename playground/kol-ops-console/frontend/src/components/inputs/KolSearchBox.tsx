import { useEffect, useState } from 'react';

interface Props {
  value: string;
  onChange: (next: string) => void;
  placeholder?: string;
  className?: string;
}

// Debounced text input for filtering the Kanban by KOL handle / email.
// Local state holds the in-progress text; the debounced value bubbles
// up so list filtering doesn't thrash on every keystroke.

export function KolSearchBox({
  value,
  onChange,
  placeholder = '搜索 @handle 或邮箱',
  className,
}: Props) {
  const [local, setLocal] = useState(value);

  // Keep local in sync if the parent resets (e.g. campaign switch).
  useEffect(() => {
    setLocal(value);
  }, [value]);

  // Debounce: push up after 200ms of stillness.
  useEffect(() => {
    if (local === value) return;
    const t = setTimeout(() => onChange(local), 200);
    return () => clearTimeout(t);
  }, [local, onChange, value]);

  return (
    <div className={`relative ${className ?? ''}`}>
      <input
        value={local}
        onChange={(e) => setLocal(e.target.value)}
        placeholder={placeholder}
        className="w-56 rounded border border-slate-300 bg-white py-1 pl-7 pr-7 text-sm focus:border-emerald-400 focus:outline-none"
      />
      <span className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-xs text-slate-400">
        ⌕
      </span>
      {local && (
        <button
          type="button"
          onClick={() => {
            setLocal('');
            onChange('');
          }}
          className="absolute right-1 top-1/2 -translate-y-1/2 rounded px-1 text-xs text-slate-400 hover:bg-slate-100"
          aria-label="clear"
        >
          ✕
        </button>
      )}
    </div>
  );
}
