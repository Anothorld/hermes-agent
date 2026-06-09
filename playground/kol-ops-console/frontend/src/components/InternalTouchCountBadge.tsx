/**
 * Workbook-based prior touch count (gate-metrics「内部曾触达次数」).
 */
export function InternalTouchCountBadge({
  count,
}: {
  count: number | null | undefined;
}) {
  if (count == null || count <= 0) return null;
  return (
    <span
      className="rounded border border-violet-200 bg-violet-50 px-1.5 py-0.5 text-[10px] font-medium text-violet-900"
      title={`在曾触达列表.xlsx 中匹配 ${count} 行（与指标页「内部曾触达次数」一致）`}
    >
      内部曾触达 · {count}次
    </span>
  );
}
