import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api } from '../api';
import { KolSearchBox } from '../components/inputs/KolSearchBox';
import { ErrorAlert } from '../components/feedback/ErrorAlert';
import { dialog } from '../components/dialogs/useDialog';
import { toast } from '../lib/store';
import { errorSummary } from '../lib/errors';

// Candidate triage for a campaign. Batch select for outreach, mark
// rejected, open escalation for repeat KOLs.
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

const REL_BADGE: Record<string, { cls: string; label: string }> = {
  new_prospect: { cls: 'bg-sky-100 text-sky-700', label: '新候选' },
  lapsed_collaborator: { cls: 'bg-amber-100 text-amber-800', label: '老朋友(冷)' },
  active_collaborator: { cls: 'bg-emerald-100 text-emerald-700', label: '在合作' },
  repeat_kol_needs_review: { cls: 'bg-rose-100 text-rose-700', label: '需复核' },
};

export function CampaignCandidatesPage() {
  const { id: campaignId = '' } = useParams();
  const [rows, setRows] = useState<Candidate[]>([]);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [filter, setFilter] = useState<'all' | Candidate['relationship_status'] | 'pending'>('pending');
  const [query, setQuery] = useState('');

  const refresh = useCallback(async () => {
    try {
      setRows(await api.get<Candidate[]>(`/campaigns/${encodeURIComponent(campaignId)}/candidates`));
      setErr(null);
    } catch (ex) {
      setErr(ex);
    }
  }, [campaignId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    let base = rows;
    if (filter !== 'all') {
      if (filter === 'pending') base = base.filter((r) => r.status === 'pending');
      else base = base.filter((r) => r.relationship_status === filter);
    }
    if (q) {
      base = base.filter((r) => `${r.handle ?? r.identity_id}`.toLowerCase().includes(q));
    }
    return base;
  }, [rows, filter, query]);

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
      toast.success('已解析关系');
      await refresh();
    } catch (ex) {
      setErr(ex);
      toast.error('解析失败', errorSummary(ex));
    } finally {
      setBusy(false);
    }
  }, [campaignId, refresh]);

  const selectForOutreach = useCallback(async () => {
    if (selected.size === 0) return;
    const ok = await dialog.confirm({
      title: `把 ${selected.size} 个候选发起 outreach？`,
      description: 'AI 会为这些 KOL 起草初邀。',
      confirmLabel: '选定',
      cancelLabel: '取消',
      variant: 'info',
    });
    if (!ok) return;
    setBusy(true);
    try {
      await api.post(`/campaigns/${encodeURIComponent(campaignId)}/candidates/select`, {
        identity_ids: Array.from(selected),
      });
      toast.success(`已选定 ${selected.size} 个候选`);
      setSelected(new Set());
      await refresh();
    } catch (ex) {
      setErr(ex);
      toast.error('选定失败', errorSummary(ex));
    } finally {
      setBusy(false);
    }
  }, [campaignId, selected, refresh]);

  const markRejected = useCallback(
    async (c: Candidate) => {
      const ok = await dialog.confirm({
        title: `把 @${c.handle ?? c.identity_id} 标为已拒绝？`,
        confirmLabel: '拒绝',
        cancelLabel: '取消',
        variant: 'danger',
      });
      if (!ok) return;
      setBusy(true);
      try {
        await api.post(`/campaigns/${encodeURIComponent(campaignId)}/candidates`, {
          identity_id: c.identity_id,
          notes: 'rejected via console',
        });
        toast.success('已拒绝');
        await refresh();
      } catch (ex) {
        setErr(ex);
        toast.error('操作失败', errorSummary(ex));
      } finally {
        setBusy(false);
      }
    },
    [campaignId, refresh],
  );

  const openEscalation = useCallback(
    async (c: Candidate) => {
      const question = await dialog.prompt({
        title: `为 @${c.handle ?? c.identity_id} 开启升级`,
        description: '请输入需要操作员回答的问题。',
        defaultValue: 'Repeat KOL detected — confirm whether to include in outreach.',
        required: true,
        multiline: true,
        confirmLabel: '开启升级',
      });
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
        toast.success('升级已开启');
        await refresh();
      } catch (ex) {
        setErr(ex);
        toast.error('开启失败', errorSummary(ex));
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
          候选列表 — <span className="font-mono">{campaignId}</span>
        </h1>
        <Link
          to={`/kols?campaign_id=${encodeURIComponent(campaignId)}`}
          className="rounded border border-slate-300 px-2 py-1 text-xs hover:bg-slate-50"
        >
          → 看板
        </Link>
        <KolSearchBox value={query} onChange={setQuery} />
        <select
          value={filter ?? 'all'}
          onChange={(e) => setFilter(e.target.value as typeof filter)}
          className="rounded border border-slate-300 px-2 py-1 text-sm"
        >
          <option value="pending">待处理</option>
          <option value="all">全部</option>
          <option value="new_prospect">新候选</option>
          <option value="lapsed_collaborator">老朋友(冷)</option>
          <option value="active_collaborator">在合作</option>
          <option value="repeat_kol_needs_review">需复核</option>
        </select>
        <button
          disabled={busy}
          onClick={resolveRelationships}
          className="rounded border border-slate-300 px-2 py-1 text-sm hover:bg-slate-50 disabled:opacity-40"
        >
          解析关系
        </button>
        <button
          disabled={busy || selected.size === 0}
          onClick={selectForOutreach}
          className="rounded bg-sky-600 px-3 py-1 text-sm text-white hover:bg-sky-700 disabled:opacity-40"
        >
          选定 {selected.size} 个发起 outreach
        </button>
        <button
          onClick={refresh}
          className="rounded border border-slate-300 px-2 py-1 text-sm hover:bg-slate-50"
        >
          刷新
        </button>
      </div>
      {!!err && <ErrorAlert error={err} onRetry={refresh} />}
      <table className="w-full text-sm">
        <thead className="text-left text-xs uppercase tracking-wide text-slate-500">
          <tr>
            <th className="p-2"></th>
            <th className="p-2">handle</th>
            <th className="p-2">关系</th>
            <th className="p-2">合作次数</th>
            <th className="p-2">上次结果</th>
            <th className="p-2">评分</th>
            <th className="p-2">来源</th>
            <th className="p-2">状态</th>
            <th className="p-2">操作</th>
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
                      REL_BADGE[c.relationship_status]?.cls ?? 'bg-slate-100 text-slate-600'
                    }`}
                  >
                    {REL_BADGE[c.relationship_status]?.label ?? c.relationship_status}
                  </span>
                ) : (
                  <span className="text-xs text-slate-400">(未解析)</span>
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
                      升级
                    </button>
                  )}
                  {c.status === 'pending' && (
                    <button
                      disabled={busy}
                      onClick={() => markRejected(c)}
                      className="rounded border border-slate-300 px-2 py-0.5 hover:bg-slate-50 disabled:opacity-40"
                    >
                      拒绝
                    </button>
                  )}
                </div>
              </td>
            </tr>
          ))}
          {filtered.length === 0 && (
            <tr>
              <td colSpan={9} className="p-6 text-center text-sm text-slate-500">
                当前筛选下没有候选。
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
