import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, ApiError, getToken } from '../api';
import { errorSummary } from '../lib/errors';
import { useGatewayApprovalDockStore, toast } from '../lib/store';
import { useLiveEvents } from '../useLiveEvents';

// Discriminated payload that lives in ``_pending`` rows on the backend
// watcher and arrives over the websocket. Mirrors the type union in
// useLiveEvents.ts; redeclared here so the hook contract reads cleanly
// at call sites without forcing consumers to import the WS-frame types.

export type ApprovalChoice = 'once' | 'session' | 'always' | 'deny';

export type GatewayApproval = {
  run_id: string;
  campaign_id: string | null;
  kind: string;
  command: string;
  description: string;
  pattern_key: string;
  pattern_keys?: string[];
  choices: ApprovalChoice[];
  source: 'gateway';
  captured_at: string | number;
};

type SnapshotResponse = {
  approvals: GatewayApproval[];
  seq: number;
};

type ResolveResponse = {
  run_id: string;
  choice: ApprovalChoice;
  resolved: number;
};

type State = {
  /** Pending approvals sorted oldest-first so newest sits at the bottom
   *  of the dock list (matches operator scanning behaviour). */
  items: GatewayApproval[];
  /** Run IDs whose resolve call is in flight. Buttons disable while
   *  the request is pending so a panicked operator can't double-fire. */
  inflight: Set<string>;
  /** Per-row error surfaced underneath an entry on a failed resolve. */
  errors: Map<string, string>;
  /** Initial snapshot loaded — drives the empty-state vs loading
   *  rendering decision in the dock. */
  loaded: boolean;
};

const EMPTY_STATE: State = {
  items: [],
  inflight: new Set<string>(),
  errors: new Map<string, string>(),
  loaded: false,
};

/**
 * Subscribe to the global gateway-approval channel.
 *
 * - Fetches the snapshot on mount (so a page reload while approvals are
 *   pending re-populates the dock immediately).
 * - Listens to the existing websocket multiplex (``/ws``) for
 *   ``gateway_approvals`` frames; the backend watcher broadcasts
 *   request/responded/cleared events with a monotonic ``seq``.
 * - Auto-opens the dock when a *new* run_id appears (snapshot or live).
 *   Manual collapse is preserved for non-new events (the user knows the
 *   pending entries are still there; we don't fight them by re-popping
 *   on every progress update).
 * - ``resolve(run_id, choice)`` POSTs to the proxy endpoint; on success
 *   the row is optimistically removed (the eventual
 *   ``gateway_approval.responded`` from the watcher is a no-op).
 */
