import { factKeyLabel } from '../factKeyLabel';
import { usePrefsStore } from '../../lib/store';

interface Props {
  factKey: string;
  // Visual variant: "missing" (dashed border, the to-do look),
  // "filled" (solid, the done look), "neutral".
  variant?: 'missing' | 'filled' | 'neutral';
  // Optional prefix label ("缺：邮箱").
  prefix?: string;
  className?: string;
}

const VARIANT_CLS: Record<NonNullable<Props['variant']>, string> = {
  missing:
    'rounded border border-dashed border-slate-300 bg-white px-1.5 py-0.5 text-[10px] text-slate-600',
  filled:
    'rounded border border-slate-200 bg-slate-50 px-1.5 py-0.5 text-[10px] text-slate-700',
  neutral:
    'rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[10px] text-slate-700',
};

// Renders a fact key with the friendly Chinese label up front and the
// raw `namespace.key` in the tooltip. When the operator has flipped on
// "高级 / 显示原始字段" in Settings, the raw key is appended inline so
// devs can debug at a glance.

export function FactKeyChip({
  factKey,
  variant = 'missing',
  prefix,
  className,
}: Props) {
  const meta = factKeyLabel(factKey);
  const showRaw = usePrefsStore((s) => s.showRawFactKeys);
  return (
    <span className={`${VARIANT_CLS[variant]} ${className ?? ''}`} title={meta.title}>
      {prefix ? `${prefix}` : ''}
      {meta.short}
      {showRaw && (
        <span className="ml-1 text-[9px] font-mono text-slate-400">{factKey}</span>
      )}
    </span>
  );
}
