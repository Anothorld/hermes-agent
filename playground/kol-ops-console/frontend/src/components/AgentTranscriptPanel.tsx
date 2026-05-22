import { useEffect, useMemo, useRef, useState } from 'react';
import { api, streamLines } from '../api';

export type TranscriptItem = {
  index?: number;
  ts?: string;
  level?: string;
  label?: string;
  kind:
    | 'user'
    | 'assistant'
    | 'tool_call'
    | 'tool_result'
    | 'model'
    | 'tool'
    | 'reasoning'
    | 'error'
    | 'done'
    | 'info';
  message: string;
  // Origin run (added when the panel is fed from the multi-run aggregator).
  // Lets the UI show a per-line badge so operators can tell outreach lines
  // apart from reply-dispatcher / draft-preview / resume lines.
  runId?: string;
  runKind?: 'outreach' | 'reply' | 'draft' | 'resume';
  // Set when a `tool.completed` event arrives for an earlier `tool.started`
  // row — populated as `✓ 1.2s` / `✗ error` and rendered as a trailing tag.
  completion?: { ok: boolean; duration: number };
};

type AgentLogResponse = {
  campaign_id: string;
  env: string;
  source: string;
  items: TranscriptItem[];
};

type RunRow = {
  run_id: string;
  kind: 'outreach' | 'reply' | 'draft' | 'resume';
  session_id?: string | null;
  started_at: string;
  ended_at?: string | null;
};

type SnapshotEvent = {
  campaign_id: string;
  env: string;
  items: TranscriptItem[];
  runs: RunRow[];
};

type WrappedEvent = {
  run_id: string;
  kind: 'outreach' | 'reply' | 'draft' | 'resume';
  event: string;
  payload: Record<string, unknown>;
};

type Props = {
  campaignId: string;
  env: string;
  live: boolean;
  // 'inline' = card body with fixed 18rem height (default)
  // 'fullscreen' = fills the available height
  variant?: 'inline' | 'fullscreen';
};

const KIND_TONE: Record<TranscriptItem['kind'], string> = {
  user: 'text-slate-300',
  assistant: 'text-emerald-300',
  tool_call: 'text-sky-300',
  tool: 'text-sky-300',
  tool_result: 'text-sky-200/80',
  model: 'text-violet-300',
  reasoning: 'text-violet-300/90 italic',
  error: 'text-rose-300',
  done: 'text-emerald-400',
  info: 'text-slate-400',
};

const KIND_PREFIX: Record<TranscriptItem['kind'], string> = {
  user: '›',
  assistant: '▌',
  tool_call: '⚙',
  tool: '⚙',
  tool_result: '↵',
  model: '∴',
  reasoning: '…',
  error: '✗',
  done: '✓',
  info: 'ⓘ',
};

const RUN_KIND_LABEL: Record<NonNullable<TranscriptItem['runKind']>, string> = {
  outreach: 'outreach',
  reply: 'reply',
  draft: 'draft',
  resume: 'resume',
};

const RUN_KIND_TONE: Record<NonNullable<TranscriptItem['runKind']>, string> = {
  outreach: 'bg-emerald-900/60 text-emerald-200',
  reply: 'bg-sky-900/60 text-sky-200',
  draft: 'bg-amber-900/60 text-amber-200',
  resume: 'bg-violet-900/60 text-violet-200',
};

function hmsFromIso(ts?: string): string {
  if (!ts) return '--:--:--';
  const d = ts.length > 10 ? new Date(ts) : new Date(Number(ts) * 1000);
  if (Number.isNaN(d.getTime())) return ts.slice(0, 8);
  return d.toTimeString().slice(0, 8);
}

function clip(s: string, max = 400): string {
  return s.length <= max ? s : `${s.slice(0, max)}…`;
}