export function useGatewayApprovals() {
  const [state, setState] = useState<State>(EMPTY_STATE);
  const lastSeqRef = useRef(0);
  const open = useGatewayApprovalDockStore((s) => s.open);
  const setOpen = useGatewayApprovalDockStore((s) => s.setOpen);
  const toggle = useGatewayApprovalDockStore((s) => s.toggle);

  // Snapshot fetch — runs once on mount per auth scope.  If the token
  // changes (logout → login), the wrapped api.get triggers a redirect to
  // /login and the hook unmounts; we don't need to thrash here.
  useEffect(() => {
    if (!getToken()) {
      setState(EMPTY_STATE);
      return;
    }
    let cancelled = false;
    api
      .get<SnapshotResponse>('/gateway-approvals')
      .then((res) => {
        if (cancelled) return;
        lastSeqRef.current = res.seq ?? 0;
        const sorted = [...res.approvals].sort((a, b) =>
          String(a.captured_at).localeCompare(String(b.captured_at)),
        );
        setState({
          items: sorted,
          inflight: new Set<string>(),
          errors: new Map<string, string>(),
          loaded: true,
        });
        if (sorted.length > 0) setOpen(true);
      })
      .catch((ex) => {
        if (cancelled) return;
        // 401 is already handled by the api wrapper (redirect to login).
        if (!(ex instanceof ApiError && ex.status === 401)) {
          // eslint-disable-next-line no-console -- best-effort visibility
          console.warn('gateway-approvals snapshot failed:', errorSummary(ex));
        }
        setState((s) => ({ ...s, loaded: true }));
      });
    return () => {
      cancelled = true;
    };
  }, [setOpen]);

  useLiveEvents((frame) => {
    if (frame.type !== 'gateway_approvals') return;
    for (const ev of frame.items) {
      // Drop stale events that arrived after the snapshot fetch already
      // showed a fresher state. seq is monotonic on the backend.
      if (typeof ev.seq === 'number') {
        if (ev.seq <= lastSeqRef.current) continue;
        lastSeqRef.current = ev.seq;
      }
      if (ev.event === 'gateway_approval.request') {
        setState((prev) => {
          const existed = prev.items.some((it) => it.run_id === ev.run_id);
          const nextItems = existed
            ? prev.items.map((it) =>
                it.run_id === ev.run_id ? { ...it, ...itemFromWire(ev) } : it,
              )
            : [...prev.items, itemFromWire(ev)];
          if (!existed) {
            setOpen(true);
            toast.info(
              'Agent 需要命令审批',
              '有一条终端命令被安全策略拦住。请在右侧「命令待批」面板选择允许或拒绝。',
            );
          }
          // Drop any prior error row for the same run (a fresh request
          // means the previous failed resolve is stale).
          const nextErrors = new Map(prev.errors);
          nextErrors.delete(ev.run_id);
          return {
            ...prev,
            items: nextItems,
            errors: nextErrors,
          };
        });
      } else {
        // responded | cleared — remove the row in both cases.
        setState((prev) => ({
          ...prev,
          items: prev.items.filter((it) => it.run_id !== ev.run_id),
          inflight: removeFromSet(prev.inflight, ev.run_id),
          errors: removeFromMap(prev.errors, ev.run_id),
        }));
      }
    }
  });

  const resolve = useCallback(
    async (runId: string, choice: ApprovalChoice) => {
      setState((prev) => ({
        ...prev,
        inflight: addToSet(prev.inflight, runId),
        errors: removeFromMap(prev.errors, runId),
      }));
      try {
        await api.post<ResolveResponse>(
          `/gateway-approvals/${encodeURIComponent(runId)}/resolve`,
          { choice },
        );
        setState((prev) => ({
          ...prev,
          items: prev.items.filter((it) => it.run_id !== runId),
          inflight: removeFromSet(prev.inflight, runId),
        }));
      } catch (ex) {
        const msg = errorSummary(ex);
        // 409 = upstream says "no pending approval" — treat as success
        // (the row was cleared by another path; just remove it).
        if (ex instanceof ApiError && (ex.status === 409 || ex.status === 404)) {
          setState((prev) => ({
            ...prev,
            items: prev.items.filter((it) => it.run_id !== runId),
            inflight: removeFromSet(prev.inflight, runId),
          }));
          return;
        }
        setState((prev) => ({
          ...prev,
          inflight: removeFromSet(prev.inflight, runId),
          errors: setInMap(prev.errors, runId, msg),
        }));
        toast.error('审批操作失败', msg);
      }
    },
    [],
  );

  const count = state.items.length;

  return useMemo(
    () => ({
      items: state.items,
      inflight: state.inflight,
      errors: state.errors,
      loaded: state.loaded,
      count,
      open,
      setOpen,
      toggle,
      resolve,
    }),
    [state, count, open, setOpen, toggle, resolve],
  );
}

// ---------------------------------------------------------------------- helpers

function itemFromWire(ev: {
  run_id: string;
  campaign_id: string | null;
  kind: string;
  command: string;
  description: string;
  pattern_key: string;
  pattern_keys?: string[];
  choices: ApprovalChoice[];
  source: 'gateway';
  captured_at: string | number;
}): GatewayApproval {
  return {
    run_id: ev.run_id,
    campaign_id: ev.campaign_id,
    kind: ev.kind,
    command: ev.command,
    description: ev.description,
    pattern_key: ev.pattern_key,
    pattern_keys: ev.pattern_keys,
    choices: ev.choices,
    source: ev.source,
    captured_at: ev.captured_at,
  };
}

function addToSet<T>(s: Set<T>, v: T): Set<T> {
  const n = new Set(s);
  n.add(v);
  return n;
}

function removeFromSet<T>(s: Set<T>, v: T): Set<T> {
  if (!s.has(v)) return s;
  const n = new Set(s);
  n.delete(v);
  return n;
}

function setInMap<K, V>(m: Map<K, V>, k: K, v: V): Map<K, V> {
  const n = new Map(m);
  n.set(k, v);
  return n;
}

function removeFromMap<K, V>(m: Map<K, V>, k: K): Map<K, V> {
  if (!m.has(k)) return m;
  const n = new Map(m);
  n.delete(k);
  return n;
}
