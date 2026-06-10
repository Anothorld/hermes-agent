export type TrendPoint = { bucket: string; value: number | null };

export type TrendValueFormat = 'percent' | 'minutes' | 'decimal';

function formatTrendValue(value: number, format: TrendValueFormat): string {
  if (format === 'percent') {
    return `${(value * 100).toFixed(1)}%`;
  }
  if (format === 'minutes') {
    return `${value.toFixed(1)} 分钟`;
  }
  return value.toFixed(2);
}

function bucketLabel(bucket: string): string {
  if (/^\d{4}-W\d{2}$/.test(bucket)) {
    return bucket.replace('-W', ' 第') + '周';
  }
  if (/^\d{4}-\d{2}-\d{2}$/.test(bucket)) {
    return bucket.slice(5);
  }
  if (/^\d{4}-\d{2}$/.test(bucket)) {
    return bucket;
  }
  if (/^\d{4}$/.test(bucket)) {
    return `${bucket}年`;
  }
  return bucket;
}

export function MetricTrendSparkline({
  points,
  valueFormat,
  colorClass = 'bg-sky-500',
}: {
  points?: TrendPoint[];
  valueFormat: TrendValueFormat;
  colorClass?: string;
}) {
  if (!points?.length) {
    return (
      <div className="mt-2 text-[11px] text-slate-400">暂无趋势数据</div>
    );
  }

  const numeric = points
    .map((p) => p.value)
    .filter((v): v is number => v !== null && Number.isFinite(v));
  if (!numeric.length) {
    return (
      <div className="mt-2 text-[11px] text-slate-400">该时段暂无记录</div>
    );
  }

  const max = Math.max(...numeric, 0.0001);
  const min = Math.min(...numeric);
  const range = Math.max(max - min, max * 0.05, 0.0001);

  return (
    <div className="mt-2">
      <div className="flex items-end gap-0.5" style={{ height: 40 }}>
        {points.map((p) => {
          const hasValue = p.value !== null && Number.isFinite(p.value);
          const heightPx = hasValue
            ? Math.max(3, Math.round(((p.value! - min) / range) * 36))
            : 2;
          const title = hasValue
            ? `${bucketLabel(p.bucket)} · ${formatTrendValue(p.value!, valueFormat)}`
            : `${bucketLabel(p.bucket)} · 无数据`;
          return (
            <div
              key={p.bucket}
              className="group relative flex-1"
              title={title}
            >
              <div
                className={`w-full rounded-t ${hasValue ? colorClass : 'bg-slate-200'}`}
                style={{ height: `${heightPx}px` }}
              />
            </div>
          );
        })}
      </div>
      <div className="mt-1 flex justify-between text-[10px] text-slate-400">
        <span>{bucketLabel(points[0].bucket)}</span>
        <span>{bucketLabel(points[points.length - 1].bucket)}</span>
      </div>
    </div>
  );
}
