import { useCallback, useEffect, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { api, GoalState, Lane } from '../api';
import { GoalProgressBar } from '../components/GoalProgressBar';
import { FactsEditor } from '../components/FactsEditor';
import { RepeatKolBadge } from '../components/RepeatKolBadge';

type GoalsResponse = {
  identity_id: number;
  campaign_id: string;
  goals: Record<Lane, GoalState | null>;
};

type IdentityResponse = {
  id: number;
  handle: string;
  primary_email: string | null;
  creator_type: string | null;
  env: string;
  repeat_count?: number;
  last_outcome?: string | null;
};

type EscalationLite = {
  id: number;
  rule_id: string | null;
  reason: string;
  state: string;
  created_at: string;
};

export function KolDetailPage() {
  const { id } = useParams();
  const [search] = useSearchParams();
  const campaignId = search.get('campaign_id') || '';
  const identityId = Number(id);
  const [identity, setIdentity] = useState<IdentityResponse | null>(null);
  const [goals, setGoals] = useState<GoalsResponse | null>(null);
  const [escalations, setEscalations] = useState<EscalationLite[]>([]);
  const [err, setErr] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!identityId || !campaignId) {
      setErr('Need identity id + ?campaign_id=<>');
      return;
    }
    try {
      const [idResp, goalsResp, esc] = await Promise.all([
        api.get<IdentityResponse>(`/kols/${identityId}`),
        api.get<GoalsResponse>(
          `/identities/${identityId}/goals?campaign_id=${encodeURIComponent(campaignId)}`,
        ),
        api.get<EscalationLite[]>(`/escalations?state=open`),
      ]);
      setIdentity(idResp);
      setGoals(goalsResp);
      setEscalations(
        (esc || []).filter((e) => (e as unknown as { identity_id?: number }).identity_id === identityId),
      );
      setErr(null);
    } catch (ex) {
      setErr(String(ex));
    }
  }, [identityId, campaignId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  if (err) return <div className="text-red-600">{err}</div>;
  if (!identity || !goals) return <div className="text-sm text-slate-500">Loading…</div>;

  const lanes: Lane[] = ['commerce', 'fulfillment', 'publish', 'meta'];
  const activeCommerce = goals.goals.commerce?.goal ?? null;
  const allMissing = lanes.flatMap((l) => goals.goals[l]?.missing_facts ?? []);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-lg font-semibold">
          @{identity.handle}
          <RepeatKolBadge
            count={identity.repeat_count || 0}
            lastOutcome={identity.last_outcome ?? null}
          />
        </h1>
        <div className="text-xs text-slate-500">
          {identity.primary_email} · {identity.env}
        </div>
        <Link
          to={`/kols/${identity.id}/relationship`}
          className="ml-auto text-xs text-sky-700 hover:underline"
        >
          history & reusable facts →
        </Link>
      </div>

      <GoalProgressBar
        active={activeCommerce}
        blocked={!!goals.goals.commerce?.blocked_reason}
      />

      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-4">
        {lanes.map((lane) => {
          const g = goals.goals[lane];
          return (
            <div
              key={lane}
              className="rounded border border-slate-200 bg-white p-3 text-sm"
            >
              <div className="mb-1 text-xs uppercase tracking-wide text-slate-500">
                {lane}
              </div>
              <div className="font-medium">{g?.goal ?? <em>idle</em>}</div>
              <div className="text-xs text-slate-600">state: {g?.state ?? '-'}</div>
              {g?.blocked_reason && (
                <div className="mt-1 rounded bg-amber-100 px-2 py-1 text-xs text-amber-900">
                  blocked: {g.blocked_reason}
                </div>
              )}
              {!!g?.missing_facts?.length && (
                <div className="mt-1 flex flex-wrap gap-1">
                  {g.missing_facts.map((f) => (
                    <span
                      key={f}
                      className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[10px] text-slate-700"
                    >
                      {f}
                    </span>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {!!escalations.length && (
        <div className="rounded border border-amber-300 bg-amber-50 p-3">
          <div className="mb-1 text-xs font-medium uppercase tracking-wide text-amber-800">
            Open escalations ({escalations.length})
          </div>
          <ul className="space-y-1 text-sm">
            {escalations.map((e) => (
              <li key={e.id}>
                <Link
                  to={`/escalations/${e.id}`}
                  className="text-amber-900 underline-offset-2 hover:underline"
                >
                  #{e.id} · {e.rule_id || 'manual'} · {e.reason}
                </Link>
              </li>
            ))}
          </ul>
        </div>
      )}

      <FactsEditor
        identityId={identityId}
        campaignId={campaignId}
        factKeys={Array.from(new Set(allMissing))}
        onSubmitted={refresh}
      />
    </div>
  );
}
