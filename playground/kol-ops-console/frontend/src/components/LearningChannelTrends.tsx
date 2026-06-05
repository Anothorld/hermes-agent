type VolumeBucket = { bucket: string; count: number };

type ChannelTrends = {
  edits?: { buckets?: VolumeBucket[] };
  rejects?: { buckets?: VolumeBucket[]; total?: number };
  outcome_retros?: { buckets?: VolumeBucket[]; total?: number };
};

function MiniBars({
  label,
  buckets,
  color,
}: {
  label: string;
  buckets: VolumeBucket[];
  color: string;
}) {
  const slice = buckets.slice(-12);
  const max = Math.max(1, ...slice.map((b) => b.count));
  return (
    <div>
      <div className="text-[11px] font-medium text-slate-700">{label}</div>
      <div className="mt-1 flex items-end gap-0.5" style={{ height: 36 }}>
        {slice.map((b) => (
          <div
            key={b.bucket}
            className="flex-1"
            title={`${b.bucket} · ${b.count} 条`}
          >
            <div
              className={`w-full rounded-t ${color}`}
              style={{ height: `${Math.max(2, Math.round((b.count / max) * 32))}px` }}
            />
          </div>
        ))}
      </div>
    </div>
  );
}

/** Three learning channels: edit intensity, reject count, outcome retro count. */
export function LearningChannelTrends({ trends }: { trends?: ChannelTrends }) {
  if (!trends) return null;
  const editBuckets = trends.edits?.buckets ?? [];
  const rejectBuckets = trends.rejects?.buckets ?? [];
  const outcomeBuckets = trends.outcome_retros?.buckets ?? [];
  if (!editBuckets.length && !rejectBuckets.length && !outcomeBuckets.length) {
    return null;
  }
  return (
    <div className="rounded border border-slate-200 bg-white p-3">
      <div className="text-sm font-medium text-slate-800">三通道活动趋势（近 90 天 · 按周）</div>
      <p className="mt-0.5 text-[11px] text-slate-500">
        编辑通道看幅度（上一卡片）；此处为驳回次数与合作复盘条数。
      </p>
      <div className="mt-3 grid gap-4 sm:grid-cols-2">
        {rejectBuckets.length > 0 && (
          <MiniBars label="驳回回信" buckets={rejectBuckets} color="bg-amber-400" />
        )}
        {outcomeBuckets.length > 0 && (
          <MiniBars label="合作复盘" buckets={outcomeBuckets} color="bg-emerald-400" />
        )}
      </div>
    </div>
  );
}
