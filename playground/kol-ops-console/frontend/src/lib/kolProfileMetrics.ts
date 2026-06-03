/** Resolve KOL profile display fields from CAL facts (+ optional identity row). */

import { formatAuthenticityDisplay } from './noxValueFormat';

export type KolProfileMetrics = {
  followers: string | null;
  region: string | null;
  language: string | null;
  creatorType: string | null;
  engagementRate: string | null;
  avgViews: string | null;
  audienceAuthenticity: string | null;
  audienceQuality: string | null;
  genderSkew: string | null;
  topInterests: string | null;
  followersSource: string | null;
  followersIsEstimate: boolean;
  hasNoxDiligence: boolean;
};

function pickString(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const t = value.trim();
  return t || null;
}

export function formatMetric(value: unknown): string | null {
  if (value == null || value === '') return null;
  if (typeof value === 'number' && !Number.isNaN(value)) {
    if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
    if (value >= 10_000) return `${(value / 1_000).toFixed(1)}K`;
    return value.toLocaleString();
  }
  if (typeof value === 'string') {
    const t = value.trim();
    return t || null;
  }
  return String(value);
}

/** Format 0.0302 → 3.0% */
export function formatPercent(value: unknown): string | null {
  if (value == null || value === '') return null;
  if (typeof value === 'number' && !Number.isNaN(value)) {
    const pct = value <= 1 ? value * 100 : value;
    return `${pct.toFixed(1)}%`;
  }
  const s = pickString(value);
  if (!s) return null;
  const n = Number(s);
  if (!Number.isNaN(n)) {
    const pct = n <= 1 ? n * 100 : n;
    return `${pct.toFixed(1)}%`;
  }
  return s;
}

export function pickKolProfileMetrics(
  facts: Record<string, unknown>,
  identity?: {
    region?: string | null;
    language?: string | null;
    creator_type?: string | null;
  },
): KolProfileMetrics {
  const hasNoxDiligence = Boolean(
    pickString(facts['identity.nox_diligence_verdict']),
  );

  const followersRaw =
    facts['identity.followers']
    ?? facts['identity.nox_followers']
    ?? facts['identity.follower_count']
    ?? facts['identity.fans_count'];
  const followers = formatMetric(followersRaw);
  const followersSource =
    typeof facts['identity.nox_followers_source'] === 'string'
      ? facts['identity.nox_followers_source']
      : null;

  const region =
    pickString(facts['identity.region'])
    ?? pickString(identity?.region)
    ?? pickString(facts['identity.nox_top_region'])
    ?? pickString(facts['identity.nox_country'])
    ?? pickString(facts['identity.country']);

  const language =
    pickString(facts['identity.language']) ?? pickString(identity?.language);

  const creatorType =
    pickString(identity?.creator_type)
    ?? pickString(facts['identity.creator_type'])
    ?? pickString(facts['identity.kol_type']);

  const engagementRate =
    formatPercent(facts['identity.nox_engagement_rate'])
    ?? formatPercent(facts['identity.engagement_rate']);

  const avgViews = formatMetric(facts['identity.nox_avg_views']);

  const audienceAuthenticity = formatAuthenticityDisplay(
    facts['identity.nox_audience_authenticity'],
  );

  const audienceQuality =
    formatMetric(facts['identity.nox_audience_quality_score'])
    ?? formatAuthenticityDisplay(facts['identity.nox_audience_quality']);

  const genderSkew = pickString(facts['identity.nox_gender_skew']);

  const topInterests = pickString(facts['identity.nox_audience_interests_top']);

  return {
    followers,
    region,
    language,
    creatorType,
    engagementRate,
    avgViews,
    audienceAuthenticity,
    audienceQuality,
    genderSkew,
    topInterests,
    followersSource,
    followersIsEstimate: followersSource === 'inferred_views_ratio',
    hasNoxDiligence,
  };
}
