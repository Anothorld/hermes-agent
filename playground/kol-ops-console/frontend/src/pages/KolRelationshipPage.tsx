import { useCallback, useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api } from '../api';

type Relationship = {
  identity_id: number;
  collab_history: Array<{
    campaign_id: string;
    outcome: string;
    notes?: string;
    archived_at: string;
  }>;
  preferred_skus?: string[];
  preferred_mode?: string | null;
  default_shipping_address?: boolean;
};

type ReusableFacts = {
  identity_id: number;
  facts: Record<string, unknown>;
};

/**
 * Per-KOL relationship & reusable facts panel. Read-only view; archival
 * happens via the dispatcher / archival skill.
 */
export function KolRelationshipPage() {
  const { id } = useParams();
  const identityId = Number(id);
  const [rel, setRel] = useState<Relationship | null>(null);
  const [reusable, setReusable] = useState<ReusableFacts | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!identityId) return;
    try {
      const [r, f] = await Promise.all([
        api.get<Relationship>(`/identities/${identityId}/relationship`),
        api.get<ReusableFacts>(`/identities/${identityId}/relationship/reusable-facts`),
      ]);
      setRel(r);
      setReusable(f);
      setErr(null);
    } catch (ex) {
      setErr(String(ex));
    }
  }, [identityId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  if (err) return <div className="text-red-600">{err}</div>;
  if (!rel || !reusable) return <div className="text-sm text-slate-500">Loading…</div>;

  return (
    <div className="space-y-3">
      <Link to={`/kols/${identityId}`} className="text-xs text-sky-700 hover:underline">
        ← back to KOL detail
      </Link>
      <h1 className="text-lg font-semibold">Relationship · KOL #{identityId}</h1>

      <section className="rounded border border-slate-200 bg-white p-3">
        <div className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-500">
          Collab history ({rel.collab_history?.length || 0})
        </div>
        {!rel.collab_history?.length && (
          <div className="text-xs text-slate-500">No prior collaborations.</div>
        )}
        <ul className="space-y-1 text-sm">
          {rel.collab_history?.map((c, i) => (
            <li key={i} className="rounded bg-slate-50 p-2">
              <div>
                <strong>{c.campaign_id}</strong> · outcome:{' '}
                <span
                  className={
                    c.outcome === 'success'
                      ? 'text-emerald-700'
                      : c.outcome === 'declined' || c.outcome === 'aborted'
                      ? 'text-amber-700'
                      : 'text-slate-700'
                  }
                >
                  {c.outcome}
                </span>{' '}
                · <span className="text-xs text-slate-500">{c.archived_at}</span>
              </div>
              {c.notes && <div className="text-xs text-slate-600">{c.notes}</div>}
            </li>
          ))}
        </ul>
      </section>

      <section className="rounded border border-slate-200 bg-white p-3">
        <div className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-500">
          Preferences
        </div>
        <div className="text-sm">preferred_mode: {rel.preferred_mode ?? '—'}</div>
        <div className="text-sm">
          preferred_skus:{' '}
          {rel.preferred_skus?.length ? rel.preferred_skus.join(', ') : '—'}
        </div>
        <div className="text-sm">
          default_shipping_address: {rel.default_shipping_address ? 'yes' : 'no'}
        </div>
      </section>

      <section className="rounded border border-slate-200 bg-white p-3">
        <div className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-500">
          Reusable facts
        </div>
        <pre className="overflow-x-auto rounded bg-slate-50 p-2 text-xs">
          {JSON.stringify(reusable.facts, null, 2)}
        </pre>
      </section>
    </div>
  );
}
