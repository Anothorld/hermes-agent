/**
 * Persisted in-flight lock for buttons that trigger an async gateway run
 * (preview-draft, refine). The pure-React ``busy`` state is per-component
 * and resets on page refresh — which lets the operator click the button
 * a second time and spawn a duplicate writer for the same fact. Mirror
 * the backend's in-flight dedup (5 min TTL on ``product_campaign_runs``)
 * in ``sessionStorage`` so the disabled state survives refresh.
 *
 * Pair with ``parseConflictBody`` on the call-site: when the backend
 * returns 409 with a known run, ``acquire()`` with that run_id so the
 * UI immediately reflects the existing in-flight run from a different
 * tab.
 */
import { useCallback, useEffect, useState } from 'react';
import { ApiError } from './api';

// Match backend INFLIGHT_TTL_SECONDS in run_registry.py.
export const LOCK_TTL_MS = 5 * 60 * 1000;

type Lock = { run_id: string | null; started_at_ms: number };

function readLock(key: string): Lock | null {
  try {
    const raw = sessionStorage.getItem(key);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Lock;
    if (typeof parsed?.started_at_ms !== 'number') return null;
    if (Date.now() - parsed.started_at_ms > LOCK_TTL_MS) {
      sessionStorage.removeItem(key);
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

export type InflightLock = {
  locked: boolean;
  runId: string | null;
  startedAtMs: number | null;
  remainingSeconds: number;
  acquire: (runId: string | null, startedAtMs?: number) => void;
  release: () => void;
};

export function useInflightLock(key: string): InflightLock {
  const [lock, setLock] = useState<Lock | null>(() => readLock(key));

  useEffect(() => {
    // Re-read on key change (e.g. switching between escalations) and
    // tick every second so the countdown in the UI stays current and
    // the lock auto-clears at TTL expiry without a manual refresh.
    setLock(readLock(key));
    const t = setInterval(() => setLock(readLock(key)), 1000);
    return () => clearInterval(t);
  }, [key]);

  const acquire = useCallback(
    (runId: string | null, startedAtMs: number = Date.now()) => {
      const next: Lock = { run_id: runId, started_at_ms: startedAtMs };
      sessionStorage.setItem(key, JSON.stringify(next));
      setLock(next);
    },
    [key],
  );
  const release = useCallback(() => {
    sessionStorage.removeItem(key);
    setLock(null);
  }, [key]);

  const remainingSeconds = lock
    ? Math.max(
        0,
        Math.ceil((LOCK_TTL_MS - (Date.now() - lock.started_at_ms)) / 1000),
      )
    : 0;

  return {
    locked: lock !== null && remainingSeconds > 0,
    runId: lock?.run_id ?? null,
    startedAtMs: lock?.started_at_ms ?? null,
    remainingSeconds,
    acquire,
    release,
  };
}

/** Parse a FastAPI 409 response body into our backend's conflict shape.
 * Returns null if the error isn't a 409 or the body isn't structured. */
export function parseConflictBody(err: unknown): {
  error?: string;
  message?: string;
  run_id?: string;
  started_at?: string;
  current_captured_at?: string;
  expected_captured_at?: string;
} | null {
  if (!(err instanceof ApiError) || err.status !== 409) return null;
  try {
    const parsed = JSON.parse(err.body) as { detail?: unknown };
    if (parsed && typeof parsed === 'object' && 'detail' in parsed) {
      const d = parsed.detail;
      if (typeof d === 'object' && d !== null) {
        return d as Record<string, string>;
      }
      if (typeof d === 'string') return { message: d };
    }
  } catch {
    /* non-JSON body — fall through */
  }
  return { message: err.body };
}

/** Convert a server ``started_at`` ISO string into the ms epoch the lock
 * stores. Falls back to ``now`` if unparseable. */
export function startedAtMs(isoOrNull: string | null | undefined): number {
  if (!isoOrNull) return Date.now();
  const t = Date.parse(isoOrNull);
  return Number.isFinite(t) ? t : Date.now();
}
