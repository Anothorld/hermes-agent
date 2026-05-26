import { useEffect, useState } from 'react';
import { formatAbsolute, formatRelativeAgo } from '../../lib/time';

interface Props {
  // ISO-8601 timestamp string (or number of ms since epoch).
  iso: string | number | null | undefined;
  // Optional className passthrough.
  className?: string;
  // Refresh interval for the relative label. Default 30s — coarse
  // enough that "5 分钟前" doesn't flicker every second, fine enough
  // that the operator's screen stays current.
  refreshIntervalMs?: number;
  // Optional prefix label ("初邀 12 分钟前" — prefix="初邀").
  prefix?: string;
}

// Renders a relative time (e.g. "5 分钟前") with the absolute timestamp
// in the `title` tooltip. Re-renders on a timer so a card left open
// updates "刚刚" → "1 分钟前" naturally.

export function TimeAgo({ iso, className, refreshIntervalMs = 30_000, prefix }: Props) {
  const normalised = typeof iso === 'number' ? new Date(iso).toISOString() : iso;
  const [, force] = useState(0);
  useEffect(() => {
    if (!normalised) return;
    const t = setInterval(() => force((x) => x + 1), refreshIntervalMs);
    return () => clearInterval(t);
  }, [normalised, refreshIntervalMs]);
  if (!normalised) return null;
  const rel = formatRelativeAgo(normalised);
  const abs = formatAbsolute(normalised);
  return (
    <span className={className} title={abs}>
      {prefix ? `${prefix} ` : ''}
      {rel}
    </span>
  );
}
