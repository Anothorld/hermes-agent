import { useId, useMemo } from 'react';
import { factKeyLabel } from '../factKeyLabel';
import type { FactKind } from '../factKeyLabel';

interface Props {
  factKey: string;
  value: unknown;
  onChange: (next: unknown) => void;
  // Override the kind derived from factKeyLabel. Rare — usually leave undefined.
  kindOverride?: FactKind;
  // Compact mode renders without label / outer wrapper (caller renders its own).
  bare?: boolean;
  // Disable the input.
  disabled?: boolean;
  // Optional placeholder for text-ish inputs.
  placeholder?: string;
}

// Type-aware fact input. Replaces the freeform `<input placeholder="value
// (string / number / true|false / [a,b])">` that used to live in
// FactsEditor + EscalationDetail. The component returns the typed
// value (not a coerced string) so callers no longer need a coerce()
// helper.

export function FactInput({
  factKey,
  value,
  onChange,
  kindOverride,
  bare = false,
  disabled = false,
  placeholder,
}: Props) {
  const meta = useMemo(() => factKeyLabel(factKey), [factKey]);
  const kind = kindOverride ?? meta.kind ?? 'string';
  const id = useId();

  const inputNode = renderInputByKind({
    kind,
    value,
    onChange,
    disabled,
    placeholder,
    enumOptions: meta.enumOptions,
    id,
  });

  if (bare) return inputNode;
  return (
    <label htmlFor={id} className="flex items-center gap-2 text-sm">
      <span
        className="w-40 shrink-0 truncate text-xs text-slate-700"
        title={meta.title}
      >
        {meta.short}
      </span>
      {inputNode}
    </label>
  );
}

function renderInputByKind(args: {
  kind: FactKind;
  value: unknown;
  onChange: (v: unknown) => void;
  disabled: boolean;
  placeholder?: string;
  enumOptions?: ReadonlyArray<{ value: string; label: string }>;
  id: string;
}) {
  const { kind, value, onChange, disabled, placeholder, enumOptions, id } = args;
  const base =
    'flex-1 rounded border border-slate-300 px-2 py-1 text-sm focus:border-emerald-400 focus:outline-none disabled:bg-slate-50 disabled:text-slate-400';
  switch (kind) {
    case 'bool': {
      const checked = value === true || value === 'true' || value === 1;
      return (
        <label
          htmlFor={id}
          className="inline-flex items-center gap-2 text-sm text-slate-700"
        >
          <input
            id={id}
            type="checkbox"
            disabled={disabled}
            checked={checked}
            onChange={(e) => onChange(e.target.checked)}
            className="h-4 w-4 rounded border-slate-300 text-emerald-600 focus:ring-emerald-500"
          />
          <span className="text-xs text-slate-500">{checked ? '是' : '否'}</span>
        </label>
      );
    }
    case 'enum': {
      const cur = typeof value === 'string' ? value : '';
      return (
        <select
          id={id}
          value={cur}
          disabled={disabled}
          onChange={(e) => onChange(e.target.value || null)}
          className={base}
        >
          <option value="">— 选择 —</option>
          {enumOptions?.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      );
    }
    case 'number':
    case 'currency': {
      const cur = typeof value === 'number' ? value : value === '' ? '' : Number(value);
      return (
        <input
          id={id}
          type="number"
          inputMode="decimal"
          disabled={disabled}
          value={Number.isNaN(cur) ? '' : (cur as number | '')}
          placeholder={placeholder ?? (kind === 'currency' ? '金额' : '数字')}
          onChange={(e) =>
            onChange(e.target.value === '' ? '' : Number(e.target.value))
          }
          className={base}
        />
      );
    }
    case 'datetime': {
      // Browser-native local-datetime picker. Bridge stores ISO; convert
      // on both edges so the picker shows the user's wall-clock time.
      const toLocal = (iso: unknown): string => {
        if (typeof iso !== 'string' || !iso) return '';
        const d = new Date(iso);
        if (Number.isNaN(d.getTime())) return '';
        const pad = (n: number) => String(n).padStart(2, '0');
        return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
      };
      return (
        <input
          id={id}
          type="datetime-local"
          disabled={disabled}
          value={toLocal(value)}
          onChange={(e) => {
            const v = e.target.value;
            onChange(v ? new Date(v).toISOString() : '');
          }}
          className={base}
        />
      );
    }
    case 'email':
      return (
        <input
          id={id}
          type="email"
          disabled={disabled}
          value={typeof value === 'string' ? value : ''}
          placeholder={placeholder ?? 'name@example.com'}
          onChange={(e) => onChange(e.target.value)}
          className={base}
        />
      );
    case 'url':
      return (
        <input
          id={id}
          type="url"
          disabled={disabled}
          value={typeof value === 'string' ? value : ''}
          placeholder={placeholder ?? 'https://…'}
          onChange={(e) => onChange(e.target.value)}
          className={base}
        />
      );
    case 'json': {
      // JSON inputs are rare and almost always read-only displays;
      // when an operator needs to edit one we show a textarea + best-
      // effort parse. Invalid JSON is left as a string so submission
      // doesn't drop the value silently.
      const text =
        typeof value === 'string'
          ? value
          : value == null
          ? ''
          : JSON.stringify(value);
      return (
        <textarea
          id={id}
          disabled={disabled}
          value={text}
          placeholder={placeholder ?? '{"key":"value"}'}
          rows={3}
          onChange={(e) => {
            const raw = e.target.value;
            try {
              onChange(JSON.parse(raw));
            } catch {
              onChange(raw);
            }
          }}
          className={`${base} font-mono text-xs`}
        />
      );
    }
    case 'string':
    default:
      return (
        <input
          id={id}
          type="text"
          disabled={disabled}
          value={typeof value === 'string' ? value : value == null ? '' : String(value)}
          placeholder={placeholder}
          onChange={(e) => onChange(e.target.value)}
          className={base}
        />
      );
  }
}
