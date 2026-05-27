import { Link } from 'react-router-dom';
import AgentTranscriptPanel from '../AgentTranscriptPanel';
import { formatAbsolute, formatRelativeAgo } from '../../lib/time';
import type { AgentSession } from './types';

type Props = {
  session: AgentSession | null;
  env: 'TEST' | 'LIVE';
};

export function SelectedSessionView({ session, env }: Props) {
  if (!session) {
    return (
      <div className="flex flex-1 items-center justify-center p-6 text-xs text-slate-500">
        从上方列表选择一个会话以查看 transcript。
      </div>
    );
  }
  const copySessionId = () => {
    if (typeof navigator !== 'undefined' && navigator.clipboard) {
      navigator.clipboard.writeText(session.session_id).catch(() => {});
    }
  };
  return (
    <div className="flex flex-1 min-h-0 flex-col gap-2 p-2">
      <div className="flex flex-col gap-1 rounded border border-slate-800 bg-slate-900/60 p-2 text-[11px]">
        <div className="flex items-center gap-2">
          <span className="truncate font-mono text-slate-200" title={session.session_id}>
            {session.session_id}
          </span>
          <button
            type="button"
            onClick={copySessionId}
            className="shrink-0 rounded border border-slate-700 px-1 text-[10px] text-slate-300 hover:bg-slate-800"
            title="复制 session_id"
          >
            copy
          </button>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-slate-400">
          <Link
            to={`/campaigns/${encodeURIComponent(session.campaign_id)}/transcript?env=${env}`}
            className="text-sky-400 hover:underline"
            title="在新页面打开 transcript"
          >
            campaign: {session.campaign_id}
          </Link>
          <span
            className={`rounded px-1 text-[10px] ${
              session.open
                ? 'bg-emerald-900/60 text-emerald-200'
                : 'bg-slate-800 text-slate-300'
            }`}
          >
            {session.open ? `LIVE · ${session.runs.length} runs` : `closed · ${session.runs.length} runs`}
          </span>
          <span title={formatAbsolute(session.last_activity_at)}>
            {formatRelativeAgo(session.last_activity_at)}
          </span>
        </div>
      </div>
      <div className="flex-1 min-h-0">
        {/* key forces a fresh mount on session switch so internal state
            (items, runs, openRuns) is reset even when two sessions
            share the same campaign_id. sessionId opts into the
            per-session history endpoint so closed sessions render
            their full hermes-persisted transcript instead of just the
            terminal output. runIds keeps live wrapped events
            constrained to this session's runs. */}
        <AgentTranscriptPanel
          key={session.session_id}
          campaignId={session.campaign_id}
          env={env}
          live
          variant="dock"
          sessionId={session.session_id}
          runIds={session.runs.map((r) => r.run_id)}
        />
      </div>
    </div>
  );
}
