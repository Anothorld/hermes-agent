/** Operator-facing snapshot for profile hover cards (no iframe). */

export type LinkPreviewPayload = {
  ok?: boolean;
  title?: string | null;
  description?: string | null;
  image?: string | null;
  cached?: boolean;
  source?: string;
  reason?: string;
};

export type KolProfilePreviewSnapshot = {
  handle?: string | null;
  displayName?: string | null;
  platform?: string | null;
  followers?: string | null;
  followersNote?: string | null;
  noxVerdict?: string | null;
  description?: string | null;
};

function formatFollowers(raw: unknown): string | null {
  if (raw === null || raw === undefined || raw === '') return null;
  const n = typeof raw === 'number' ? raw : Number(String(raw).replace(/,/g, ''));
  if (!Number.isFinite(n)) return String(raw);
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1).replace(/\.0$/, '')}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1).replace(/\.0$/, '')}K`;
  return String(Math.round(n));
}

/** Build a snapshot from CAL facts + identity row (detail / shortlist). */
export function buildKolProfileSnapshot(
  facts: Record<string, unknown> | null | undefined,
  options?: {
    handle?: string | null;
    displayName?: string | null;
    platform?: string | null;
    noxVerdict?: string | null;
  },
): KolProfilePreviewSnapshot {
  const f = facts ?? {};
  const followersRaw =
    f['identity.followers']
    ?? f['identity.nox_followers']
    ?? f['identity.follower_count'];
  const followersSource =
    typeof f['identity.nox_followers_source'] === 'string'
      ? f['identity.nox_followers_source']
      : null;
  const displayName =
    options?.displayName
    ?? (typeof f['identity.nox_creator_name'] === 'string'
      ? f['identity.nox_creator_name']
      : null);

  const ogTitle =
    typeof f['identity.profile_og_title'] === 'string'
      ? f['identity.profile_og_title']
      : null;
  const ogDesc =
    typeof f['identity.profile_og_description'] === 'string'
      ? f['identity.profile_og_description']
      : null;

  return {
    handle: options?.handle ?? null,
    displayName: displayName ?? ogTitle,
    platform: options?.platform ?? null,
    followers: formatFollowers(followersRaw),
    followersNote:
      followersSource === 'inferred_views_ratio' ? '粉丝数为估算' : null,
    noxVerdict:
      options?.noxVerdict
      ?? (typeof f['identity.nox_diligence_verdict'] === 'string'
        ? f['identity.nox_diligence_verdict']
        : null),
    description: ogDesc,
  };
}

function normalizeProfileUrl(url: string): string {
  try {
    const u = new URL(url.trim());
    const path = u.pathname.replace(/\/$/, '') || '';
    return `${u.protocol}//${u.hostname.toLowerCase()}${path}`;
  } catch {
    return url.trim().toLowerCase().replace(/\/$/, '');
  }
}

/** Read CAL-cached OG (same rules as Console backend). */
export function linkPreviewFromFacts(
  facts: Record<string, unknown> | null | undefined,
  profileUrl: string,
): LinkPreviewPayload | null {
  const f = facts ?? {};
  const cachedUrl = f['identity.profile_og_source_url'];
  const fetchedAt = f['identity.profile_og_fetched_at'];
  if (typeof cachedUrl !== 'string' || typeof fetchedAt !== 'string') return null;
  if (normalizeProfileUrl(cachedUrl) !== normalizeProfileUrl(profileUrl)) return null;
  const fetchedMs = Date.parse(
    fetchedAt.endsWith('Z') ? fetchedAt : fetchedAt,
  );
  if (!Number.isFinite(fetchedMs)) return null;
  const ageDays = (Date.now() - fetchedMs) / 86_400_000;
  if (ageDays > 7) return null;

  const image = f['identity.profile_og_image_url'];
  const title = f['identity.profile_og_title'];
  const description = f['identity.profile_og_description'];
  if (
    !(typeof image === 'string' && image.trim())
    && !(typeof title === 'string' && title.trim())
  ) {
    return null;
  }
  return {
    ok: true,
    url: profileUrl,
    title: typeof title === 'string' ? title : null,
    description: typeof description === 'string' ? description : null,
    image: typeof image === 'string' ? image : null,
    cached: true,
    source: 'cal_cache',
  };
}
