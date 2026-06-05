/** Parse Nox fact strings into chart segments (no chart library). */

export type NoxChartSegment = {
  label: string;
  value: number;
  /** Override right-column text (e.g. L3 instead of 60%). */
  caption?: string;
};

export type NoxAgeGroupChart = {
  group: string;
  segments: NoxChartSegment[];
};

/** Labels rendered as charts instead of plain text grids. */
export const NOX_CHART_LABELS = new Set([
  '主要受众地区',
  '性别分布',
  '年龄分布',
  '成人/儿童占比',
  '受众语言',
  '受众类型',
  'Nox 评分',
  '多维对标排名',
  '表现等级',
  '内容形式数量',
  '分形式互动',
]);

const PCT_IN_PARENS = /^(.+?)\s*\(([\d.]+)%\)\s*$/;

function parsePercentToken(token: string): NoxChartSegment | null {
  const m = PCT_IN_PARENS.exec(token.trim());
  if (!m) return null;
  const value = Number(m[2]);
  if (Number.isNaN(value)) return null;
  return { label: m[1].trim(), value };
}

/** ``US (74.1%), CA (5.1%)`` or ``female (67.1%), male (32.9%)`` */
export function parseNameValuePercents(raw: string): NoxChartSegment[] {
  return raw
    .split(',')
    .map((t) => parsePercentToken(t))
    .filter((x): x is NoxChartSegment => !!x);
}

/** ``播放 75% · 互动 39%`` */
export function parseLabeledPercents(raw: string): NoxChartSegment[] {
  return raw
    .split('·')
    .map((part) => {
      const t = part.trim();
      const m = /^(.+?)\s+([\d.]+)%$/.exec(t);
      if (!m) return null;
      const value = Number(m[2]);
      if (Number.isNaN(value)) return null;
      return { label: m[1].trim(), value };
    })
    .filter((x): x is NoxChartSegment => !!x);
}

/** ``播放 L2 · 互动 L3`` — L1–L5 mapped to 20–100 for bar width */
export function parseLevelSummary(raw: string): NoxChartSegment[] {
  const out: NoxChartSegment[] = [];
  for (const part of raw.split('·')) {
    const m = /^(.+?)\s+(L(\d+))$/i.exec(part.trim());
    if (!m) continue;
    const level = Number(m[3]);
    if (Number.isNaN(level) || level < 1) continue;
    out.push({
      label: m[1].trim(),
      value: Math.min(100, level * 20),
      caption: m[2].toUpperCase(),
    });
  }
  return out;
}

/** ``帖 10 · Reels 5 · 图 5`` */
export function parseCountTokens(raw: string): NoxChartSegment[] {
  return raw
    .split('·')
    .map((part) => {
      const m = /^(.+?)\s+([\d.]+)$/.exec(part.trim());
      if (!m) return null;
      const value = Number(m[2]);
      if (Number.isNaN(value)) return null;
      return { label: m[1].trim(), value };
    })
    .filter((x): x is NoxChartSegment => !!x);
}

/** ``女 13-17 (4.5%), 18-24 (12.1%) · 男 13-17 (2.1%)`` */
export function parseAgeDistribution(raw: string): NoxAgeGroupChart[] {
  return raw
    .split('·')
    .map((chunk) => {
      const t = chunk.trim();
      const m = /^(女|男)\s+(.+)$/.exec(t);
      if (!m) {
        const segments = parseNameValuePercents(t);
        return segments.length ? { group: '受众', segments } : null;
      }
      const segments = parseNameValuePercents(m[2]);
      return segments.length ? { group: m[1], segments } : null;
    })
    .filter((x): x is NoxAgeGroupChart => !!x);
}

export function parseNoxScoreBreakdown(raw: unknown): NoxChartSegment[] {
  let obj: Record<string, unknown> | null = null;
  if (typeof raw === 'string' && raw.trim().startsWith('{')) {
    try {
      obj = JSON.parse(raw) as Record<string, unknown>;
    } catch {
      return [];
    }
  } else if (typeof raw === 'object' && raw !== null && !Array.isArray(raw)) {
    obj = raw as Record<string, unknown>;
  }
  if (!obj) return [];

  const labels: Record<string, string> = {
    overall: '综合',
    growth: '增长',
    creativity: '创意',
    audience: '受众',
    engagement: '互动',
    credibility: '可信',
  };
  const order = ['overall', 'growth', 'creativity', 'audience', 'engagement', 'credibility'];
  const out: NoxChartSegment[] = [];
  for (const key of order) {
    if (!(key in obj)) continue;
    const n = Number(obj[key]);
    if (Number.isNaN(n)) continue;
    const value = n <= 1 ? n * 100 : n;
    out.push({ label: labels[key] ?? key, value });
  }
  return out;
}

export type NoxChartPayload =
  | { kind: 'pie'; segments: NoxChartSegment[]; unit: '%' | '' }
  | { kind: 'bars'; segments: NoxChartSegment[]; unit: '%' | '' }
  | { kind: 'age'; groups: NoxAgeGroupChart[] };

export function buildChartPayload(
  label: string,
  displayValue: string,
  facts: Record<string, unknown>,
  factKey: string,
): NoxChartPayload | null {
  if (!displayValue || displayValue === '—') return null;

  if (label === 'Nox 评分') {
    const raw =
      facts['identity.nox_score_breakdown'] ?? facts['identity.nox_score'];
    const segments = parseNoxScoreBreakdown(raw);
    return segments.length ? { kind: 'bars', segments, unit: '%' } : null;
  }
  if (label === '多维对标排名') {
    const segments = parseLabeledPercents(displayValue);
    return segments.length ? { kind: 'bars', segments, unit: '%' } : null;
  }
  if (label === '表现等级') {
    const segments = parseLevelSummary(displayValue);
    return segments.length ? { kind: 'bars', segments, unit: '%' } : null;
  }
  if (label === '内容形式数量' || label === '分形式互动') {
    const segments = parseCountTokens(displayValue);
    return segments.length ? { kind: 'pie', segments, unit: '' } : null;
  }
  if (label === '年龄分布') {
    const groups = parseAgeDistribution(displayValue);
    return groups.length ? { kind: 'age', groups } : null;
  }

  const segments = parseNameValuePercents(displayValue);
  if (segments.length >= 1) {
    return { kind: 'pie', segments, unit: '%' };
  }

  void factKey;
  return null;
}
