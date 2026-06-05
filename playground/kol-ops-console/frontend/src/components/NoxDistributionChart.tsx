import { useMemo, useState } from 'react';
import type { NoxAgeGroupChart, NoxChartSegment } from '../lib/noxDistributionParse';

const PIE_COLORS = [
  '#7c3aed',
  '#a78bfa',
  '#8b5cf6',
  '#6d28d9',
  '#c4b5fd',
  '#5b21b6',
  '#ddd6fe',
  '#4c1d95',
  '#ede9fe',
  '#9333ea',
];

function formatVal(value: number, unit: '%' | ''): string {
  if (unit === '%') return `${value.toFixed(1)}%`;
  return value >= 1000 ? value.toLocaleString() : String(Math.round(value));
}

type Slice = NoxChartSegment & {
  color: string;
  startAngle: number;
  endAngle: number;
  sharePct: number;
};

function polar(cx: number, cy: number, r: number, deg: number) {
  const rad = ((deg - 90) * Math.PI) / 180;
  return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) };
}

function arcPath(cx: number, cy: number, r: number, start: number, end: number): string {
  const sweep = end - start;
  if (sweep >= 359.99) {
    return [
      `M ${cx - r} ${cy}`,
      `A ${r} ${r} 0 1 1 ${cx + r} ${cy}`,
      `A ${r} ${r} 0 1 1 ${cx - r} ${cy}`,
      'Z',
    ].join(' ');
  }
  const s = polar(cx, cy, r, end);
  const e = polar(cx, cy, r, start);
  const large = sweep > 180 ? 1 : 0;
  return `M ${cx} ${cy} L ${s.x} ${s.y} A ${r} ${r} 0 ${large} 0 ${e.x} ${e.y} Z`;
}

function explodeTransform(start: number, end: number, amount: number): string {
  const mid = (start + end) / 2;
  const { x, y } = polar(0, 0, amount, mid);
  return `translate(${x}, ${y})`;
}

function buildSlices(segments: NoxChartSegment[]): Slice[] {
  const total = segments.reduce((sum, s) => sum + Math.max(0, s.value), 0) || 1;
  let angle = 0;
  return segments.map((seg, i) => {
    const value = Math.max(0, seg.value);
    const sweep = (value / total) * 360;
    const slice: Slice = {
      ...seg,
      color: PIE_COLORS[i % PIE_COLORS.length],
      startAngle: angle,
      endAngle: angle + sweep,
      sharePct: (value / total) * 100,
    };
    angle += sweep;
    return slice;
  });
}

function sliceDisplayValue(slice: Slice, unit: '%' | ''): string {
  if (slice.caption) return slice.caption;
  if (unit === '%') return formatVal(slice.value, unit);
  return `${formatVal(slice.value, unit)}（占 ${slice.sharePct.toFixed(1)}%）`;
}

function sliceShareLabel(slice: Slice, unit: '%' | ''): string {
  if (unit === '%') return sliceDisplayValue(slice, unit);
  return `${slice.sharePct.toFixed(1)}%`;
}

type PieProps = {
  segments: NoxChartSegment[];
  unit?: '%' | '';
  size?: 'sm' | 'md';
  /** Horizontal pie + legend. */
  compact?: boolean;
  /** Pie above legend (fits narrow popovers). */
  stacked?: boolean;
  /** Keep legend labels intact (e.g. age ranges like 13-17). */
  fullLabels?: boolean;
};

