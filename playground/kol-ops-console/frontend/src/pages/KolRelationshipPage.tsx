import { useCallback, useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api } from '../api';
import { TimeAgo } from '../components/inputs/TimeAgo';
import { FactKeyChip } from '../components/inputs/FactKeyChip';
import { ErrorAlert } from '../components/feedback/ErrorAlert';
import { factKeyLabel } from '../components/factKeyLabel';

type CollabEntry = {
  campaign_id: string;
  outcome: string;
  notes?: string | null;
  archived_at: string;
  source?: 'archive' | 'legacy_import';
  env?: string;
  handle?: string | null;
  skus?: string[];
};

type Relationship = {
  identity_id: number;
  total_collabs?: number;
  last_outcome?: string | null;
  last_campaign_id?: string | null;
  last_archived_at?: string | null;
  preferred_skus?: string[];
  preferred_mode?: string | null;
  default_shipping_address?: boolean;
  collab_history?: CollabEntry[];
};

type ReusableFacts = {
  identity_id: number;
  facts: Record<string, unknown>;
};

// Per-KOL relationship & reusable facts panel. Read-only view.
export function KolRelationshipPage() {
  const { id } = useParams();
  const identityId = Number(id);
  const [rel, setRel] = useState<Relationship | null>(null);
  const [reusable, setReusable] = useState<ReusableFacts | null>(null);
  const [err, setErr] = useState<unknown>(null);

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
      setErr(ex);
    }
  }, [identityId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  if (err) return <ErrorAlert error={err} onRetry={refresh} />;
  if (!rel || !reusable) return <div className="text-sm text-slate-500">加载中…</div>;

  return (
    <div className="space-y-3">
      <Link to={`/kols/${identityId}`} className="text-xs text-sky-700 hover:underline">
        ← 返回 KOL 详情
      </Link>
      <h1 className="text-lg font-semibold">历史 · KOL #{identityId}</h1>

      <section className="rounded border border-slate-200 bg-white p-3">
        <div className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-500">
          概览
        </div>
        <div className="grid grid-cols-2 gap-y-1 text-sm md:grid-cols-4">
          <div>
            <span className="text-slate-500">累计合作：</span>
            <strong>{rel.total_collabs ?? 0}</strong>
          </div>
          <div>
            <span className="text-slate-500">最近结果：</span>
            <span
              className={
                rel.last_outcome === 'success'
                  ? 'text-emerald-700'
                  : rel.last_outcome === 'aborted' || rel.last_outcome === 'declined'
                  ? 'text-amber-700'
                  : 'text-slate-700'
              }
            >
              {rel.last_outcome || '—'}
            </span>
          </div>
          <div>
            <span className="text-slate-500">最近 archive：</span>
            {rel.last_archived_at ? (
              <TimeAgo iso={rel.last_archived_at} className="text-xs text-slate-700" />
            ) : (
              '—'
            )}
          </div>
          <div className="truncate">
            <span className="text-slate-500">最近 campaign：</span>
            <span className="font-mono text-xs">{rel.last_campaign_id || '—'}</span>
          </div>
        </div>
      </section>

      <section className="rounded border border-slate-200 bg-white p-3">
        <div className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-500">
          合作历史 ({rel.collab_history?.length || 0})
        </div>
        {!rel.collab_history?.length && (
          <div className="text-xs text-slate-500">
            未发现已 archive 的历史合作。
            {(rel.total_collabs ?? 0) > 0 && (
              <span className="ml-1 text-amber-700">
                （但 total_collabs={rel.total_collabs}，可能 archival_outcome 已被清理）
              </span>
            )}
          </div>
        )}
        <ul className="space-y-1 text-sm">
          {rel.collab_history?.map((c) => (
            <li key={c.campaign_id} className="rounded bg-slate-50 p-2">
              <div className="flex flex-wrap items-baseline gap-2">
                <strong className="font-mono text-xs">{c.campaign_id}</strong>
                <span className="text-slate-400">·</span>
                <span className="text-xs text-slate-500">结果</span>
                <span
                  className={
                    c.outcome === 'success'
                      ? 'text-emerald-700'
                      : c.outcome === 'aborted' || c.outcome === 'declined'
                      ? 'text-amber-700'
                      : c.outcome === 'outreach_only'
                      ? 'text-sky-700'
                      : 'text-slate-700'
                  }
                >
                  {c.outcome || '—'}
                </span>
                <span className="text-slate-400">·</span>
                {c.archived_at && (
                  <TimeAgo iso={c.archived_at} className="text-xs text-slate-500" />
                )}
                {c.source === 'legacy_import' && (
                  <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[10px] uppercase text-amber-800">
                    legacy
                  </span>
                )}
              </div>
              {c.notes && <div className="text-xs text-slate-600">{c.notes}</div>}
              {c.skus && c.skus.length > 0 && (
                <div className="text-xs text-slate-600">
                  SKU: <span className="font-mono">{c.skus.join(', ')}</span>
                </div>
              )}
            </li>
          ))}
        </ul>
      </section>

      <section className="rounded border border-slate-200 bg-white p-3">
        <div className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-500">
          偏好
        </div>
        <div className="text-sm">合作模式：{rel.preferred_mode ?? '—'}</div>
        <div className="text-sm">
          偏好 SKU：{rel.preferred_skus?.length ? rel.preferred_skus.join(', ') : '—'}
        </div>
        <div className="text-sm">
          已有默认收件地址：{rel.default_shipping_address ? '是' : '否'}
        </div>
      </section>

      <section className="rounded border border-slate-200 bg-white p-3">
        <div className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-500">
          可复用事实
        </div>
        {Object.keys(reusable.facts).length === 0 ? (
          <div className="text-xs text-slate-500">还没有可复用事实。</div>
        ) : (
          <ul className="space-y-1.5">
            {Object.entries(reusable.facts).map(([k, v]) => (
              <li key={k} className="rounded bg-slate-50 p-1.5">
                <FactKeyChip factKey={k} variant="filled" />
                <div className="mt-0.5 break-all text-xs text-slate-900">
                  {renderValue(k, v)}
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function renderValue(key: string, v: unknown) {
  if (v === null || v === undefined) return <span className="italic text-slate-400">空</span>;
  const meta = factKeyLabel(key);
  if (meta.kind === 'bool') return v ? '✓ 是' : '— 否';
  if (meta.kind === 'datetime' && typeof v === 'string') return <TimeAgo iso={v} />;
  if (typeof v === 'string') return v;
  if (typeof v === 'number' || typeof v === 'boolean') return String(v);
  try {
    return <span className="font-mono text-[11px]">{JSON.stringify(v)}</span>;
  } catch {
    return String(v);
  }
}
