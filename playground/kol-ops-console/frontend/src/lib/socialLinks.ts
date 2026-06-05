import { guessKolProfileUrl, resolveKolProfileUrl } from './kolProfileUrl';

export type SocialPlatformKey =
  | 'identity.instagram_profile_url'
  | 'identity.tiktok_profile_url'
  | 'identity.youtube_profile_url'
  | 'identity.facebook_profile_url'
  | 'identity.twitter_profile_url'
  | 'identity.threads_profile_url'
  | 'identity.linktree_url'
  | 'identity.personal_site_url';

export type SocialLinkItem = {
  key: SocialPlatformKey | 'inferred';
  label: string;
  shortLabel: string;
  url: string;
};

export const SOCIAL_LINKS: ReadonlyArray<{
  key: SocialPlatformKey;
  label: string;
  shortLabel: string;
}> = [
  { key: 'identity.instagram_profile_url', label: 'Instagram', shortLabel: 'IG' },
  { key: 'identity.tiktok_profile_url', label: 'TikTok', shortLabel: 'TikTok' },
  { key: 'identity.youtube_profile_url', label: 'YouTube', shortLabel: 'YT' },
  { key: 'identity.facebook_profile_url', label: 'Facebook', shortLabel: 'FB' },
  { key: 'identity.twitter_profile_url', label: 'X', shortLabel: 'X' },
  { key: 'identity.threads_profile_url', label: 'Threads', shortLabel: 'Threads' },
  { key: 'identity.linktree_url', label: 'Link-in-bio', shortLabel: 'bio' },
  { key: 'identity.personal_site_url', label: '个人站', shortLabel: 'site' },
];

const PLATFORM_SHORT: Record<string, string> = {
  instagram: 'IG',
  tiktok: 'TikTok',
  youtube: 'YT',
  facebook: 'FB',
  twitter: 'X',
  x: 'X',
  threads: 'Threads',
  blog: 'IG',
};

export function readSocialUrl(
  facts: Record<string, unknown>,
  key: SocialPlatformKey,
): string | null {
  const v = facts[key];
  if (typeof v !== 'string') return null;
  const trimmed = v.trim();
  if (!trimmed || !/^https?:\/\//i.test(trimmed)) return null;
  return trimmed;
}

export function listSocialLinkItems(
  facts: Record<string, unknown>,
): SocialLinkItem[] {
  return SOCIAL_LINKS.map(({ key, label, shortLabel }) => {
    const url = readSocialUrl(facts, key);
    return url ? { key, label, shortLabel, url } : null;
  }).filter((x): x is SocialLinkItem => !!x);
}

/** All known profile URLs (CAL facts, else inferred primary). */
export function listSocialLinksForCandidate(
  previewFacts: Record<string, unknown> | null | undefined,
  options?: {
    platform?: string | null;
    handle?: string | null;
    profileUrl?: string | null;
  },
): SocialLinkItem[] {
  const facts = previewFacts ?? {};
  const fromFacts = listSocialLinkItems(facts);
  if (fromFacts.length > 0) return fromFacts;

  const url =
    (typeof options?.profileUrl === 'string' && /^https?:\/\//i.test(options.profileUrl.trim())
      ? options.profileUrl.trim()
      : null)
    ?? resolveKolProfileUrl(facts, {
      platform: options?.platform,
      handle: options?.handle,
    })
    ?? guessKolProfileUrl(options?.platform, options?.handle);
  if (!url) return [];

  const plat = (options?.platform ?? 'instagram').trim().toLowerCase();
  const spec = SOCIAL_LINKS.find((s) => {
    const platKey = plat === 'x' ? 'twitter' : plat;
    return s.key === `identity.${platKey}_profile_url`;
  });
  return [{
    key: 'inferred',
    label: spec?.label ?? (plat ? plat.charAt(0).toUpperCase() + plat.slice(1) : 'Profile'),
    shortLabel: PLATFORM_SHORT[plat] ?? spec?.shortLabel ?? '主页',
    url,
  }];
}

/** Merge CAL facts + API rows (detail-page order; dedupe by URL). */
export function mergeSocialLinksForCandidate(
  previewFacts: Record<string, unknown> | null | undefined,
  options?: {
    platform?: string | null;
    handle?: string | null;
    profileUrl?: string | null;
    socialLinksFromApi?: Array<{
      key?: string;
      label?: string;
      short_label?: string;
      url?: string;
    }> | null;
  },
): SocialLinkItem[] {
  const fromFacts = listSocialLinksForCandidate(previewFacts, {
    platform: options?.platform,
    handle: options?.handle,
    profileUrl: options?.profileUrl,
  });
  const fromApi = socialLinksFromApi(options?.socialLinksFromApi);
  const seen = new Set<string>();
  const out: SocialLinkItem[] = [];
  for (const item of [...fromFacts, ...fromApi]) {
    const norm = item.url.trim().toLowerCase().replace(/\/$/, '');
    if (seen.has(norm)) continue;
    seen.add(norm);
    out.push(item);
  }
  return out;
}

/** Map shortlist API `social_links` rows to SocialLinkItem. */
export function socialLinksFromApi(
  rows: Array<{ key?: string; label?: string; short_label?: string; url?: string }> | null | undefined,
): SocialLinkItem[] {
  if (!rows?.length) return [];
  const out: SocialLinkItem[] = [];
  for (const row of rows) {
    const url = typeof row.url === 'string' ? row.url.trim() : '';
    if (!url || !/^https?:\/\//i.test(url)) continue;
    out.push({
      key: (row.key as SocialLinkItem['key']) || 'inferred',
      label: row.label ?? 'Profile',
      shortLabel: row.short_label ?? row.label ?? '主页',
      url,
    });
  }
  return out;
}
