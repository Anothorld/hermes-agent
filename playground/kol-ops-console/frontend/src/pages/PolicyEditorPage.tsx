import { useCallback, useEffect, useState } from 'react';
import { api, Policy } from '../api';
import { TimeAgo } from '../components/inputs/TimeAgo';
import { ErrorAlert } from '../components/feedback/ErrorAlert';
import { toast } from '../lib/store';
import { errorSummary } from '../lib/errors';
import { dialog } from '../components/dialogs/useDialog';
import { policyScopeLabel } from '../constants/domainLabels';
import { useEnvStore } from '../lib/store';

type Tab =
  | 'company_style'
  | 'user_style'
  | 'escalation_rules'
  | 'reply_strategy'
  | 'outcome_strategy';

const TABS: Array<{ key: Tab; label: string; description: string; ownerOnly?: boolean }> = [
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
  {
    key: 'reply_strategy',
    label: policyScopeLabel('reply_strategy'),
    description: 'Owner 维护 · LIVE 回信策略（与编辑学习提案合并）。',
    ownerOnly: true,
  },
  {
    key: 'outcome_strategy',
    label: policyScopeLabel('outcome_strategy'),
    description: 'Owner 维护 · LIVE 合作结局指导（与复盘提案合并）。',
    ownerOnly: true,
  },
];

type Me = { id: number; email: string; role: 'owner' | 'operator' | 'viewer' };

export function PolicyEditorPage() {
  const env = useEnvStore((s) => s.env);
  const [me, setMe] = useState<Me | null>(null);
  const [tab, setTab] = useState<Tab>('company_style');
  const [policy, setPolicy] = useState<Policy | null>(null);
  const [draft, setDraft] = useState('');
  const [history, setHistory] = useState<Policy[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [preview, setPreview] = useState<{ version: number; content: string } | null>(null);

  useEffect(() => {
    api.get<Me>('/auth/me').then(setMe).catch((e) => setErr(e));
  }, []);

  const owner = tab === 'user_style' ? me?.id ?? null : null;

  const isEnvScoped = tab === 'reply_strategy' || tab === 'outcome_strategy';

  const refresh = useCallback(async () => {
    if (!me) return;
    try {
      const params = new URLSearchParams();
      if (tab === 'user_style') params.set('owner_user_id', String(me.id));
      if (isEnvScoped) params.set('env', env);
      const qs = params.toString() ? `?${params.toString()}` : '';
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
  }, [me, tab, env, isEnvScoped]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  function canWrite(): boolean {
    if (!me) return false;
    if (me.role === 'viewer') return false;
    if (tab === 'user_style') return true;
    if (isEnvScoped) return me.role === 'owner';
    return me.role === 'owner';
  }

  const visibleTabs = TABS.filter(
    (t) => !t.ownerOnly || me?.role === 'owner',
  );

  async function viewVersion(version: number) {
    if (!me) return;
    try {
      const params = new URLSearchParams();
      if (tab === 'user_style') params.set('owner_user_id', String(me.id));
      if (isEnvScoped) params.set('env', env);
      const qs = params.toString() ? `?${params.toString()}` : '';
      const resp = await api.get<{ policy: Policy | null }>(
        `/policies/${tab}/version/${version}${qs}`,
      );
      setPreview({ version, content: resp.policy?.content_md ?? '' });
    } catch (ex) {
      toast.error('加载版本失败', errorSummary(ex));
    }
  }

  async function rollback(version: number) {
    if (!me) return;
    const ok = await dialog.confirm({
      title: `回滚到 v${version}？`,
      description: '将以该历史版本内容生成一个新版本（不删除历史）。当前草稿会被覆盖为该版本内容。',
      confirmLabel: '确认回滚',
      cancelLabel: '取消',
      variant: 'danger',
    });
    if (!ok) return;
    setBusy(true);
    try {
      const body: Record<string, unknown> = { to_version: version };
      if (tab === 'user_style') body.owner_user_id = me.id;
      if (isEnvScoped) body.env = env;
      await api.post(`/policies/${tab}/rollback`, body);
      toast.success('已回滚', `已基于 v${version} 生成新版本`);
      setPreview(null);
      refresh();
    } catch (ex) {
      toast.error('回滚失败', errorSummary(ex));
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    if (!me) return;
    setBusy(true);
    setErr(null);
    try {
      const body: Record<string, unknown> = { content_md: draft };
      if (tab === 'user_style') body.owner_user_id = me.id;
      if (isEnvScoped) body.env = env;
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
        {visibleTabs.map((t) => (
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
        {visibleTabs.find((t) => t.key === tab)?.description}
        {isEnvScoped ? ` · 环境 ${env}` : ''}
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
                  'flex items-center gap-2 rounded p-1 ' +
                  (h.is_active ? 'bg-emerald-50 text-emerald-900' : 'text-slate-700')
                }
              >
                <span className="flex-1">
                  v{h.version} · <TimeAgo iso={h.updated_at} /> · {h.updated_by}{' '}
                  {h.is_active ? <strong>（当前）</strong> : ''}
                </span>
                <button
                  type="button"
                  onClick={() => viewVersion(h.version)}
                  className="rounded border border-slate-300 px-1.5 py-0.5 text-[11px] hover:bg-slate-100"
                >
                  查看
                </button>
                {canWrite() && !h.is_active && (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => rollback(h.version)}
                    className="rounded border border-amber-300 bg-amber-50 px-1.5 py-0.5 text-[11px] text-amber-800 hover:bg-amber-100 disabled:opacity-50"
                  >
                    回滚
                  </button>
                )}
              </li>
            ))}
          </ul>
          {preview && (
            <div className="mt-2 border-t border-slate-100 pt-2">
              <div className="mb-1 flex items-center gap-2 text-[11px] text-slate-500">
                <span>v{preview.version} 内容预览</span>
                <button
                  type="button"
                  onClick={() => setPreview(null)}
                  className="ml-auto underline"
                >
                  收起
                </button>
              </div>
              <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded border border-slate-200 bg-slate-50 p-2 text-[11px] text-slate-700">
                {preview.content || '(空)'}
              </pre>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
