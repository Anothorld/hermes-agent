/** Fact keys for known social profile URLs (platform-specific first). */
export const KOL_PROFILE_URL_FACT_KEYS = [
  'identity.instagram_profile_url',
  'identity.tiktok_profile_url',
  'identity.youtube_profile_url',
  'identity.facebook_profile_url',
  'identity.twitter_profile_url',
  'identity.threads_profile_url',
  'identity.linktree_url',
  'identity.personal_site_url',
] as const;

const PLATFORM_FACT_KEY: Record<string, string> = {
  instagram: 'identity.instagram_profile_url',
  tiktok: 'identity.tiktok_profile_url',
  youtube: 'identity.youtube_profile_url',
  facebook: 'identity.facebook_profile_url',
  twitter: 'identity.twitter_profile_url',
  x: 'identity.twitter_profile_url',
  threads: 'identity.threads_profile_url',
};

function normalizeHttpUrl(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const trimmed = value.trim();
  if (!/^https?:\/\//i.test(trimmed)) return null;
  return trimmed;
}

/** Best-effort profile URL from CAL facts, then platform + handle. */
export function resolveKolProfileUrl(
  facts: Record<string, unknown> | null | undefined,
  options?: { platform?: string | null; handle?: string | null },
): string | null {
  const platform = options?.platform?.trim().toLowerCase() || null;
  const factsMap = facts ?? {};

  if (platform) {
    const key = PLATFORM_FACT_KEY[platform];
    if (key) {
      const fromPlatform = normalizeHttpUrl(factsMap[key]);
      if (fromPlatform) return fromPlatform;
    }
  }

  for (const key of KOL_PROFILE_URL_FACT_KEYS) {
    const url = normalizeHttpUrl(factsMap[key]);
    if (url) return url;
  }

  return guessKolProfileUrl(options?.platform, options?.handle);
}

/** Construct a public profile URL when facts are missing (discovery default). */
export function guessKolProfileUrl(
  platform: string | null | undefined,
  handle: string | null | undefined,
): string | null {
  const h = (handle ?? '').trim().replace(/^@+/, '');
  if (!h) return null;
  const p = (platform ?? 'instagram').trim().toLowerCase();
  switch (p) {
    case 'tiktok':
      return `https://www.tiktok.com/@${h}`;
    case 'youtube':
      return `https://www.youtube.com/@${h}`;
    case 'twitter':
    case 'x':
      return `https://x.com/${h}`;
    case 'facebook':
      return `https://www.facebook.com/${h}`;
    case 'threads':
      return `https://www.threads.net/@${h}`;
    case 'instagram':
    case 'blog':
    default:
      return `https://www.instagram.com/${h}/`;
  }
}
