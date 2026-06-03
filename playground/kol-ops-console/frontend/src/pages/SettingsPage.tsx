import { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api, API_BASE } from '../api';
import { getToken } from '../api';

type GoogleStatus = {
  connected: boolean;
  google_email?: string | null;
  connected_at?: string | null;
  scopes?: string[];
};

export function SettingsPage() {
  const [search] = useSearchParams();
  const [status, setStatus] = useState<GoogleStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const r = await api.get<GoogleStatus>('/auth/google/status');
      setStatus(r);
    } catch {
      setStatus({ connected: false });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    const g = search.get('google');
    if (g === 'connected') {
      setMsg('Gmail 已连接。');
      refresh();
    } else if (g === 'error') {
      setMsg(`Gmail 连接失败：${search.get('reason') || 'unknown'}`);
    }
  }, [search, refresh]);

  const connect = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const r = await api.get<{ auth_url: string }>('/auth/google/start');
      window.location.href = r.auth_url;
    } catch (ex) {
      setMsg(String(ex));
      setBusy(false);
    }
  };

  const disconnect = async () => {
    setBusy(true);
    setMsg(null);
    try {
      await api.delete('/auth/google');
      await refresh();
      setMsg('已断开 Gmail。');
    } catch (ex) {
      setMsg(String(ex));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto max-w-lg space-y-6">
      <h1 className="text-lg font-semibold">设置</h1>

      <section className="rounded border border-slate-200 bg-white p-4">
        <h2 className="text-sm font-medium text-slate-800">Gmail 账号</h2>
        <p className="mt-1 text-xs text-slate-600">
          每位操作员连接自己的 Gmail。本 campaign 首次发信的操作员将成为该 KOL 的邮箱负责人。
        </p>

        {loading ? (
          <p className="mt-3 text-sm text-slate-500">加载中…</p>
        ) : status?.connected ? (
          <div className="mt-3 space-y-2 text-sm">
            <div>
              <span className="text-slate-500">已连接：</span>
              <span className="font-mono text-slate-900">{status.google_email}</span>
            </div>
            {status.connected_at && (
              <div className="text-xs text-slate-500">连接于 {status.connected_at}</div>
            )}
            <button
              type="button"
              disabled={busy}
              onClick={disconnect}
              className="rounded border border-rose-300 bg-rose-50 px-3 py-1.5 text-sm text-rose-800 hover:bg-rose-100 disabled:opacity-50"
            >
              断开连接
            </button>
          </div>
        ) : (
          <div className="mt-3">
            <button
              type="button"
              disabled={busy}
              onClick={connect}
              className="rounded bg-sky-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-sky-700 disabled:opacity-50"
            >
              连接 Gmail
            </button>
          </div>
        )}

        {msg && (
          <p className="mt-2 text-xs text-slate-700">{msg}</p>
        )}

        <p className="mt-3 text-[10px] text-slate-400">
          OAuth 回调地址需在 Google Cloud Console 中注册为：
          {' '}
          <span className="font-mono">{API_BASE}/auth/google/callback</span>
        </p>
      </section>

      <section className="rounded border border-slate-100 bg-slate-50 p-3 text-xs text-slate-600">
        <div>API: {API_BASE}</div>
        <div>登录态: {getToken() ? '已登录' : '未登录'}</div>
      </section>
    </div>
  );
}
