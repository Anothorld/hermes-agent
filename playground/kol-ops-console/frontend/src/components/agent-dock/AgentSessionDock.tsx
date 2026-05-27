import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, getToken } from '../../api';
import { errorSummary } from '../../lib/errors';
import { useAgentDockStore, useEnvStore } from '../../lib/store';
import { SelectedSessionView } from './SelectedSessionView';
import { SessionList } from './SessionList';
import type { AgentSession, AgentSessionsResponse } from './types';

const POLL_MS = 10_000;

export function AgentSessionDock() {
  const open = useAgentDockStore((s) => s.open);
  const selectedSessionId = useAgentDockStore((s) => s.selectedSessionId);
  const toggle = useAgentDockStore((s) => s.toggle);
  const setOpen = useAgentDockStore((s) => s.setOpen);
  const selectSession = useAgentDockStore((s) => s.selectSession);
  const env = useEnvStore((s) => s.env);

  const [sessions, setSessions] = useState<AgentSession[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Track the first load per env so the stale-selection effect doesn't
  // clobber the persisted selectedSessionId before the first response.
  const firstLoadDone = useRef(false);

  const fetchSessions = useCallback(async () => {
    if (!getToken()) return;
    setLoading(true);
    try {
      const r = await api.get<AgentSessionsResponse>(
        `/campaigns/agent-sessions?env=${env}&limit=200`,
      );
      setSessions(r.sessions ?? []);
      setError(null);
    } catch (ex) {
      setError(errorSummary(ex));
    } finally {
      setLoading(false);
      firstLoadDone.current = true;
    }
  }, [env]);

  useEffect(() => {
    // Reset the first-load flag whenever env flips so the stale-selection
    // effect waits for fresh data before clearing the persisted choice.
    firstLoadDone.current = false;
    fetchSessions();
    const id = window.setInterval(fetchSessions, POLL_MS);
    return () => window.clearInterval(id);
  }, [env, fetchSessions]);

  // Self-guard: this component is mounted at the App root next to
  // DialogHost/ToastHost (which live outside RequireAuth). Hide on
  // login / when the token is cleared. Hooks above always run so we
  // stay compliant with the Rules of Hooks.
  if (!getToken()) return null;

  // Drop the persisted selection if the session disappears (env switch,
  // registry purge). Wait for the first fetch of the new env to settle
  // so we don't drop on the empty initial state.
  useEffect(() => {
    if (!selectedSessionId) return;
    if (!firstLoadDone.current) return;
    if (!sessions.some((s) => s.session_id === selectedSessionId)) {
      selectSession(null);
    }
  }, [sessions, selectedSessionId, selectSession]);

  const selectedSession = useMemo(
    () => sessions.find((s) => s.session_id === selectedSessionId) ?? null,
    [sessions, selectedSessionId],
  );

  const openSessionCount = useMemo(
    () => sessions.reduce((acc, s) => (s.open ? acc + 1 : acc), 0),
    [sessions],
  );

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="fixed right-0 top-1/3 z-40 flex h-32 w-7 cursor-pointer items-center justify-center gap-2 rounded-l border-y border-l border-slate-700 bg-slate-900 text-[11px] font-medium text-slate-200 shadow-lg hover:bg-slate-800"
        style={{ writingMode: 'vertical-rl' }}
        title="展开 Agent Sessions"
      >
        {openSessionCount > 0 && (
          <span className="inline-block h-2 w-2 rounded-full bg-emerald-400 animate-pulse" />
        )}
        Agent Sessions
        {openSessionCount > 0 && (
          <span className="text-emerald-300">·{openSessionCount}</span>
        )}
      </button>
    );
  }

  return (
    <div className="fixed right-0 top-12 bottom-4 z-40 flex w-[480px] flex-col rounded-l-lg border-y border-l border-slate-700 bg-slate-950 shadow-2xl">
      <div className="flex items-center justify-between border-b border-slate-800 px-3 py-2">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-slate-100">Agent Sessions</span>
          <span
            className={`rounded px-1 text-[10px] ${
              env === 'LIVE'
                ? 'bg-rose-900/50 text-rose-200'
                : 'bg-sky-900/50 text-sky-200'
            }`}
          >
            {env}
          </span>
          {openSessionCount > 0 && (
            <span className="flex items-center gap-1 text-[11px] text-emerald-300">
              <span className="inline-block h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse" />
              {openSessionCount} live
            </span>
          )}
        </div>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={fetchSessions}
            className="rounded border border-slate-700 px-2 py-0.5 text-[11px] text-slate-300 hover:bg-slate-800"
            title="刷新会话列表"
          >
            ↻
          </button>
          <button
            type="button"
            onClick={toggle}
            className="rounded border border-slate-700 px-2 py-0.5 text-[11px] text-slate-300 hover:bg-slate-800"
            title="收起"
          >
            ›
          </button>
        </div>
      </div>
      <div className="flex max-h-[40%] overflow-y-auto border-b border-slate-800">
        <SessionList
          sessions={sessions}
          selectedId={selectedSessionId}
          loading={loading}
          error={error}
          onSelect={selectSession}
          onRetry={fetchSessions}
        />
      </div>
      <SelectedSessionView session={selectedSession} env={env} />
    </div>
  );
}
