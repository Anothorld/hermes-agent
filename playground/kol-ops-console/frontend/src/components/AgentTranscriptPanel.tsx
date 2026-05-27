import { useEffect, useMemo, useRef, useState } from 'react';
import { api, streamLines } from '../api';
import { errorSummary } from '../lib/errors';
import { RUN_KIND_LABEL, RUN_KIND_TONE } from './agentRunStyles';

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
  // 'fullscreen' = fills the available height of the viewport
  // 'dock' = fills the available height of its flex parent (used by the
  //          global Agent Session Dock; the dock owns its own container
  //          height and just wants the transcript body to stretch)
  variant?: 'inline' | 'fullscreen' | 'dock';
  // When set, restrict the displayed runs registry and live items to
  // these run_ids — used by the global Agent Session Dock so two
  // sessions that share a campaign_id (e.g. outreach + draft refine on
  // the same CID) render distinct transcripts. Items WITH a runId are
  // filtered against this set; items without runId pass through ONLY
  // when they come from a session-scoped source (sessionId set).
  runIds?: string[] | null;
  // When set, the panel sources its initial history from the per-session
  // file via GET /campaigns/agent-sessions/{sid}/log instead of the
  // campaign-wide agent-log. This gives closed sessions their full
  // step-by-step transcript (hermes persists every message on disk per
  // session). The SSE stream is still campaign-scoped (multi-run
  // aggregator); we just ignore its snapshot.items field and rely on
  // the live wrapped events filtered through runIds.
  sessionId?: string | null;
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

function hmsFromIso(ts?: string): string {
  if (!ts) return '--:--:--';
  const d = ts.length > 10 ? new Date(ts) : new Date(Number(ts) * 1000);
  if (Number.isNaN(d.getTime())) return ts.slice(0, 8);
  return d.toTimeString().slice(0, 8);
}

function clip(s: string, max = 400): string {
  return s.length <= max ? s : `${s.slice(0, max)}…`;
}

function relativeAgo(ts?: string | null): string {
  if (!ts) return '';
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return '';
  const diffSec = Math.max(0, Math.round((Date.now() - d.getTime()) / 1000));
  if (diffSec < 60) return `${diffSec}s ago`;
  if (diffSec < 3600) return `${Math.round(diffSec / 60)}m ago`;
  return `${Math.round(diffSec / 3600)}h ago`;
}

