import { formatAbsolute, formatRelativeAgo } from '../../lib/time';
import { RUN_KIND_LABEL, RUN_KIND_TONE } from '../agentRunStyles';
import type { AgentSession } from './types';

type Props = {
  session: AgentSession;
  selected: boolean;
  onClick: () => void;
};

// Parse a session_id like "kol-campaign:TEST:CID-42" into
// { namespace: 'kol-campaign', tail: 'CID-42' }. Falls back to the full
// string as the tail for `run:{run_id}` pseudo-sessions and anything
// unexpected.
function parseSessionLabel(sid: string): { namespace: string | null; tail: string } {
  const parts = sid.split(':');
  if (parts.length >= 3 && (parts[1] === 'TEST' || parts[1] === 'LIVE')) {
    return { namespace: parts[0], tail: parts.slice(2).join(':') };
  }
  if (parts.length >= 2 && parts[0] === 'run') {
    return { namespace: 'run', tail: parts.slice(1).join(':') };
  }
  return { namespace: null, tail: sid };
}

const NAMESPACE_TONE: Record<string, string> = {
  'kol-campaign': 'bg-emerald-900/50 text-emerald-200',
  'kol-campaign-draft': 'bg-amber-900/50 text-amber-200',
  'kol-email-discover': 'bg-sky-900/50 text-sky-200',
  run: 'bg-slate-800 text-slate-300',
};

export function SessionRow({ session, selected, onClick }: Props) {
  const { namespace, tail } = parseSessionLabel(session.session_id);
  const nsTone = namespace ? NAMESPACE_TONE[namespace] ?? 'bg-slate-800 text-slate-300' : '';
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex w-full items-center gap-2 border-l-2 px-3 py-2 text-left transition-colors hover:bg-slate-900 ${
        selected ? 'border-emerald-400 bg-slate-900' : 'border-transparent'
      }`}
      title={session.session_id}
    >
      <span
        className={`inline-block h-2 w-2 shrink-0 rounded-full ${
          session.open ? 'bg-emerald-400 animate-pulse' : 'bg-slate-600'
        }`}
      />
      <span className="flex min-w-0 flex-1 flex-col">
        <span className="flex items-center gap-1 min-w-0">
          {namespace && (
            <span className={`shrink-0 rounded px-1 text-[9px] uppercase tracking-wide ${nsTone}`}>
              {namespace}
            </span>
          )}
          <span className="truncate text-xs font-medium text-slate-100">{tail}</span>
        </span>
        <span className="mt-0.5 flex flex-wrap items-center gap-1 text-[10px]">
          {/* Campaign attribution is always explicit so operators can
              tell which campaign a session belongs to — sessions from
              different campaigns sit side-by-side in this list. */}
          <span
            className="shrink-0 rounded bg-slate-800 px-1 font-mono text-slate-300"
            title={`campaign · ${session.campaign_id}`}
          >
            cid · {session.campaign_id}
          </span>
          {session.kinds.slice(0, 4).map((k) => (
            <span
              key={k}
              className={`rounded px-1 ${RUN_KIND_TONE[k] ?? 'bg-slate-800 text-slate-300'}`}
            >
              {RUN_KIND_LABEL[k] ?? k}
            </span>
          ))}
          {session.kinds.length > 4 && (
            <span className="text-slate-500">+{session.kinds.length - 4}</span>
          )}
        </span>
      </span>
      <span
        className="shrink-0 text-[10px] text-slate-500"
        title={formatAbsolute(session.last_activity_at)}
      >
        {formatRelativeAgo(session.last_activity_at)}
      </span>
    </button>
  );
}
