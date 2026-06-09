import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { api, ApiError, getToken } from './api';
import { errorSummary } from './lib/errors';
import { useGatewayApprovalDockStore, toast } from './lib/store';
import { useLiveEvents } from './useLiveEvents';

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
  items: GatewayApproval[];
  inflight: Set<string>;
  errors: Map<string, string>;
  loaded: boolean;
};

const EMPTY_STATE: State = {
  items: [],
  inflight: new Set<string>(),
  errors: new Map<string, string>(),
  loaded: false,
};

export type GatewayApprovalContextValue = {
  items: GatewayApproval[];
  inflight: Set<string>;
  errors: Map<string, string>;
  loaded: boolean;
  count: number;
  open: boolean;
  setOpen: (open: boolean) => void;
  toggle: () => void;
  resolve: (runId: string, choice: ApprovalChoice) => Promise<void>;
};

const GatewayApprovalContext = createContext<GatewayApprovalContextValue | null>(
  null,
);

export function GatewayApprovalProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<State>(EMPTY_STATE);
  const lastSeqRef = useRef(0);
  const open = useGatewayApprovalDockStore((s) => s.open);
  const setOpen = useGatewayApprovalDockStore((s) => s.setOpen);
  const toggle = useGatewayApprovalDockStore((s) => s.toggle);

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
          const nextErrors = new Map(prev.errors);
          nextErrors.delete(ev.run_id);
          return {
            ...prev,
            items: nextItems,
            errors: nextErrors,
          };
        });
      } else {
        setState((prev) => ({
          ...prev,
          items: prev.items.filter((it) => it.run_id !== ev.run_id),
          inflight: removeFromSet(prev.inflight, ev.run_id),
          errors: removeFromMap(prev.errors, ev.run_id),
        }));
      }
    }
  });

  const resolve = useCallback(async (runId: string, choice: ApprovalChoice) => {
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
  }, []);

  const count = state.items.length;

  const value = useMemo(
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

  return (
    <GatewayApprovalContext.Provider value={value}>
      {children}
    </GatewayApprovalContext.Provider>
  );
}

export function useGatewayApprovals(): GatewayApprovalContextValue {
  const ctx = useContext(GatewayApprovalContext);
  if (!ctx) {
    throw new Error('useGatewayApprovals requires GatewayApprovalProvider');
  }
  return ctx;
}

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
