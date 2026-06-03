/** Operator-facing labels for Nox diligence facts and internal cache keys. */

export type ParsedNoxCacheKey = {
  kind: string;
  kindLabel: string;
  noxCreatorId: string | null;
  dimensions: string[];
  dimensionLabels: string[];
  lang: string | null;
  langLabel: string | null;
};

const CACHE_KIND_LABELS: Record<string, string> = {
  diligence_pack: '达人尽调包',
  contacts: '联系方式查询',
  creator_search: '达人搜索',
  monitor_setup: '视频监测',
};

const DIMENSION_LABELS: Record<string, string> = {
  profile: '资料档案',
  audience: '受众画像',
  content: '内容表现',
  cooperation: '合作记录',
};

const LANG_LABELS: Record<string, string> = {
  en: '英文',
  zh: '中文',
  'zh-cn': '简体中文',
  'zh-tw': '繁体中文',
};

const VERDICT_LABELS: Record<string, { label: string; hint: string; tone: string }> = {
  high_priority: {
    label: '优先合作',
    hint: '数据表现较好，可优先考虑推进合作',
    tone: 'bg-emerald-100 text-emerald-900 border-emerald-200',
  },
  viable_with_risks: {
    label: '可行（有风险）',
    hint: '可以合作，但需关注受众或表现方面的风险点',
    tone: 'bg-amber-100 text-amber-900 border-amber-200',
  },
  needs_manual_review: {
    label: '需人工复核',
    hint: '自动结论不确定，建议结合主页与历史合作再判断',
    tone: 'bg-sky-100 text-sky-900 border-sky-200',
  },
  not_priority: {
    label: '不建议优先',
    hint: '存在争议记录或明显风险信号，通常不作为优先人选',
    tone: 'bg-slate-100 text-slate-800 border-slate-200',
  },
};

export function formatNoxDiligenceVerdict(verdict: string): {
  label: string;
  hint: string;
  tone: string;
  raw: string;
} {
  const v = verdict.trim();
  const mapped = VERDICT_LABELS[v];
  if (mapped) {
    return { ...mapped, raw: v };
  }
  return {
    label: v.replace(/_/g, ' '),
    hint: 'Nox 自动尽调结论',
    tone: 'bg-violet-100 text-violet-900 border-violet-200',
    raw: v,
  };
}

export function parseNoxCacheKey(raw: string): ParsedNoxCacheKey | null {
  const key = raw.trim();
  if (!key) return null;
  const parts = key.split('|');
  if (parts.length < 2) return null;

  const kind = parts[0];
  const kindLabel = CACHE_KIND_LABELS[kind] ?? kind;

  if (kind === 'diligence_pack' && parts.length >= 4) {
    const dims = parts[2]
      .split(',')
      .map((d) => d.trim())
      .filter(Boolean);
    const lang = parts[3] || null;
    return {
      kind,
      kindLabel,
      noxCreatorId: parts[1] || null,
      dimensions: dims,
      dimensionLabels: dims.map((d) => DIMENSION_LABELS[d] ?? d),
      lang,
      langLabel: lang ? (LANG_LABELS[lang.toLowerCase()] ?? lang) : null,
    };
  }

  if (kind === 'contacts' && parts.length >= 3) {
    const lang = parts[2] || null;
    return {
      kind,
      kindLabel,
      noxCreatorId: parts[1] || null,
      dimensions: [],
      dimensionLabels: [],
      lang,
      langLabel: lang ? (LANG_LABELS[lang.toLowerCase()] ?? lang) : null,
    };
  }

  return {
    kind,
    kindLabel,
    noxCreatorId: parts[1] || null,
    dimensions: [],
    dimensionLabels: [],
    lang: null,
    langLabel: null,
  };
}

export function truncateNoxId(id: string, head = 10, tail = 6): string {
  if (id.length <= head + tail + 3) return id;
  return `${id.slice(0, head)}…${id.slice(-tail)}`;
}
