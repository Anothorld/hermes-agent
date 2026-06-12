import { useCallback, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useEnvStore } from '../lib/store';

function parseIdentityId(raw: string | null): number | null {
  if (!raw) return null;
  const n = Number.parseInt(raw, 10);
  return Number.isFinite(n) && n >= 1 ? n : null;
}

/** Deep-link scope + text query for KOL-scoped operator list pages. */
export function useKolListScope() {
  const [searchParams, setSearchParams] = useSearchParams();
  const storeEnv = useEnvStore((s) => s.env);
  const env = (
    searchParams.get('env') === 'LIVE'
      ? 'LIVE'
      : searchParams.get('env') === 'TEST'
        ? 'TEST'
        : storeEnv
  ) as 'TEST' | 'LIVE';

  const scopedIdentityId = parseIdentityId(searchParams.get('identity_id'));
  const scopedCampaignId = searchParams.get('campaign_id')?.trim() || null;
  const kolQuery = searchParams.get('q') ?? '';
  const hasKolScope = scopedIdentityId != null;

  const setKolQuery = useCallback(
    (next: string) => {
      setSearchParams(
        (prev) => {
          const p = new URLSearchParams(prev);
          const trimmed = next.trim();
          if (trimmed) p.set('q', trimmed);
          else p.delete('q');
          return p;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  const clearKolScope = useCallback(() => {
    setSearchParams(
      (prev) => {
        const p = new URLSearchParams(prev);
        p.delete('identity_id');
        p.delete('campaign_id');
        p.delete('q');
        return p;
      },
      { replace: true },
    );
  }, [setSearchParams]);

  const scopeQueryParams = useMemo(() => {
    const params = new URLSearchParams();
    if (scopedIdentityId != null) {
      params.set('identity_id', String(scopedIdentityId));
    }
    if (scopedCampaignId) params.set('campaign_id', scopedCampaignId);
    return params;
  }, [scopedIdentityId, scopedCampaignId]);

  return {
    env,
    scopedIdentityId,
    scopedCampaignId,
    kolQuery,
    setKolQuery,
    clearKolScope,
    hasKolScope,
    scopeQueryParams,
  };
}
