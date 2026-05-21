import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api } from '../api';

/**
 * Candidate triage for a campaign.
 * - Lists candidates with relationship_status (new_prospect / lapsed_collaborator /
 *   active_collaborator / repeat_kol_needs_review) + discovery_score.
 * - Batch select for outreach (POST /candidates/select).
 * - Mark rejected (POST /candidates with status='rejected').
 * - Open escalation for repeat_kol_needs_review.
 */
type Candidate = {
  identity_id: number;
  handle: string | null;
  discovery_score: number | null;
  discovery_source: string | null;
  relationship_status:
    | 'new_prospect'
    | 'lapsed_collaborator'
    | 'active_collaborator'
    | 'repeat_kol_needs_review'
    | null;
  total_collabs: number | null;
  last_outcome: string | null;
  status: 'pending' | 'selected' | 'rejected';
  notes: string | null;
};

const REL_BADGE: Record<string, string> = {
  new_prospect: 'bg-sky-100 text-sky-700',
  lapsed_collaborator: 'bg-amber-100 text-amber-800',
  active_collaborator: 'bg-emerald-100 text-emerald-700',
  repeat_kol_needs_review: 'bg-rose-100 text-rose-700',
};

export function CampaignCandidatesPage() {
  const { id: campaignId = '' } = useParams();
  const [rows, setRows] = useState<Candidate[]>([]);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [filter, setFilter] = useState<'all' | Candidate['relationship_status'] | 'pending'>('pending');

  const refresh = useCallback(async () => {
    try {
      setRows(await api.get<Candidate[]>(`/campaigns/${encodeURIComponent(campaignId)}/candidates`));
      setErr(null);
    } catch (ex) {
      setErr(String(ex));
    }
  }, [campaignId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const filtered = useMemo(() => {
    if (filter === 'all') return rows;
    if (filter === 'pending') return rows.filter((r) => r.status === 'pending');
    return rows.filter((r) => r.relationship_status === filter);
  }, [rows, filter]);

  const toggle = (id: number) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const resolveRelationships = useCallback(async () => {
    setBusy(true);
    try {
      await api.post(`/campaigns/${encodeURIComponent(campaignId)}/candidates/resolve-relationships`);
      await refresh();
    } catch (ex) {
      setErr(String(ex));
    } finally {
      setBusy(false);
    }
  }, [campaignId, refresh]);

  const selectForOutreach = useCallback(async () => {
    if (selected.size === 0) return;
    setBusy(true);
    try {
      await api.post(`/campaigns/${encodeURIComponent(campaignId)}/candidates/select`, {
        identity_ids: Array.from(selected),
      });
      setSelected(new Set());
      await refresh();
    } catch (ex) {
      setErr(String(ex));
    } finally {
      setBusy(false);
    }
  }, [campaignId, selected, refresh]);

  const markRejected = useCallback(
    async (c: Candidate) => {
      setBusy(true);
      try {
        await api.post(`/campaigns/${encodeURIComponent(campaignId)}/candidates`, {
          identity_id: c.identity_id,
          notes: 'rejected via console',
        });
        // bridge POST is upsert; status field handled server-side
        await refresh();
      } catch (ex) {
        setErr(String(ex));
      } finally {
        setBusy(false);
      }
    },
    [campaignId, refresh],
  );

  const openEscalation = useCallback(
    async (c: Candidate) => {
      const question = window.prompt(
        `Open escalation for @${c.handle ?? c.identity_id}?\n\nQuestion to operator:`,
        'Repeat KOL detected — confirm whether to include in outreach.',
      );
      if (!question) return;
      setBusy(true);
      try {
        await api.post('/escalations', {
          identity_id: c.identity_id,
          campaign_id: campaignId,
          rule_id: 'repeat_kol_needs_review',
          reason: 'Repeat KOL flagged on candidate triage',
          suggested_question: question,
        });
        await refresh();
      } catch (ex) {
        setErr(String(ex));
      } finally {
        setBusy(false);
      }
    },
    [campaignId, refresh],
  );

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="text-lg font-semibold">
          Candidates — <span className="font-mono">{campaignId}</span>
        </h1>
        <Link
          to={`/kols?campaign_id=${encodeURIComponent(campaignId)}`}
          className="rounded border border-slate-300 px-2 py-1 text-xs hover:bg-slate-50"
        >
          → Kanban
        </Link>
        <select
          value={filter ?? 'all'}
          onChange={(e) => setFilter(e.target.value as typeof filter)}
          className="rounded border border-slate-300 px-2 py-1 text-sm"
        >
          <option value="pending">pending</option>
          <option value="all">all</option>
          <option value="new_prospect">new_prospect</option>
          <option value="lapsed_collaborator">lapsed_collaborator</option>
          <option value="active_collaborator">active_collaborator</option>
          <option value="repeat_kol_needs_review">repeat_kol_needs_review</option>
        </select>
        <button
          disabled={busy}
          onClick={resolveRelationships}
          className="rounded border border-slate-300 px-2 py-1 text-sm hover:bg-slate-50 disabled:opacity-40"
        >
          Resolve relationships
        </button>
        <button
          disabled={busy || selected.size === 0}
          onClick={selectForOutreach}
          className="rounded bg-sky-600 px-3 py-1 text-sm text-white hover:bg-sky-700 disabled:opacity-40"
        >
          Select {selected.size} for outreach
        </button>
        <button
          onClick={refresh}
          className="rounded border border-slate-300 px-2 py-1 text-sm hover:bg-slate-50"
        >
          Refresh
        </button>
      </div>
      {err && <div className="text-sm text-red-600">{err}</div>}
      <table className="w-full text-sm">
        <thead className="text-left text-xs uppercase tracking-wide text-slate-500">
          <tr>
            <th className="p-2"></th>
            <th className="p-2">handle</th>
            <th className="p-2">relationship</th>
            <th className="p-2">total_collabs</th>
            <th className="p-2">last_outcome</th>
            <th className="p-2">score</th>
            <th className="p-2">source</th>
            <th className="p-2">status</th>
            <th className="p-2">actions</th>
          </tr>
        </thead>
        <tbody>
          {filtered.map((c) => (
            <tr key={c.identity_id} className="border-t border-slate-100 hover:bg-slate-50">
              <td className="p-2">
                <input
                  type="checkbox"
                  disabled={c.status !== 'pending'}
                  checked={selected.has(c.identity_id)}
                  onChange={() => toggle(c.identity_id)}
                />
              </td>
              <td className="p-2">
                <Link
                  to={`/kols/${c.identity_id}?campaign_id=${encodeURIComponent(campaignId)}`}
                  className="text-sky-700 hover:underline"
                >
                  @{c.handle ?? c.identity_id}
                </Link>
              </td>
              <td className="p-2">
                {c.relationship_status ? (
                  <span
                    className={`rounded px-2 py-0.5 text-xs ${
                      REL_BADGE[c.relationship_status] ?? 'bg-slate-100 text-slate-600'
                    }`}
                  >
                    {c.relationship_status}
                  </span>
                ) : (
                  <span className="text-xs text-slate-400">(unresolved)</span>
                )}
              </td>
              <td className="p-2 text-xs">{c.total_collabs ?? 0}</td>
              <td className="p-2 text-xs">{c.last_outcome ?? '—'}</td>
              <td className="p-2 text-xs">{c.discovery_score?.toFixed(2) ?? '—'}</td>
              <td className="p-2 text-xs">{c.discovery_source ?? '—'}</td>
              <td className="p-2 text-xs">{c.status}</td>
              <td className="p-2 text-xs">
                <div className="flex gap-1">
                  {c.relationship_status === 'repeat_kol_needs_review' && (
                    <button
                      disabled={busy}
                      onClick={() => openEscalation(c)}
                      className="rounded bg-rose-600 px-2 py-0.5 text-white hover:bg-rose-700 disabled:opacity-40"
                    >
                      Escalate
                    </button>
                  )}
                  {c.status === 'pending' && (
                    <button
                      disabled={busy}
                      onClick={() => markRejected(c)}
                      className="rounded border border-slate-300 px-2 py-0.5 hover:bg-slate-50 disabled:opacity-40"
                    >
                      Reject
                    </button>
                  )}
                </div>
              </td>
            </tr>
          ))}
          {filtered.length === 0 && (
            <tr>
              <td colSpan={9} className="p-6 text-center text-sm text-slate-500">
                No candidates match this filter.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
