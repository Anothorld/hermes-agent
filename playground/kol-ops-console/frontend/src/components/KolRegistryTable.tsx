import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, ApiError } from '../api';
import { toast, useEnvStore } from '../lib/store';
import { ErrorAlert } from './feedback/ErrorAlert';
import { formatMetric } from '../lib/kolProfileMetrics';
import { AudienceProfileHoverButton } from './NoxAudienceHoverPanel';

export type KolRegistryRow = {
  identity_id: number;
  handle: string | null;
  display_name?: string | null;
  email: string | null;
  ig_url: string | null;
  internal_touch_count: number;
  target_spu: string | null;
  followers: unknown;
  avg_views: unknown;
  audience_facts: Record<string, unknown>;
  latest_campaign_id?: string | null;
  has_legacy_import?: boolean;
  has_initial_outreach_draft?: boolean;
  has_inbound_reply?: boolean;
  first_discovered_at?: string | null;
};

type IngestSortOrder = 'desc' | 'asc';

type RegistryResp = {
  env: string;
  total: number;
  limit: number;
  offset: number;
  items: KolRegistryRow[];
};

const PAGE_SIZE = 50;

function cellText(value: unknown): string {
  if (value == null || value === '') return '';
  const formatted = formatMetric(value);
  return formatted ?? String(value);
}

