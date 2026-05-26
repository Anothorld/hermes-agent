// Time helpers. Output is intentionally Chinese to match the rest of
// the operator-facing UI; pass to `<TimeAgo>` for the standard pair
// (relative label + absolute tooltip).

export function formatRelativeAgo(iso: string | null | undefined): string {
  if (!iso) return '';
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  const deltaSec = Math.max(0, Math.round((Date.now() - t) / 1000));
  if (deltaSec < 10) return '刚刚';
  if (deltaSec < 60) return `${deltaSec} 秒前`;
  const min = Math.round(deltaSec / 60);
  if (min < 60) return `${min} 分钟前`;
  const hr = Math.round(deltaSec / 3600);
  if (hr < 48) return `${hr} 小时前`;
  const day = Math.round(deltaSec / 86400);
  if (day < 30) return `${day} 天前`;
  const mon = Math.round(day / 30);
  if (mon < 12) return `${mon} 个月前`;
  return `${Math.round(day / 365)} 年前`;
}

// "2026-05-26 14:32" in local time. Used as the hover tooltip on every
// TimeAgo render so operators can recover the exact instant when the
// coarse relative label isn't enough.
export function formatAbsolute(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso ?? '';
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(
    d.getHours(),
  )}:${pad(d.getMinutes())}`;
}
