import { useCallback, useEffect, useState } from 'react';
import { api, Policy } from '../api';
import { TimeAgo } from '../components/inputs/TimeAgo';
import { ErrorAlert } from '../components/feedback/ErrorAlert';
import { toast } from '../lib/store';
import { errorSummary } from '../lib/errors';

type Tab = 'company_style' | 'user_style' | 'escalation_rules';

const TABS: Array<{ key: Tab; label: string; description: string }> = [
  {
    key: 'company_style',
    label: '公司邮件风格',
    description: 'Owner 维护，全公司适用。',
  },
  {
    key: 'user_style',
    label: '我的邮件风格',
    description: '仅作用于自己的对外邮件草稿。',
  },
  {
    key: 'escalation_rules',
    label: '异常处理规则',
    description:
      'Owner 维护。signals_match 列表与 rule_id 决定升级行为；保存即生效。',
  },
];

type Me = { id: number; email: string; role: 'owner' | 'operator' | 'viewer' };

export function PolicyEditorPage() {
  const [me, setMe] = useState<Me | null>(null);
  const [tab, setTab] = useState<Tab>('company_style');
  const [policy, setPolicy] = useState<Policy | null>(null);
  const [draft, setDraft] = useState('');
  const [history, setHistory] = useState<Policy[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);

  useEffect(() => {
    api.get<Me>('/auth/me').then(setMe).catch((e) => setErr(e));
  }, []);

  const owner = tab === 'user_style' ? me?.id ?? null : null;

  const refresh = useCallback(async () => {
    if (!me) return;
    try {
      const qs = tab === 'user_style' ? `?owner_user_id=${me.id}` : '';
      const resp = await api.get<{ policy: Policy | null }>(
        `/policies/${tab}${qs}`,
      );
      setPolicy(resp.policy);
      setDraft(resp.policy?.content_md ?? '');
      const hist = await api.get<{ history: Policy[] }>(
        `/policies/${tab}/history${qs}`,
      );
      setHistory(hist.history);
      setErr(null);
    } catch (ex) {
      setErr(ex);
    }
  }, [me, tab]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  function canWrite(): boolean {
    if (!me) return false;
    if (me.role === 'viewer') return false;
    if (tab === 'user_style') return true;
    return me.role === 'owner';
  }

  async function save() {
    if (!me) return;
    setBusy(true);
    setErr(null);
    try {
      const body: Record<string, unknown> = { content_md: draft };
      if (tab === 'user_style') body.owner_user_id = me.id;
      await api.put(`/policies/${tab}`, body);
      toast.success('策略已保存', '新版本已生效');
      refresh();
    } catch (ex) {
      setErr(ex);
      toast.error('保存失败', errorSummary(ex));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div data-editing className="space-y-3">
      <h1 className="text-lg font-semibold">策略</h1>
      <div className="flex gap-1 border-b border-slate-200">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={
              'px-3 py-1.5 text-sm ' +
              (tab === t.key
                ? 'border-b-2 border-emerald-600 font-medium text-emerald-700'
                : 'text-slate-600 hover:text-slate-900')
            }
          >
            {t.label}
          </button>
        ))}
      </div>
      <div className="text-xs text-slate-500">
        {TABS.find((t) => t.key === tab)?.description}
      </div>
      {!!err && <ErrorAlert error={err} onRetry={refresh} />}
      <div className="grid gap-3 lg:grid-cols-2">
        <div className="space-y-2">
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            disabled={!canWrite()}
            rows={20}
            className="w-full rounded border border-slate-300 bg-white p-2 font-mono text-sm disabled:bg-slate-50"
            placeholder="Markdown..."
          />
          {canWrite() ? (
            <div className="flex items-center gap-2">
              <button
                disabled={busy}
                onClick={save}
                className="rounded bg-emerald-600 px-3 py-1 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
              >
                {busy ? '保存中…' : '保存（生成新版本）'}
              </button>
              {policy && (
                <span className="ml-auto text-xs text-slate-500">
                  当前版本 v{policy.version} · 更新 <TimeAgo iso={policy.updated_at} />{' '}
                  · {policy.updated_by}
                </span>
              )}
            </div>
          ) : (
            <div className="text-xs text-slate-500">
              你可以查看，但不能修改此范围的策略。
            </div>
          )}
        </div>
        <div className="rounded border border-slate-200 bg-white p-3">
          <div className="mb-2 text-xs uppercase tracking-wide text-slate-500">
            历史版本 {owner !== null && `(owner ${owner})`}
          </div>
          {!history.length && (
            <div className="text-xs text-slate-500">还没有任何修订。</div>
          )}
          <ul className="space-y-1 text-xs">
            {history.map((h) => (
              <li
                key={h.id}
                className={
                  'rounded p-1 ' +
                  (h.is_active ? 'bg-emerald-50 text-emerald-900' : 'text-slate-700')
                }
              >
                v{h.version} · <TimeAgo iso={h.updated_at} /> · {h.updated_by}{' '}
                {h.is_active ? <strong>（当前）</strong> : ''}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}