export default function AgentTranscriptPanel({
  campaignId,
  env,
  live,
  variant = 'inline',
  runIds,
  sessionId,
}: Props) {
  const runIdFilter = useMemo(
    () => (runIds && runIds.length ? new Set(runIds) : null),
    [runIds],
  );
  // In session mode the initial GET hits a per-session endpoint whose
  // items are inherently session-scoped but lack per-message run_id
  // attribution. Those items should pass the runId filter; only items
  // WITH a runId get strictly checked against the set. Outside session
  // mode we keep the previous loose semantics (drop nothing when no
  // filter is set).
  const sessionScoped = sessionId != null && sessionId !== '';
  const [items, setItems] = useState<TranscriptItem[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [conn, setConn] = useState<'idle' | 'connecting' | 'live' | 'reconnecting' | 'closed'>('idle');
  const [retryAttempt, setRetryAttempt] = useState(0);
  const [runs, setRuns] = useState<RunRow[]>([]);
  // openRuns must drive UI (the "currently running" banner), so keep it in
  // both a ref (for stable mutation inside the SSE callback) and state (for
  // re-render). The ref is the source of truth; state is mirrored on
  // mutate.
  const openRuns = useRef<Set<string>>(new Set());
  const [openRunsTick, setOpenRunsTick] = useState(0);
  const bumpOpenRuns = () => setOpenRunsTick((n) => n + 1);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const stickyBottom = useRef<boolean>(true);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el || !stickyBottom.current) return;
    el.scrollTop = el.scrollHeight;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items.length, runIdFilter]);

  useEffect(() => {
    let alive = true;
    setItems([]);
    setErr(null);
    setRuns([]);
    openRuns.current = new Set();
    bumpOpenRuns();

    // Initial history fetch — session-scoped when sessionId is set
    // (reads ~/.hermes/profiles/.../session_{sid}.json so closed
    // sessions still render their full step-by-step transcript), else
    // campaign-scoped (legacy behavior for ProductDetailPage /
    // AgentTranscriptPage callers).
    const initialUrl = sessionId
      ? `/campaigns/agent-sessions/${encodeURIComponent(sessionId)}/log?env=${env}&limit=160`
      : `/campaigns/${encodeURIComponent(campaignId)}/agent-log?env=${env}&limit=160`;
    api
      .get<AgentLogResponse>(initialUrl)
      .then((r) => {
        if (!alive) return;
        setItems(r.items);
        setErr(null);
      })
      .catch((ex) => alive && setErr(errorSummary(ex)));

    if (!live) {
      setConn('idle');
      return () => {
        alive = false;
      };
    }

    setConn('connecting');
    setRetryAttempt(0);
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
          // Treat any run that does not yet have an ended_at as still
          // open. The aggregator will emit run.closed once the gateway
          // SSE finishes; until then we want the LIVE badge lit.
          openRuns.current = new Set(
            (snap.runs ?? [])
              .filter((r) => !r.ended_at)
              .map((r) => r.run_id),
          );
          bumpOpenRuns();
          setConn('live');
          // In session mode the snapshot.items are campaign-wide and
          // would clobber the session-scoped history we just fetched
          // from the per-session log endpoint. Skip them — live events
          // will keep arriving below with proper run_id attribution.
          if (
            !sessionScoped
            && Array.isArray(snap.items)
            && snap.items.length
          ) {
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
          bumpOpenRuns();
          return;
        }

        if (ev.event === 'run.closed' || ev.event === 'run.evicted' || ev.event === 'run.error') {
          const rid = String(rec.run_id ?? '');
          if (rid) {
            openRuns.current.delete(rid);
            bumpOpenRuns();
          }
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
        if (innerEvent === 'message.delta') {
          // Streaming assistant text — gateway fires one event per token.
          // Coalesce consecutive deltas into the last `assistant` row for
          // this run; otherwise long streams would create hundreds of
          // single-token rows and tank scroll performance.
          const delta = String(inner.delta ?? '');
          if (!delta) return;
          setItems((prev) => {
            if (prev.length > 0) {
              const last = prev[prev.length - 1];
              if (last.kind === 'assistant' && last.runId === runId) {
                const next = [...prev];
                next[next.length - 1] = {
                  ...last,
                  message: clip(last.message + delta, 8000),
                };
                return next;
              }
            }
            return [
              ...prev,
              decorate({
                ts: String(inner.timestamp ?? ''),
                kind: 'assistant',
                label: 'assistant',
                message: clip(delta, 8000),
              }),
            ];
          });
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
          // The gateway evicts per-run event ring buffers the moment a
          // run hits a terminal state, so the only thing the backend's
          // late-subscriber replay can hand us for a closed run is the
          // synthesized terminal frame (status + final output). Surface
          // both the status row AND the output text as separate items
          // so closed sessions render meaningful content under strict
          // session-scoped filtering rather than just a one-liner.
          const outputText = typeof inner.output === 'string' ? inner.output : '';
          const errVal = inner.error;
          const errText = errVal
            ? typeof errVal === 'string'
              ? errVal
              : (() => {
                  try {
                    return JSON.stringify(errVal);
                  } catch {
                    return String(errVal);
                  }
                })()
            : '';
          setItems((prev) => {
            const next = [
              ...prev,
              decorate({
                ts: String(inner.timestamp ?? ''),
                kind: innerEvent === 'run.failed' ? 'error' : 'done',
                label: innerEvent,
                message: innerEvent,
              }),
            ];
            if (outputText) {
              next.push(
                decorate({
                  ts: String(inner.timestamp ?? ''),
                  kind: 'assistant',
                  label: 'final',
                  message: clip(outputText, 8000),
                }),
              );
            }
            if (errText) {
              next.push(
                decorate({
                  ts: String(inner.timestamp ?? ''),
                  kind: 'error',
                  label: 'error',
                  message: clip(errText, 2000),
                }),
              );
            }
            return next;
          });
          openRuns.current.delete(runId);
          bumpOpenRuns();
          if (openRuns.current.size === 0) setConn('closed');
          return;
        }
      },
      (e) => {
        if (!alive) return;
        // Surface but do not stick the error — the SSE helper will
        // keep reconnecting in the background. We display the most
        // recent error so transient hiccups stay visible without
        // collapsing the panel.
        setErr(errorSummary(e));
      },
      {
        onState: (state, attempt) => {
          if (!alive) return;
          setRetryAttempt(attempt);
          if (state === 'open') {
            setConn('live');
            setErr(null);
          } else if (state === 'connecting') {
            setConn('connecting');
          } else if (state === 'reconnecting') {
            setConn('reconnecting');
          } else {
            setConn('closed');
          }
        },
      },
    );
    return () => {
      alive = false;
      handle.cancel();
    };
  // sessionId is in deps so swapping the source (campaign vs per-session
  // log) triggers a fresh fetch + new SSE subscribe. sessionScoped is
  // derived from sessionId so we don't list it separately.
  }, [campaignId, env, live, sessionId]);

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
    stickyBottom.current = distance < 80;
  };

  const indicator = useMemo(() => {
    if (conn === 'live') return { dot: 'bg-emerald-400 animate-pulse', text: 'LIVE', tone: 'text-emerald-300' };
    if (conn === 'connecting') return { dot: 'bg-amber-400 animate-pulse', text: 'connecting', tone: 'text-amber-300' };
    if (conn === 'reconnecting') return {
      dot: 'bg-amber-400 animate-pulse',
      text: `reconnecting (${retryAttempt})`,
      tone: 'text-amber-300',
    };
    if (conn === 'closed') return { dot: 'bg-slate-500', text: 'closed', tone: 'text-slate-400' };
    return { dot: 'bg-slate-500', text: 'idle', tone: 'text-slate-400' };
  }, [conn, retryAttempt]);

  const bodyHeight =
    variant === 'fullscreen'
      ? 'h-[calc(100vh-10rem)]'
      : variant === 'dock'
        ? 'flex-1 min-h-0'
        : 'h-72';

  // When the consumer (Agent Session Dock) hands us a session-scoped
  // run_id set, restrict the displayed runs registry to that set so a
  // campaign with multiple sessions still shows distinct rosters.
  const visibleRuns = useMemo(
    () => (runIdFilter ? runs.filter((r) => runIdFilter.has(r.run_id)) : runs),
    [runs, runIdFilter],
  );

  // Items with a `runId` are strictly filtered against `runIdFilter`.
  // Items without a `runId` are either:
  //   - kept when `sessionScoped` is true (they came from the per-session
  //     GET so they belong to this session by construction), or
  //   - dropped otherwise (they came from a campaign-wide synthesis and
  //     would leak across sessions sharing a campaign_id).
  const visibleItems = useMemo(() => {
    if (!runIdFilter) return items;
    return items.filter((it) => {
      if (it.runId != null) return runIdFilter.has(it.runId);
      return sessionScoped;
    });
  }, [items, runIdFilter, sessionScoped]);

  // A run is "currently active" when:
  //   - the SSE aggregator hasn't sent run.closed / run.evicted / run.error
  //     for it yet (tracked in openRuns), AND
  //   - the registry doesn't already have an ended_at timestamp.
  // We surface this set explicitly so the operator can tell "the agent is
  // typing right now" apart from "this transcript is finished".
  const activeRuns = useMemo(
    () => visibleRuns.filter(
      (r) => !r.ended_at && (live ? openRuns.current.has(r.run_id) : false),
    ),
    [visibleRuns, live, openRunsTick],
  );

  const rootLayout =
    variant === 'dock'
      ? 'flex h-full flex-col rounded border border-slate-800 bg-slate-900/95 p-2 shadow-inner'
      : 'rounded border border-slate-800 bg-slate-900/95 p-2 shadow-inner';

  return (
    <div className={rootLayout}>
      <div className="mb-1 flex items-center justify-between text-xs">
        <span className="font-medium text-slate-300">Agent transcript</span>
        <span className="flex items-center gap-2 text-slate-400">
          {visibleRuns.length > 0 && (
            <span className="flex items-center gap-1 font-mono text-[10px] text-slate-500">
              {visibleRuns.slice(0, 4).map((r) => {
                const isActive = activeRuns.some((a) => a.run_id === r.run_id);
                return (
                  <span
                    key={r.run_id}
                    className={`flex items-center gap-1 rounded px-1 py-[1px] ${RUN_KIND_TONE[r.kind] ?? 'bg-slate-800 text-slate-300'}`}
                    title={`${r.kind} · ${r.run_id}${isActive ? ' · live' : ''}`}
                  >
                    {isActive && (
                      <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-400" />
                    )}
                    {RUN_KIND_LABEL[r.kind] ?? r.kind}:{r.run_id.slice(0, 6)}
                  </span>
                );
              })}
              {visibleRuns.length > 4 && <span>+{visibleRuns.length - 4}</span>}
            </span>
          )}
          <span className={`flex items-center gap-1 ${indicator.tone}`}>
            <span className={`inline-block h-2 w-2 rounded-full ${indicator.dot}`} />
            {indicator.text}
          </span>
        </span>
      </div>
      {activeRuns.length > 0 && (
        <div className="mb-1 flex flex-wrap items-center gap-2 rounded border border-emerald-500/30 bg-emerald-500/10 px-2 py-1 text-[11px] text-emerald-200">
          <span className="inline-flex items-center gap-1 font-semibold">
            <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-emerald-400" />
            ● 当前正在 run 的对话 ({activeRuns.length})
          </span>
          {activeRuns.map((r) => (
            <span
              key={r.run_id}
              className="rounded bg-emerald-900/60 px-2 py-0.5 font-mono text-emerald-100"
              title={r.run_id}
            >
              {RUN_KIND_LABEL[r.kind] ?? r.kind} · {r.run_id.slice(0, 8)} · {relativeAgo(r.started_at)}
            </span>
          ))}
        </div>
      )}
      {live && activeRuns.length === 0 && conn === 'live' && (
        <div className="mb-1 rounded border border-slate-700 bg-slate-800/60 px-2 py-1 text-[11px] text-slate-400">
          监听中，但当前没有 active run — 任何新的 outreach / reply / draft / refine run 会自动显示在这里。
        </div>
      )}
      <div
        ref={scrollRef}
        onScroll={onScroll}
        className={`overflow-y-auto rounded bg-slate-950 p-2 font-mono text-[11px] leading-5 ${bodyHeight}`}
      >
        {err && <div className="text-rose-300">stream error: {err}</div>}
        {visibleItems.length === 0 && !err && <div className="text-slate-500">[no agent activity yet]</div>}
        {visibleItems.map((item, idx) => {
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
