import { useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useCampaignStore } from './store';

/**
 * Bind global campaign picker to ``?campaign_id=`` on campaign-scoped pages.
 *
 * - Deep link / back-forward: URL → store (only when the query changes)
 * - Picker change: store → URL (read store after URL sync in the same tick)
 * - Data loading should use the returned id (store wins; URL is bootstrap)
 */
export function useCampaignQuerySync(): string {
  const [search, setSearch] = useSearchParams();
  const campaignId = useCampaignStore((s) => s.currentCampaignId);
  const setCampaignId = useCampaignStore((s) => s.setCampaignId);
  const urlCampaignId = search.get('campaign_id') ?? '';

  // External navigation (link, back button): adopt URL when param present.
  useEffect(() => {
    const fromUrl = search.get('campaign_id');
    if (fromUrl == null || fromUrl === '') return;
    setCampaignId(fromUrl);
  }, [search, setCampaignId]);

  // Picker / persisted store: reflect into URL so refresh and share work.
  useEffect(() => {
    const fromUrl = search.get('campaign_id') ?? '';
    const id = useCampaignStore.getState().currentCampaignId;
    if (id && fromUrl !== id) {
      setSearch(
        (prev) => {
          const next = new URLSearchParams(prev);
          next.set('campaign_id', id);
          return next;
        },
        { replace: true },
      );
      return;
    }
    if (!id && fromUrl) {
      setSearch(
        (prev) => {
          const next = new URLSearchParams(prev);
          next.delete('campaign_id');
          return next;
        },
        { replace: true },
      );
    }
  }, [campaignId, search, setSearch]);

  return campaignId || urlCampaignId;
}
