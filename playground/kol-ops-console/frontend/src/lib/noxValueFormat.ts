/** Normalize Nox API metric shapes (often `{ value, status }`) for display. */

import { formatMetric, formatPercent } from './kolProfileMetrics';

export function unwrapNoxMetric(raw: unknown): unknown {
  if (raw === null || raw === undefined) return null;
  if (typeof raw === 'string' && raw.trim().startsWith('{')) {
    try {
      const parsed = JSON.parse(raw) as unknown;
      return unwrapNoxMetric(parsed);
    } catch {
      return raw;
    }
  }
  if (typeof raw === 'object' && !Array.isArray(raw)) {
    const o = raw as Record<string, unknown>;
    if ('value' in o) return unwrapNoxMetric(o.value);
    if ('score' in o) return unwrapNoxMetric(o.score);
    if ('overall' in o) return o.overall;
  }
  return raw;
}

const NOX_SCORE_DIM_LABELS: Record<string, string> = {
  growth: '增长',
  creativity: '创意',
  audience: '受众',
  engagement: '互动',
  credibility: '可信',
};

function formatScorePart(value: unknown): string | null {
  if (value === null || value === undefined || value === '') return null;
  if (typeof value === 'number' && !Number.isNaN(value)) {
    return value <= 1 ? String(Math.round(value * 100)) : String(Math.round(value));
  }
  const n = Number(String(value).trim());
  if (!Number.isNaN(n)) {
    return n <= 1 ? String(Math.round(n * 100)) : String(Math.round(n));
  }
  return String(value).trim() || null;
}

function formatNoxScoreFromObject(o: Record<string, unknown>): string | null {
  const segments: string[] = [];
  const overall = formatScorePart(o.overall);
  segments.push(`综合 ${overall ?? '—'}`);
  for (const [key, label] of Object.entries(NOX_SCORE_DIM_LABELS)) {
    segments.push(`${label} ${formatScorePart(o[key]) ?? '—'}`);
  }
  return segments.join(' · ');
}

/** Nox composite score: ``{ overall, growth, audience, ... }`` — always show all dimensions. */
export function formatNoxScoreDisplay(raw: unknown): string | null {
  let v: unknown = raw;
  if (typeof v === 'string' && v.trim().startsWith('{')) {
    try {
      v = JSON.parse(v) as unknown;
    } catch {
      return formatMetric(v);
    }
  }
  if (typeof v === 'object' && v !== null && !Array.isArray(v)) {
    const o = v as Record<string, unknown>;
    if ('value' in o || 'score' in o) {
      return formatNoxScoreDisplay(unwrapNoxMetric(o));
    }
    if ('overall' in o) {
      return formatNoxScoreFromObject(o);
    }
  }
  const scalar = unwrapNoxMetric(v);
  if (scalar !== null && typeof scalar === 'object') return null;
  const main = formatScorePart(scalar);
  if (main === null) return null;
  return `综合 ${main}`;
}

/** Human-readable percent/score for authenticity-style metrics (0–1 or 0–100). */
export function formatAuthenticityDisplay(raw: unknown): string | null {
  const v = unwrapNoxMetric(raw);
  if (v === null || v === undefined || v === '') return null;
  if (typeof v === 'number' && !Number.isNaN(v)) {
    const pct = v <= 1 ? v * 100 : v;
    return `${pct.toFixed(0)}%`;
  }
  if (typeof v === 'string') {
    const n = Number(v);
    if (!Number.isNaN(n)) {
      const pct = n <= 1 ? n * 100 : n;
      return `${pct.toFixed(0)}%`;
    }
    return v.trim() || null;
  }
  return formatMetric(v);
}

export function formatNoxField(
  raw: unknown,
  format: 'metric' | 'percent' | 'text' | 'nox_score' = 'text',
): string | null {
  if (format === 'nox_score') return formatNoxScoreDisplay(raw);
  const v = unwrapNoxMetric(raw);
  if (v === null || v === undefined || v === '') return null;
  if (format === 'percent') return formatPercent(v) ?? formatAuthenticityDisplay(v);
  if (format === 'metric') {
    const score = formatNoxScoreDisplay(raw);
    if (score) return score;
    return formatMetric(v);
  }
  if (typeof v === 'boolean') return v ? '是' : '否';
  if (typeof v === 'number') return formatMetric(v);
  if (typeof v === 'string') return v.trim() || null;
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}
