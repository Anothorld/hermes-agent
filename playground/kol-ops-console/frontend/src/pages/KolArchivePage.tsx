import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import { TimeAgo } from '../components/inputs/TimeAgo';
import { ErrorAlert } from '../components/feedback/ErrorAlert';
import { OUTCOMES, outcomeLabel, outcomeTextClass } from '../lib/kolOutcomes';

type ArchivedKol = {
  identity_id: number;
  primary_handle: string | null;
  display_name: string | null;
  platform: string | null;
  primary_email: string | null;
  total_collabs: number;
  last_outcome: string | null;
  last_campaign_id: string | null;
  last_archived_at: string | null;
  preferred_mode: string | null;
  preferred_skus: string[];
};

type ArchivedList = {
  total: number;
  limit: number;
  offset: number;
  items: ArchivedKol[];
  env: string;
};

const OUTCOME_OPTIONS: ReadonlyArray<readonly [string, string]> = [
  ['', '全部结果'],
  ...OUTCOMES.map((o) => [o.value, o.label] as const),
];

const PLATFORM_OPTIONS = [
  ['', '全部平台'],
  ['instagram', 'instagram'],
  ['tiktok', 'tiktok'],
  ['youtube', 'youtube'],
  ['pinterest', 'pinterest'],
] as const;

const PAGE_SIZE = 100;

const outcomeClass = outcomeTextClass;

export function KolArchivePage() {
  const [data, setData] = useState<ArchivedList | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [q, setQ] = useState('');
  const [outcomeFilter, setOutcomeFilter] = useState('');
  const [platformFilter, setPlatformFilter] = useState('');
  const [offset, setOffset] = useState(0);

  const fetchPage = useCallback(async () => {
    const params = new URLSearchParams();
    params.set('limit', String(PAGE_SIZE));
    params.set('offset', String(offset));
    if (q.trim()) params.set('q', q.trim());
    if (outcomeFilter) params.set('last_outcome', outcomeFilter);
    if (platformFilter) params.set('platform', platformFilter);
    try {
      const res = await api.get<ArchivedList>(`/kols/archive?${params.toString()}`);
      setData(res);
      setErr(null);
    } catch (ex) {
      setErr(ex);
    }
  }, [q, outcomeFilter, platformFilter, offset]);

  useEffect(() => {
    fetchPage();
  }, [fetchPage]);

  const pageInfo = useMemo(() => {
    if (!data) return '';
    const start = data.total === 0 ? 0 : data.offset + 1;
    const end = Math.min(data.offset + data.items.length, data.total);
    return `${start}–${end} / ${data.total}`;
  }, [data]);

  return (
    <div className="space-y-3">
      <div className="flex items-baseline justify-between">
        <h1 className="text-lg font-semibold">历史合作 KOL 池</h1>
        <span className="text-xs text-slate-500">{pageInfo}</span>
      </div>

      <div className="flex flex-wrap items-center gap-2 rounded border border-slate-200 bg-white p-2">
        <input
          type="text"
          placeholder="搜索 handle / display name / email…"
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setOffset(0);
          }}
          className="min-w-[240px] flex-1 rounded border border-slate-300 px-2 py-1 text-sm"
        />
        <select
          value={outcomeFilter}
          onChange={(e) => {
            setOutcomeFilter(e.target.value);
            setOffset(0);
          }}
          className="rounded border border-slate-300 px-2 py-1 text-sm"
        >
          {OUTCOME_OPTIONS.map(([v, label]) => (
            <option key={v} value={v}>
              {label}
            </option>
          ))}
        </select>
        <select
          value={platformFilter}
          onChange={(e) => {
            setPlatformFilter(e.target.value);
            setOffset(0);
          }}
          className="rounded border border-slate-300 px-2 py-1 text-sm"
        >
          {PLATFORM_OPTIONS.map(([v, label]) => (
            <option key={v} value={v}>
              {label}
            </option>
          ))}
        </select>
      </div>

      {!!err && <ErrorAlert error={err} onRetry={fetchPage} />}

      {data && (
        <div className="overflow-x-auto rounded border border-slate-200 bg-white">
          <table className="min-w-full text-sm">
            <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-3 py-2 text-left">Handle</th>
                <th className="px-3 py-2 text-left">平台</th>
                <th className="px-3 py-2 text-right">累计</th>
                <th className="px-3 py-2 text-left">最近结果</th>
                <th className="px-3 py-2 text-left">最近 archive</th>
                <th className="px-3 py-2 text-left">偏好 SKU</th>
                <th className="px-3 py-2 text-left">邮箱</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((k) => (
                <tr key={k.identity_id} className="border-t border-slate-100 hover:bg-slate-50">
                  <td className="px-3 py-1.5">
                    <Link
                      to={`/kols/${k.identity_id}/relationship`}
                      className="text-sky-700 hover:underline"
                    >
                      {k.primary_handle || `#${k.identity_id}`}
                    </Link>
                    {k.display_name && k.display_name !== k.primary_handle && (
                      <span className="ml-1 text-xs text-slate-500">({k.display_name})</span>
                    )}
                  </td>
                  <td className="px-3 py-1.5 text-xs text-slate-600">{k.platform || '—'}</td>
                  <td className="px-3 py-1.5 text-right font-mono text-xs">{k.total_collabs}</td>
                  <td className={`px-3 py-1.5 text-xs ${outcomeClass(k.last_outcome)}`}>
                    {k.last_outcome ? outcomeLabel(k.last_outcome) : '—'}
                  </td>
                  <td className="px-3 py-1.5 text-xs">
                    {k.last_archived_at ? <TimeAgo iso={k.last_archived_at} /> : '—'}
                  </td>
                  <td className="px-3 py-1.5 font-mono text-[11px] text-slate-600">
                    {k.preferred_skus.length > 0 ? k.preferred_skus.slice(0, 3).join(', ') : '—'}
                  </td>
                  <td className="px-3 py-1.5 text-xs text-slate-500">{k.primary_email || '—'}</td>
                </tr>
              ))}
              {data.items.length === 0 && (
                <tr>
                  <td colSpan={7} className="px-3 py-4 text-center text-sm text-slate-500">
                    没有命中的 KOL。
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {data && data.total > PAGE_SIZE && (
        <div className="flex items-center justify-end gap-2 text-sm">
          <button
            type="button"
            onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
            disabled={offset === 0}
            className="rounded border border-slate-300 px-3 py-1 disabled:opacity-50"
          >
            上一页
          </button>
          <button
            type="button"
            onClick={() => setOffset((o) => o + PAGE_SIZE)}
            disabled={offset + PAGE_SIZE >= data.total}
            className="rounded border border-slate-300 px-3 py-1 disabled:opacity-50"
          >
            下一页
          </button>
        </div>
      )}
    </div>
  );
}