export function NoxPieChart({
  segments,
  unit = '%',
  size = 'md',
  compact = false,
  stacked = false,
  fullLabels = false,
}: PieProps) {
  const [activeLabel, setActiveLabel] = useState<string | null>(null);
  const [hoverLabel, setHoverLabel] = useState<string | null>(null);

  const slices = useMemo(() => buildSlices(segments), [segments]);
  const focusedLabel = hoverLabel ?? activeLabel;
  const focused = slices.find((s) => s.label === focusedLabel) ?? null;

  if (!slices.length) return null;

  const horizontal = (compact || size === 'md') && !stacked;
  const dim = size === 'sm' ? 'h-12 w-12' : 'h-14 w-14';
  const cx = 16;
  const cy = 16;
  const r = 15;
  const explode = size === 'sm' ? 0.5 : 0.65;
  const legendCols = fullLabels ? 1 : slices.length > 5 ? 2 : 1;

  const focusSlice = (label: string) => {
    setActiveLabel((prev) => (prev === label ? null : label));
  };

  const centerLabel = focused
    ? fullLabels
      ? focused.label
      : focused.label.length > 7
        ? `${focused.label.slice(0, 6)}…`
        : focused.label
    : '';

  return (
    <div
      className={[
        'min-w-0 max-w-full',
        horizontal
          ? 'flex items-start gap-2'
          : 'flex flex-col items-stretch gap-1.5',
      ].join(' ')}
      role="group"
      aria-label="分布饼图，点击区块查看占比"
    >
      <svg
        viewBox="0 0 32 32"
        className={[dim, 'shrink-0', stacked ? 'mx-auto' : ''].join(' ')}
      >
        {slices.map((s) => {
          const isFocused = focusedLabel === s.label;
          const dimOthers = Boolean(focusedLabel) && !isFocused;
          return (
            <path
              key={s.label}
              d={arcPath(cx, cy, r, s.startAngle, s.endAngle)}
              fill={s.color}
              stroke={isFocused ? '#4c1d95' : '#fff'}
              strokeWidth={isFocused ? 0.9 : 0.4}
              opacity={dimOthers ? 0.38 : 1}
              transform={
                isFocused ? explodeTransform(s.startAngle, s.endAngle, explode) : undefined
              }
              className="cursor-pointer transition-all duration-150"
              onClick={() => focusSlice(s.label)}
              onMouseEnter={() => setHoverLabel(s.label)}
              onMouseLeave={() => setHoverLabel(null)}
            >
              <title>
                {s.label}: {sliceDisplayValue(s, unit)}
              </title>
            </path>
          );
        })}
        <circle cx={cx} cy={cy} r={size === 'sm' ? 5 : 5.5} fill="#fff" pointerEvents="none" />
        {focused && horizontal && (
          <>
            <text
              x={cx}
              y={cy - 0.5}
              textAnchor="middle"
              fill="#4c1d95"
              fontSize="2.4"
              fontWeight="600"
              pointerEvents="none"
            >
              {centerLabel}
            </text>
            <text
              x={cx}
              y={cy + 2.5}
              textAnchor="middle"
              fill="#6d28d9"
              fontSize="2.6"
              fontWeight="600"
              pointerEvents="none"
            >
              {sliceShareLabel(focused, unit)}
            </text>
          </>
        )}
      </svg>

      <div className="min-w-0 max-w-full flex-1 overflow-hidden">
        {focused && (fullLabels || !horizontal) && (
          <div
            className="mb-1 rounded-md border border-violet-200 bg-violet-50 px-2 py-1 text-xs leading-snug text-violet-900"
            aria-live="polite"
          >
            <span className="font-semibold">{focused.label}</span>
            <span className="ml-1.5 tabular-nums">{sliceDisplayValue(focused, unit)}</span>
          </div>
        )}

        <ul
          className={[
            'max-w-full text-xs leading-snug',
            stacked || legendCols === 1 ? 'space-y-0.5' : 'grid grid-cols-2 gap-x-2 gap-y-0.5',
          ].join(' ')}
        >
          {slices.map((s) => {
            const isFocused = focusedLabel === s.label;
            return (
              <li key={s.label}>
                <button
                  type="button"
                  onClick={() => focusSlice(s.label)}
                  onMouseEnter={() => setHoverLabel(s.label)}
                  onMouseLeave={() => setHoverLabel(null)}
                  className={[
                    'flex w-full items-center gap-1.5 rounded px-1 py-0.5 text-left transition',
                    isFocused
                      ? 'bg-violet-100 ring-1 ring-violet-300'
                      : 'hover:bg-violet-50/80',
                  ].join(' ')}
                  aria-pressed={activeLabel === s.label}
                  title={`${s.label}: ${sliceDisplayValue(s, unit)}`}
                >
                  <span
                    className="h-2 w-2 shrink-0 rounded-full"
                    style={{ backgroundColor: s.color }}
                  />
                  <span
                    className={[
                      'min-w-0 flex-1 break-words',
                      stacked ? 'line-clamp-2' : fullLabels ? 'truncate' : 'truncate',
                      isFocused ? 'font-semibold text-violet-900' : 'text-slate-600',
                    ].join(' ')}
                  >
                    {s.label}
                  </span>
                  <span className="shrink-0 tabular-nums text-slate-800">
                    {sliceDisplayValue(s, unit)}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}

type BarProps = {
  segments: NoxChartSegment[];
  unit?: '%' | '';
  maxValue?: number;
};

/** Compact bars for scores / ranks (not composition). */
export function NoxHorizontalBarChart({ segments, unit = '%', maxValue }: BarProps) {
  if (!segments.length) return null;
  const max = maxValue ?? Math.max(...segments.map((s) => s.value), 1);

  return (
    <div className="space-y-1.5" role="img" aria-label="指标条形图">
      {segments.map((s) => (
        <div key={s.label}>
          <div className="mb-0.5 flex justify-between gap-2 text-xs text-slate-600">
            <span className="truncate font-medium">{s.label}</span>
            <span className="shrink-0 tabular-nums text-slate-800">
              {s.caption ?? formatVal(s.value, unit)}
            </span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-slate-100">
            <div
              className="h-full rounded-full bg-violet-500"
              style={{ width: `${Math.min(100, (s.value / max) * 100)}%` }}
            />
          </div>
        </div>
      ))}
    </div>
  );
}

export function NoxAgeSplitChart({
  groups,
  stacked = false,
}: {
  groups: NoxAgeGroupChart[];
  stacked?: boolean;
}) {
  return (
    <div
      className={[
        'grid min-w-0 gap-2',
        stacked ? 'grid-cols-1' : 'sm:grid-cols-2',
      ].join(' ')}
      role="group"
      aria-label="年龄分布饼图"
    >
      {groups.map((g) => (
        <div
          key={g.group}
          className="min-w-0 overflow-hidden rounded-md bg-violet-50/40 px-2 py-1.5"
        >
          <div className="mb-1 text-xs font-semibold text-violet-800">{g.group}</div>
          <NoxPieChart
            segments={g.segments}
            unit="%"
            size="sm"
            compact
            stacked={stacked}
            fullLabels
          />
        </div>
      ))}
    </div>
  );
}