export default function AgentTranscriptPanel({ campaignId, env, live, variant = 'inline' }: Props) {
  const [items, setItems] = useState<TranscriptItem[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [conn, setConn] = useState<'idle' | 'connecting' | 'live' | 'closed'>('idle');
  const [runs, setRuns] = useState<RunRow[]>([]);
  const openRuns = useRef<Set<string>>(new Set());
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const stickyBottom = useRef<boolean>(true);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el || !stickyBottom.current) return;
    el.scrollTop = el.scrollHeight;
  }, [items.length]);

  useEffect(() => {
    let alive = true;
    setItems([]);
    setErr(null);
    setRuns([]);
    openRuns.current = new Set();

    api
      .get<AgentLogResponse>(
        `/campaigns/${encodeURIComponent(campaignId)}/agent-log?env=${env}&limit=160`,
      )
      .then((r) => {
        if (!alive) return;
        setItems(r.items);
        setErr(null);
      })
      .catch((ex) => alive && setErr(String(ex)));

    if (!live) {
      setConn('idle');
      return () => {
        alive = false;
      };
    }

    setConn('connecting');
    const handle = streamLines(
      `/campaigns/${encodeURIComponent(campaignId)}/agent-stream?env=${env}&limit=160`,
      (ev) => {
        if (!alive) return;
        let parsed: unknown = null;
        try {
          parsed = JSON.parse(ev.data);
        } catch {
          return;
        }
        const rec = (parsed ?? {}) as Record<string, unknown>;

        if (ev.event === 'snapshot') {
          const snap = rec as unknown as SnapshotEvent;
          setRuns(snap.runs ?? []);
          openRuns.current = new Set((snap.runs ?? []).map((r) => r.run_id));
          setConn('live');
          if (Array.isArray(snap.items) && snap.items.length) {
            setItems(snap.items);
          }
          return;
        }

        if (ev.event === 'run.added') {
          const r = rec as unknown as { run_id: string; kind: RunRow['kind']; started_at: string };
          setRuns((prev) => (prev.find((x) => x.run_id === r.run_id) ? prev : [
            { run_id: r.run_id, kind: r.kind, started_at: r.started_at },
            ...prev,
          ]));
          openRuns.current.add(r.run_id);
          return;
        }

        if (ev.event === 'run.closed' || ev.event === 'run.evicted' || ev.event === 'run.error') {
          const rid = String(rec.run_id ?? '');
          if (rid) openRuns.current.delete(rid);
          if (openRuns.current.size === 0) setConn('closed');
          return;
        }

        // Every other frame is a wrapped gateway event of shape
        // { run_id, kind, event, payload: {...gateway frame...} }.
        const wrapped = rec as unknown as WrappedEvent;
        if (!wrapped || !wrapped.event) return;
        const inner = (wrapped.payload ?? {}) as Record<string, unknown>;
        const innerEvent = wrapped.event;
        const runId = wrapped.run_id;
        const runKind = wrapped.kind;

        const decorate = (item: Omit<TranscriptItem, 'runId' | 'runKind'>): TranscriptItem => ({
          ...item,
          runId,
          runKind,
        });

        if (innerEvent === 'tool.started') {
          const tool = String(inner.tool ?? 'tool');
          const preview = String(inner.preview ?? '');
          setItems((prev) => [
            ...prev,
            decorate({
              ts: String(inner.timestamp ?? ''),
              kind: 'tool_call',
              label: tool,
              message: `${tool}${preview ? `(${clip(preview, 280)})` : '()'}`,
            }),
          ]);
          return;
        }
        if (innerEvent === 'tool.completed') {
          const tool = String(inner.tool ?? 'tool');
          const duration = Number(inner.duration ?? 0);
          const isError = Boolean(inner.error);
          setItems((prev) => {
            for (let i = prev.length - 1; i >= 0; i--) {
              if (
                prev[i].kind === 'tool_call' &&
                prev[i].label === tool &&
                prev[i].runId === runId &&
                !prev[i].completion
              ) {
                const next = [...prev];
                next[i] = { ...next[i], completion: { ok: !isError, duration } };
                return next;
              }
            }
            return [
              ...prev,
              decorate({
                ts: String(inner.timestamp ?? ''),
                kind: isError ? 'error' : 'done',
                label: tool,
                message: `${tool} ${isError ? '✗ error' : `✓ ${duration.toFixed(2)}s`}`,
              }),
            ];
          });
          return;
        }
        if (innerEvent === 'reasoning.available') {
          const text = String(inner.text ?? '');
          if (!text) return;
          setItems((prev) => [
            ...prev,
            decorate({
              ts: String(inner.timestamp ?? ''),
              kind: 'reasoning',
              label: 'reasoning',
              message: clip(text, 2000),
            }),
          ]);
          return;
        }
        if (innerEvent === 'approval.request' || innerEvent === 'approval.responded') {
          setItems((prev) => [
            ...prev,
            decorate({
              ts: String(inner.timestamp ?? ''),
              kind: 'info',
              label: innerEvent,
              message: clip(JSON.stringify(inner), 400),
            }),
          ]);
          return;
        }
        if (innerEvent === 'run.completed' || innerEvent === 'run.failed' || innerEvent === 'run.cancelled') {
          setItems((prev) => [
            ...prev,
            decorate({
              ts: String(inner.timestamp ?? ''),
              kind: innerEvent === 'run.failed' ? 'error' : 'done',
              label: innerEvent,
              message: innerEvent,
            }),
          ]);
          openRuns.current.delete(runId);
          if (openRuns.current.size === 0) setConn('closed');
          return;
        }
      },
      (e) => {
        if (!alive) return;
        setErr(String(e));
        setConn('closed');
      },
    );
    return () => {
      alive = false;
      handle.cancel();
    };
  }, [campaignId, env, live]);

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
    stickyBottom.current = distance < 80;
  };

  const indicator = useMemo(() => {
    if (conn === 'live') return { dot: 'bg-emerald-400 animate-pulse', text: 'LIVE', tone: 'text-emerald-300' };
    if (conn === 'connecting') return { dot: 'bg-amber-400 animate-pulse', text: 'connecting', tone: 'text-amber-300' };
    if (conn === 'closed') return { dot: 'bg-slate-500', text: 'closed', tone: 'text-slate-400' };
    return { dot: 'bg-slate-500', text: 'idle', tone: 'text-slate-400' };
  }, [conn]);

  const bodyHeight = variant === 'fullscreen' ? 'h-[calc(100vh-10rem)]' : 'h-72';

  return (
    <div className="rounded border border-slate-800 bg-slate-900/95 p-2 shadow-inner">
      <div className="mb-1 flex items-center justify-between text-xs">
        <span className="font-medium text-slate-300">Agent transcript</span>
        <span className="flex items-center gap-2 text-slate-400">
          {runs.length > 0 && (
            <span className="flex items-center gap-1 font-mono text-[10px] text-slate-500">
              {runs.slice(0, 4).map((r) => (
                <span
                  key={r.run_id}
                  className={`rounded px-1 py-[1px] ${RUN_KIND_TONE[r.kind] ?? 'bg-slate-800 text-slate-300'}`}
                  title={`${r.kind} · ${r.run_id}`}
                >
                  {RUN_KIND_LABEL[r.kind] ?? r.kind}:{r.run_id.slice(0, 6)}
                </span>
              ))}
              {runs.length > 4 && <span>+{runs.length - 4}</span>}
            </span>
          )}
          <span className={`flex items-center gap-1 ${indicator.tone}`}>
            <span className={`inline-block h-2 w-2 rounded-full ${indicator.dot}`} />
            {indicator.text}
          </span>
        </span>
      </div>
      <div
        ref={scrollRef}
        onScroll={onScroll}
        className={`overflow-y-auto rounded bg-slate-950 p-2 font-mono text-[11px] leading-5 ${bodyHeight}`}
      >
        {err && <div className="text-rose-300">stream error: {err}</div>}
        {items.length === 0 && !err && <div className="text-slate-500">[no agent activity yet]</div>}
        {items.map((item, idx) => {
          const tone = KIND_TONE[item.kind] || 'text-slate-300';
          const prefix = KIND_PREFIX[item.kind] || '·';
          const runBadgeTone = item.runKind ? RUN_KIND_TONE[item.runKind] : 'bg-slate-800 text-slate-400';
          return (
            <div key={`${item.index ?? item.ts ?? 'row'}-${idx}`} className="flex gap-2">
              <span className="w-20 shrink-0 text-slate-600">{hmsFromIso(item.ts)}</span>
              {item.runKind ? (
                <span
                  className={`shrink-0 rounded px-1 text-[9px] uppercase tracking-wide ${runBadgeTone}`}
                  title={item.runId}
                >
                  {RUN_KIND_LABEL[item.runKind]}
                </span>
              ) : (
                <span className="w-12 shrink-0" />
              )}
              <span className={`shrink-0 ${tone}`}>{prefix}</span>
              <span className={`min-w-0 flex-1 whitespace-pre-wrap break-words ${tone}`}>
                {item.label && item.kind !== 'tool_call' && (
                  <span className="mr-1 text-slate-500">[{item.label}]</span>
                )}
                {item.message}
                {item.completion && (
                  <span
                    className={`ml-2 ${
                      item.completion.ok ? 'text-emerald-400' : 'text-rose-400'
                    }`}
                  >
                    {item.completion.ok ? `✓ ${item.completion.duration.toFixed(2)}s` : '✗ error'}
                  </span>
                )}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