function formatIngestedAt(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function YesNoCell({ value, yesLabel = '是', noLabel = '否' }: {
  value: boolean | undefined;
  yesLabel?: string;
  noLabel?: string;
}) {
  if (value) {
    return (
      <span className="inline-flex rounded bg-emerald-50 px-1.5 py-0.5 font-medium text-emerald-800">
        {yesLabel}
      </span>
    );
  }
  return (
    <span className="inline-flex rounded bg-slate-100 px-1.5 py-0.5 text-slate-500">
      {noLabel}
    </span>
  );
}

export function KolRegistryTable() {
  const env = useEnvStore((s) => s.env);
  const [page, setPage] = useState(0);
  const [q, setQ] = useState('');
  const [search, setSearch] = useState('');
  const [data, setData] = useState<RegistryResp | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [exporting, setExporting] = useState(false);
  const [ingestOrder, setIngestOrder] = useState<IngestSortOrder>('desc');

  const refresh = useCallback(async () => {
    const offset = page * PAGE_SIZE;
    const params = new URLSearchParams({
      env,
      sort: 'ingested_at',
      order: ingestOrder,
      limit: String(PAGE_SIZE),
      offset: String(offset),
    });
    if (search.trim()) params.set('q', search.trim());
    try {
      const out = await api.get<RegistryResp>(`/admin/kol-registry?${params}`);
      setData(out);
      setErr(null);
    } catch (ex) {
      setErr(ex);
    }
  }, [env, page, search, ingestOrder]);

  useEffect(() => {
    setPage(0);
  }, [env, ingestOrder]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const total = data?.total ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const from = total === 0 ? 0 : page * PAGE_SIZE + 1;
  const to = Math.min(total, (page + 1) * PAGE_SIZE);

  const exportExcel = async () => {
    setExporting(true);
    try {
      const params = new URLSearchParams({ env });
      if (search.trim()) params.set('q', search.trim());
      const blob = await api.download(`/admin/kol-registry/export?${params}`);
      const stamp = new Date().toISOString().slice(0, 10).replace(/-/g, '');
      const filename = `Agent红人列表_${env}_${stamp}.xlsx`;
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = filename;
      anchor.click();
      URL.revokeObjectURL(url);
      toast.success('导出完成', `已下载 ${filename}`);
    } catch (ex) {
      const msg = ex instanceof ApiError ? ex.body || ex.message : String(ex);
      toast.error('导出失败', msg);
    } finally {
      setExporting(false);
    }
  };

  return (
    <section className="rounded border border-slate-200 bg-white">
      <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 px-3 py-2">
        <h2 className="text-sm font-semibold text-slate-800">红人列表</h2>
        <span className="text-xs text-slate-500">
          {data ? `共 ${data.total} 条 Agent 发现记录` : '含全部发现候选（不限 shortlist）'}
        </span>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <input
            type="search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                setPage(0);
                setSearch(q);
              }
            }}
            placeholder="搜索 ID / 邮箱"
            className="w-40 rounded border border-slate-300 px-2 py-1 text-xs"
          />
          <button
            type="button"
            onClick={() => {
              setPage(0);
              setSearch(q);
            }}
            className="rounded border border-slate-300 px-2 py-1 text-xs hover:bg-slate-50"
          >
            搜索
          </button>
          <button
            type="button"
            onClick={() => void refresh()}
            className="rounded border border-slate-300 px-2 py-1 text-xs hover:bg-slate-50"
          >
            刷新
          </button>
          <button
            type="button"
            disabled={exporting}
            onClick={() => void exportExcel()}
            className="rounded border border-emerald-300 bg-emerald-50 px-2 py-1 text-xs font-medium text-emerald-800 hover:bg-emerald-100 disabled:opacity-50"
            title="导出当前筛选条件下的全部红人（不限本页）"
          >
            {exporting ? '导出中…' : '导出 Excel'}
          </button>
        </div>
      </div>

      {!!err && <div className="p-3"><ErrorAlert error={err} onRetry={refresh} /></div>}

      {!data && !err && (
        <div className="p-4 text-sm text-slate-500">加载红人列表…</div>
      )}

      {data && (
        <>
          <div className="overflow-x-auto">
            <table className="min-w-full text-left text-xs">
              <thead className="bg-slate-50 text-slate-600">
                <tr>
                  <th className="whitespace-nowrap px-3 py-2 font-medium">序号</th>
                  <th className="whitespace-nowrap px-3 py-2 font-medium">
                    <button
                      type="button"
                      onClick={() => setIngestOrder((o) => (o === 'desc' ? 'asc' : 'desc'))}
                      className="inline-flex items-center gap-0.5 font-medium hover:text-sky-700"
                      title="按 Agent 发现入库时间排序，点击切换升序/降序"
                    >
                      入库时间
                      <span className="text-[10px] text-slate-400" aria-hidden>
                        {ingestOrder === 'desc' ? '↓' : '↑'}
                      </span>
                    </button>
                  </th>
                  <th className="whitespace-nowrap px-3 py-2 font-medium">ID</th>
                  <th className="whitespace-nowrap px-3 py-2 font-medium">IG链接</th>
                  <th className="whitespace-nowrap px-3 py-2 font-medium">内部曾触达次数</th>
                  <th
                    className="whitespace-nowrap px-3 py-2 font-medium"
                    title="是否已审批并生成初邀 Gmail 草稿"
                  >
                    初邀已批准
                  </th>
                  <th
                    className="whitespace-nowrap px-3 py-2 font-medium"
                    title="是否收到红人回信（系统已记录 inbound 邮件）"
                  >
                    有回信
                  </th>
                  <th className="whitespace-nowrap px-3 py-2 font-medium">目标SPU</th>
                  <th className="whitespace-nowrap px-3 py-2 font-medium">粉丝量</th>
                  <th className="whitespace-nowrap px-3 py-2 font-medium">平均播放</th>
                  <th className="whitespace-nowrap px-3 py-2 font-medium">受众画像</th>
                  <th className="whitespace-nowrap px-3 py-2 font-medium">邮箱</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {data.items.length === 0 ? (
                  <tr>
                    <td colSpan={12} className="px-3 py-6 text-center text-slate-500">
                      暂无发现记录
                    </td>
                  </tr>
                ) : (
                  data.items.map((row, idx) => {
                    const seq = data.offset + idx + 1;
                    const handle = row.handle?.replace(/^@/, '') ?? '';
                    return (
                      <tr key={row.identity_id} className="hover:bg-slate-50/80">
                        <td className="whitespace-nowrap px-3 py-2 text-slate-600">{seq}</td>
                        <td
                          className="whitespace-nowrap px-3 py-2 text-slate-600"
                          title={row.first_discovered_at ?? ''}
                        >
                          {formatIngestedAt(row.first_discovered_at)}
                        </td>
                        <td className="whitespace-nowrap px-3 py-2">
                          <Link
                            to={`/kols/${row.identity_id}`}
                            className="font-medium text-sky-700 hover:underline"
                          >
                            {handle || `kol#${row.identity_id}`}
                          </Link>
                        </td>
                        <td className="max-w-[12rem] truncate px-3 py-2">
                          {row.ig_url ? (
                            <a
                              href={row.ig_url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-sky-700 hover:underline"
                              title={row.ig_url}
                            >
                              {row.ig_url.replace(/^https?:\/\/(www\.)?instagram\.com\//, '@')}
                            </a>
                          ) : (
                            <span className="text-slate-400">—</span>
                          )}
                        </td>
                        <td className="whitespace-nowrap px-3 py-2 text-center">
                          {row.internal_touch_count}
                        </td>
                        <td className="whitespace-nowrap px-3 py-2 text-center">
                          <YesNoCell value={row.has_initial_outreach_draft} />
                        </td>
                        <td className="whitespace-nowrap px-3 py-2 text-center">
                          <YesNoCell value={row.has_inbound_reply} />
                        </td>
                        <td className="whitespace-nowrap px-3 py-2">
                          {row.target_spu || <span className="text-slate-400">—</span>}
                        </td>
                        <td className="whitespace-nowrap px-3 py-2">
                          {cellText(row.followers) || <span className="text-slate-400">—</span>}
                        </td>
                        <td className="whitespace-nowrap px-3 py-2">
                          {cellText(row.avg_views) || <span className="text-slate-400">—</span>}
                        </td>
                        <td className="px-3 py-2">
                          <AudienceProfileHoverButton facts={row.audience_facts ?? {}} />
                        </td>
                        <td className="max-w-[14rem] truncate px-3 py-2" title={row.email ?? ''}>
                          {row.email || <span className="text-slate-400">—</span>}
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-100 px-3 py-2 text-xs text-slate-600">
            <span>
              {total === 0 ? '共 0 条' : `第 ${from}–${to} 条，共 ${total} 条`}
            </span>
            <div className="flex items-center gap-1">
              <button
                type="button"
                disabled={page <= 0}
                onClick={() => setPage((p) => Math.max(0, p - 1))}
                className="rounded border border-slate-300 px-2 py-1 disabled:opacity-40 hover:bg-slate-50"
              >
                上一页
              </button>
              <span className="px-2">
                {page + 1} / {pageCount}
              </span>
              <button
                type="button"
                disabled={page + 1 >= pageCount}
                onClick={() => setPage((p) => p + 1)}
                className="rounded border border-slate-300 px-2 py-1 disabled:opacity-40 hover:bg-slate-50"
              >
                下一页
              </button>
            </div>
          </div>
        </>
      )}
    </section>
  );
}
