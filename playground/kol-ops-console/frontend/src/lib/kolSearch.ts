/** Fields used for handle / email text filtering on list pages. */
export type KolSearchFields = {
  handle?: string | null;
  email?: string | null;
  identity_id?: number | null;
};

/** Case-insensitive match against handle, email, or numeric identity id. */
export function matchesKolQuery(row: KolSearchFields, query: string): boolean {
  const needle = query.trim().toLowerCase().replace(/^@/, '');
  if (!needle) return true;
  const hay = [
    row.handle ?? '',
    row.email ?? '',
    row.identity_id != null ? String(row.identity_id) : '',
  ]
    .join(' ')
    .toLowerCase();
  return hay.includes(needle);
}

/** Build query string for deep-linking list pages scoped to one KOL. */
export function kolScopedListSearch(params: {
  campaignId: string;
  identityId: number;
  env: 'TEST' | 'LIVE';
  handle?: string | null;
  email?: string | null;
}): string {
  const qs = new URLSearchParams({
    campaign_id: params.campaignId,
    identity_id: String(params.identityId),
    env: params.env,
  });
  const handle = (params.handle ?? '').trim().replace(/^@/, '');
  if (handle) qs.set('q', handle);
  else {
    const email = (params.email ?? '').trim().toLowerCase();
    if (email) qs.set('q', email);
  }
  return qs.toString();
}
