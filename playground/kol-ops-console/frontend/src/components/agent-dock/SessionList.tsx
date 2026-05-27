import { SessionRow } from './SessionRow';
import type { AgentSession } from './types';

type Props = {
  sessions: AgentSession[];
  selectedId: string | null;
  loading: boolean;
  error: string | null;
  onSelect: (sessionId: string) => void;
  onRetry: () => void;
};

export function SessionList({ sessions, selectedId, loading, error, onSelect, onRetry }: Props) {
  if (error) {
    return (
      <div className="flex flex-col gap-2 p-3 text-xs text-rose-300">
        <span>加载失败: {error}</span>
        <button
          type="button"
          onClick={onRetry}
          className="self-start rounded border border-rose-700 px-2 py-1 text-rose-200 hover:bg-rose-900/40"
        >
          重试
        </button>
      </div>
    );
  }
  if (sessions.length === 0) {
    return (
      <div className="p-4 text-xs text-slate-500">
        {loading ? '加载中…' : '当前环境暂无 Agent 会话记录。启动一个 Campaign 或触发 Draft 即可看到运行。'}
      </div>
    );
  }
  return (
    <ul className="flex flex-col">
      {sessions.map((s) => (
        <li key={s.session_id} className="border-b border-slate-900/80 last:border-0">
          <SessionRow
            session={s}
            selected={s.session_id === selectedId}
            onClick={() => onSelect(s.session_id)}
          />
        </li>
      ))}
    </ul>
  );
}
