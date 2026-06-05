/** Categorized Nox fact keys for the KOL detail dashboard. */

import { formatNoxField } from './noxValueFormat';

export type NoxDashboardItem = {
  label: string;
  value: string;
  factKey: string;
};

export type NoxDashboardCategory = {
  id: string;
  title: string;
  description: string;
  items: NoxDashboardItem[];
};

type FactDef = {
  keys: string[];
  label: string;
  format?: 'metric' | 'percent' | 'text' | 'nox_score';
};

const FACT_DEFS: FactDef[] = [
  { keys: ['identity.nox_creator_name', 'identity.display_name'], label: '达人名称', format: 'text' },
  { keys: ['identity.nox_creator_id'], label: 'Nox ID', format: 'text' },
  { keys: ['identity.nox_platform'], label: '平台', format: 'text' },
  {
    keys: ['identity.followers', 'identity.nox_followers', 'identity.follower_count'],
    label: '粉丝数',
    format: 'metric',
  },
  { keys: ['identity.nox_followers_source'], label: '粉丝数来源', format: 'text' },
  { keys: ['identity.nox_channel_url'], label: '频道链接', format: 'text' },
  { keys: ['identity.nox_channel_handle'], label: '频道账号', format: 'text' },
  {
    keys: ['identity.nox_score_breakdown', 'identity.nox_score'],
    label: 'Nox 评分',
    format: 'nox_score',
  },
  { keys: ['identity.nox_benchmark_rank'], label: '粉播比排名', format: 'percent' },
  { keys: ['identity.nox_benchmark_ranks'], label: '多维对标排名', format: 'text' },
  { keys: ['identity.nox_performance_levels'], label: '表现等级', format: 'text' },
  { keys: ['identity.nox_median_views'], label: '中位播放', format: 'metric' },
  { keys: ['identity.nox_wave'], label: '播放波动', format: 'metric' },
  { keys: ['identity.nox_avg_active_days'], label: '周活跃天数', format: 'metric' },
  { keys: ['identity.nox_view_per_followers'], label: '粉播比', format: 'metric' },
  { keys: ['identity.nox_top_region', 'identity.region', 'identity.nox_country'], label: '主要受众地区', format: 'text' },
  { keys: ['identity.nox_gender_skew'], label: '性别分布', format: 'text' },
  { keys: ['identity.nox_audience_age_distribution'], label: '年龄分布', format: 'text' },
  { keys: ['identity.nox_audience_adults_split'], label: '成人/儿童占比', format: 'text' },
  { keys: ['identity.nox_audience_languages_top'], label: '受众语言', format: 'text' },
  { keys: ['identity.nox_audience_types_top'], label: '受众类型', format: 'text' },
  { keys: ['identity.nox_audience_authenticity'], label: '受众真实度', format: 'percent' },
  { keys: ['identity.nox_audience_authenticity_range'], label: '真实度区间', format: 'text' },
  { keys: ['identity.nox_audience_quality_score'], label: '受众质量分', format: 'metric' },
  { keys: ['identity.nox_audience_positive_pct'], label: '正面受众占比', format: 'percent' },
  { keys: ['identity.nox_audience_promo_attractiveness'], label: '推广吸引力', format: 'metric' },
  { keys: ['identity.nox_audience_promo_interested_pct'], label: '推广兴趣受众', format: 'percent' },
  { keys: ['identity.nox_audience_promo_professionalism'], label: '推广专业度', format: 'metric' },
  { keys: ['identity.nox_audience_interests_top'], label: '受众兴趣', format: 'text' },
  { keys: ['identity.nox_engagement_rate'], label: '互动率', format: 'percent' },
  { keys: ['identity.nox_avg_views'], label: '平均播放', format: 'metric' },
  { keys: ['identity.nox_content_tags_top'], label: '内容标签 Top', format: 'text' },
  { keys: ['identity.nox_content_tags_all'], label: '全部内容标签', format: 'text' },
  { keys: ['identity.nox_content_format_counts'], label: '内容形式数量', format: 'text' },
  { keys: ['identity.nox_content_engagement_split'], label: '分形式互动', format: 'text' },
  { keys: ['identity.nox_cooperation_score'], label: '合作评分', format: 'metric' },
  { keys: ['identity.nox_cooperation_price_estimate'], label: '估价区间', format: 'text' },
  { keys: ['identity.nox_cooperation_first_price_range'], label: '首次报价', format: 'text' },
  { keys: ['identity.nox_cooperation_final_price_range'], label: '最终成交价', format: 'text' },
  { keys: ['identity.nox_cooperation_avg_response_hours'], label: '平均响应 (小时)', format: 'metric' },
  { keys: ['identity.nox_cooperation_contact_efficiency'], label: '联系效率', format: 'text' },
  { keys: ['identity.nox_cooperation_ad_video_stats'], label: '广告视频表现', format: 'text' },
  { keys: ['identity.nox_cooperation_brands_top'], label: '合作品牌', format: 'text' },
  { keys: ['identity.nox_cooperation_confirmation_pct'], label: '合作确认率', format: 'percent' },
  { keys: ['identity.nox_cooperation_start_contact_pct'], label: '发起联系率', format: 'percent' },
  { keys: ['identity.nox_cooperation_promotion_online_pct'], label: '推广上线率', format: 'percent' },
  { keys: ['identity.nox_cooperation_active_period'], label: '活跃时段 (UTC)', format: 'text' },
  {
    keys: ['identity.nox_cooperation_brand_video_engagement_rate'],
    label: '品牌视频互动率',
    format: 'percent',
  },
  { keys: ['identity.nox_cooperation_pros'], label: '合作优点', format: 'text' },
  { keys: ['identity.nox_cooperation_cons'], label: '合作风险点', format: 'text' },
  { keys: ['identity.nox_dispute_types'], label: '争议类型', format: 'text' },
  { keys: ['identity.nox_dispute_count'], label: '争议记录数', format: 'metric' },
  { keys: ['identity.nox_email_quality'], label: '邮箱质量', format: 'text' },
  { keys: ['identity.email'], label: 'Nox 邮箱', format: 'text' },
  { keys: ['identity.nox_diligence_verdict'], label: '尽调结论', format: 'text' },
  { keys: ['identity.nox_diligence_at'], label: '尽调时间', format: 'text' },
  { keys: ['identity.nox_cache_month'], label: '数据月份', format: 'text' },
  { keys: ['identity.nox_cache_hit'], label: '命中缓存', format: 'text' },
  { keys: ['identity.nox_api_calls_last'], label: '上次 API 消耗', format: 'metric' },
  { keys: ['identity.nox_diligence_dimensions'], label: '尽调维度', format: 'text' },
  { keys: ['identity.nox_diligence_lang'], label: '报告语言', format: 'text' },
];

