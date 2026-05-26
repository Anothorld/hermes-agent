import { FormEvent, useEffect, useState } from 'react';
import { api } from '../api';
import { ErrorAlert } from '../components/feedback/ErrorAlert';
import { dialog } from '../components/dialogs/useDialog';
import { toast, useEnvStore, usePrefsStore } from '../lib/store';
import { errorSummary } from '../lib/errors';

type Me = { id: number; email: string; role: string };

export function SettingsPage() {
  const [me, setMe] = useState<Me | null>(null);
  const [form, setForm] = useState({ email: '', password: '', role: 'operator' });
  const [err, setErr] = useState<unknown>(null);
  const env = useEnvStore((s) => s.env);
  const showRaw = usePrefsStore((s) => s.showRawFactKeys);
  const setShowRaw = usePrefsStore((s) => s.setShowRawFactKeys);

  useEffect(() => {
    api.get<Me>('/auth/me').then(setMe).catch(() => undefined);
  }, []);

  const create = async (e: FormEvent) => {
    e.preventDefault();
    setErr(null);
    try {
      await api.post('/auth/users', form);
      toast.success(`已创建用户 ${form.email}`);
      setForm({ email: '', password: '', role: 'operator' });
    } catch (ex) {
      setErr(ex);
      toast.error('创建失败', errorSummary(ex));
    }
  };

  const wipe = async () => {
    const ok = await dialog.confirm({
      title: '清除 TEST 环境的全部 CAL 数据？',
      description: '此操作不可恢复，仅在 TEST 环境生效（LIVE 数据不受影响）。',
      confirmLabel: '确认清除',
      cancelLabel: '取消',
      variant: 'danger',
    });
    if (!ok) return;
    try {
      const r = await api.post<Record<string, number>>('/admin/wipe-test');
      toast.success('已清除 TEST 数据', JSON.stringify(r));
    } catch (ex) {
      setErr(ex);
      toast.error('清除失败', errorSummary(ex));
    }
  };

  return (
    <div className="space-y-6">
      <section className="rounded border bg-white p-3">
        <h2 className="mb-2 font-medium">登录身份</h2>
        {me ? (
          <div className="text-sm">
            {me.email} · 角色 = {me.role}
          </div>
        ) : (
          <div className="text-sm text-slate-500">加载中…</div>
        )}
      </section>

      <section className="rounded border bg-white p-3">
        <h2 className="mb-2 font-medium">偏好</h2>
        <label className="flex items-center gap-2 text-sm text-slate-700">
          <input
            type="checkbox"
            checked={showRaw}
            onChange={(e) => setShowRaw(e.target.checked)}
            className="h-4 w-4 rounded border-slate-300 text-emerald-600 focus:ring-emerald-500"
          />
          <span>开发者模式：在字段标签旁显示原始 fact_path</span>
        </label>
        <p className="mt-1 text-xs text-slate-500">
          默认关闭。开启后看板和详情页里的字段名旁会同时显示 namespace.key 原始名，方便排查后端问题。
        </p>
      </section>

      {me?.role === 'owner' && (
        <>
          <section className="rounded border bg-white p-3">
            <h2 className="mb-2 font-medium">创建用户</h2>
            <form onSubmit={create} className="grid grid-cols-4 gap-2 text-sm">
              <input
                placeholder="邮箱"
                type="email"
                value={form.email}
                onChange={(e) => setForm({ ...form, email: e.target.value })}
                className="rounded border px-2 py-1"
                required
              />
              <input
                placeholder="密码（≥8 位）"
                type="password"
                value={form.password}
                onChange={(e) => setForm({ ...form, password: e.target.value })}
                className="rounded border px-2 py-1"
                required
              />
              <select
                value={form.role}
                onChange={(e) => setForm({ ...form, role: e.target.value })}
                className="rounded border px-2 py-1"
              >
                <option value="owner">owner（管理员）</option>
                <option value="operator">operator（操作员）</option>
                <option value="viewer">viewer（只读）</option>
              </select>
              <button className="rounded bg-emerald-600 px-3 py-1 text-white">创建</button>
            </form>
          </section>

          <section className="rounded border border-red-200 bg-red-50 p-3">
            <h2 className="mb-2 font-medium text-red-800">危险区域</h2>
            <p className="mb-2 text-xs text-red-700">
              当前 env = <strong>{env}</strong>。下方按钮只会清除 TEST 数据，与 LIVE 无关。
            </p>
            <button onClick={wipe} className="rounded bg-red-600 px-3 py-1 text-white">
              清除 TEST 环境的全部 CAL 数据
            </button>
          </section>
        </>
      )}
      {!!err && <ErrorAlert error={err} />}
    </div>
  );
}
