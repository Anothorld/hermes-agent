import { KolProfilePreviewLink } from './KolProfilePreviewLink';
import { buildKolProfileSnapshot, type LinkPreviewPayload } from '../lib/kolProfileSnapshot';
import {
  mergeSocialLinksForCandidate,
  type SocialLinkItem,
} from '../lib/socialLinks';

export type { SocialPlatformKey, SocialLinkItem } from '../lib/socialLinks';
export { SOCIAL_LINKS, readSocialUrl, listSocialLinkItems } from '../lib/socialLinks';

function previewForUrl(
  url: string,
  linkPreviewsByUrl: Record<string, LinkPreviewPayload> | null | undefined,
  primaryUrl: string | null | undefined,
  primaryPreview: LinkPreviewPayload | null | undefined,
): LinkPreviewPayload | undefined {
  const fromMap = linkPreviewsByUrl?.[url];
  if (fromMap) return fromMap;
  if (primaryUrl && url === primaryUrl && primaryPreview) return primaryPreview;
  return undefined;
}

function renderSocialChips({
  items,
  facts,
  identityId,
  env,
  snapshot,
  linkPreviewsByUrl,
  primaryUrl,
  primaryLinkPreview,
  stopPropagation,
}: {
  items: SocialLinkItem[];
  facts: Record<string, unknown>;
  identityId: number | null | undefined;
  env: string;
  snapshot: ReturnType<typeof buildKolProfileSnapshot>;
  linkPreviewsByUrl?: Record<string, LinkPreviewPayload> | null;
  primaryUrl?: string | null;
  primaryLinkPreview?: LinkPreviewPayload | null;
  stopPropagation?: boolean;
}) {
  return items.map((item) => (
    <KolProfilePreviewLink
      key={`${item.key}-${item.url}`}
      url={item.url}
      label={item.shortLabel}
      variant="chip"
      previewFacts={facts}
      identityId={identityId ?? undefined}
      env={env}
      stopPropagation={stopPropagation}
      initialLinkPreview={previewForUrl(
        item.url,
        linkPreviewsByUrl,
        primaryUrl,
        primaryLinkPreview,
      )}
      snapshot={{ ...snapshot, platform: item.label }}
    />
  ));
}

/** Compact platform chips with hover profile preview (detail / shortlist). */
export function KolSocialQuickLinks({
  facts,
  identityId,
  env,
  className = '',
  platform,
  handle,
  profileUrl,
  linkPreviewsByUrl,
  primaryLinkPreview,
  stopPropagation,
  socialLinks,
}: {
  facts: Record<string, unknown>;
  identityId?: number | null;
  env: string;
  className?: string;
  platform?: string | null;
  handle?: string | null;
  profileUrl?: string | null;
  linkPreviewsByUrl?: Record<string, LinkPreviewPayload> | null;
  primaryLinkPreview?: LinkPreviewPayload | null;
  stopPropagation?: boolean;
  socialLinks?: SocialLinkItem[] | null;
}) {
  const snapshot = buildKolProfileSnapshot(facts, { handle, platform });
  const items = mergeSocialLinksForCandidate(facts, {
    platform,
    handle,
    profileUrl,
    socialLinksFromApi: socialLinks,
  });
  if (items.length === 0) return null;

  const primary = profileUrl ?? items[0]?.url ?? null;

  return (
    <div className={`flex flex-wrap items-center gap-1.5 ${className}`.trim()}>
      <span className="text-[10px] font-medium uppercase tracking-wide text-slate-500">
        快速跳转
      </span>
      {renderSocialChips({
        items,
        facts,
        identityId,
        env,
        snapshot,
        linkPreviewsByUrl,
        primaryUrl: primary,
        primaryLinkPreview,
        stopPropagation,
      })}
    </div>
  );
}