const CATEGORY_DEFS: Array<{
  id: string;
  title: string;
  description: string;
  labels: string[];
}> = [
  {
    id: 'profile',
    title: '达人档案',
    description: '基础身份与频道信息',
    labels: [
      '达人名称',
      'Nox ID',
      '平台',
      '粉丝数',
      '粉丝数来源',
      '频道链接',
      '频道账号',
      'Nox 评分',
      '粉播比排名',
      '多维对标排名',
      '表现等级',
      '中位播放',
      '播放波动',
      '周活跃天数',
      '粉播比',
    ],
  },
  {
    id: 'audience',
    title: '受众画像',
    description: '地区、性别、真实度与兴趣',
    labels: [
      '主要受众地区',
      '性别分布',
      '年龄分布',
      '成人/儿童占比',
      '受众语言',
      '受众类型',
      '受众真实度',
      '真实度区间',
      '受众质量分',
      '正面受众占比',
      '推广吸引力',
      '推广兴趣受众',
      '推广专业度',
      '受众兴趣',
    ],
  },
  {
    id: 'content',
    title: '内容表现',
    description: '互动与内容标签',
    labels: [
      '互动率',
      '平均播放',
      '内容标签 Top',
      '全部内容标签',
      '内容形式数量',
      '分形式互动',
    ],
  },
  {
    id: 'cooperation',
    title: '合作与商业',
    description: '估价、响应、品牌合作史与争议信号',
    labels: [
      '合作评分',
      '估价区间',
      '首次报价',
      '最终成交价',
      '平均响应 (小时)',
      '联系效率',
      '广告视频表现',
      '合作品牌',
      '合作确认率',
      '发起联系率',
      '推广上线率',
      '活跃时段 (UTC)',
      '品牌视频互动率',
      '合作优点',
      '合作风险点',
      '争议类型',
      '争议记录数',
    ],
  },
  {
    id: 'contacts',
    title: '联系方式',
    description: 'Gate B 邮箱查询',
    labels: ['Nox 邮箱', '邮箱质量'],
  },
  {
    id: 'meta',
    title: '尽调记录',
    description: '结论、缓存与 API 用量',
    labels: [
      '尽调结论',
      '尽调时间',
      '数据月份',
      '命中缓存',
      '上次 API 消耗',
      '尽调维度',
      '报告语言',
    ],
  },
];

function formatFactValue(value: unknown, format: FactDef['format']): string | null {
  return formatNoxField(value, format ?? 'text');
}

function pickFact(facts: Record<string, unknown>, def: FactDef): NoxDashboardItem | null {
  for (const key of def.keys) {
    const raw = facts[key];
    const formatted = formatFactValue(raw, def.format);
    if (!formatted) continue;
    let display = formatted;
    if (def.label === '尽调结论') {
      const v = String(raw);
      const map: Record<string, string> = {
        high_priority: '优先合作',
        viable_with_risks: '可行（有风险）',
        needs_manual_review: '需人工复核',
        not_priority: '不建议优先',
      };
      display = map[v] ?? formatted;
    }
    if (def.label === '命中缓存') {
      display = raw === true || raw === 'true' ? '是（未扣费）' : '否';
    }
    if (def.label === '粉丝数来源') {
      const map: Record<string, string> = {
        nox_api: 'Nox API 直出',
        inferred_views_ratio: '由播放/粉丝比推算',
        cal_existing: '沿用 CAL 已有记录',
        nox_social_media: 'Nox 社交子账号',
      };
      display = map[String(raw)] ?? formatted ?? String(raw);
    }
    return { label: def.label, value: display, factKey: key };
  }
  return null;
}

export function buildNoxDashboardCategories(
  facts: Record<string, unknown>,
): NoxDashboardCategory[] {
  const byLabel = new Map<string, NoxDashboardItem>();
  for (const def of FACT_DEFS) {
    const item = pickFact(facts, def);
    if (item) byLabel.set(def.label, item);
  }

  const out: NoxDashboardCategory[] = [];
  for (const cat of CATEGORY_DEFS) {
    const items = cat.labels
      .map((label) => byLabel.get(label) ?? null)
      .filter((x): x is NoxDashboardItem => !!x);
    if (items.length) {
      out.push({
        id: cat.id,
        title: cat.title,
        description: cat.description,
        items,
      });
    }
  }
  return out;
}

export function hasAnyNoxFacts(facts: Record<string, unknown>): boolean {
  return Object.keys(facts).some(
    (k) => k.startsWith('identity.nox_') || k === 'identity.email_source',
  );
}

const INSIGHTS_CATEGORY_ORDER = [
  'profile',
  'content',
  'audience',
  'cooperation',
  'contacts',
  'meta',
] as const;

/** All Nox categories for the unified diligence panel (verdict shown separately). */
export function buildNoxInsightsCategories(
  facts: Record<string, unknown>,
): NoxDashboardCategory[] {
  const byId = new Map(
    buildNoxDashboardCategories(facts).map((cat) => [cat.id, cat]),
  );
  return INSIGHTS_CATEGORY_ORDER.map((id) => byId.get(id))
    .filter((cat): cat is NoxDashboardCategory => !!cat)
    .map((cat) => ({
      ...cat,
      items:
        cat.id === 'meta'
          ? cat.items.filter((i) => i.label !== '尽调结论')
          : cat.items,
    }))
    .filter((cat) => cat.items.length > 0);
}
