function defaultApiBase(): string {
  if (typeof window === 'undefined') return 'http://localhost:8765';
  const { protocol, hostname } = window.location;
  return `${protocol}//${hostname}:8765`;
}

export const API_BASE: string =
  (import.meta.env.VITE_API_BASE as string) || defaultApiBase();

const TOKEN_KEY = 'koc.token';

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(t: string | null): void {
  if (t) localStorage.setItem(TOKEN_KEY, t);
  else localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  constructor(public status: number, public body: string) {
    super(`API ${status}: ${body}`);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getToken();
  const headers = new Headers(init.headers);
  if (token) headers.set('Authorization', `Bearer ${token}`);
  if (init.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  const res = await fetch(`${API_BASE}${path}`, { ...init, headers });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    if (res.status === 401) {
      setToken(null);
      // Hard redirect so RequireAuth picks up the cleared token and the
      // user lands on the login form instead of a stuck error page.
      if (typeof window !== 'undefined' && window.location.pathname !== '/login') {
        window.location.href = '/login';
      }
    }
    throw new ApiError(res.status, text);
  }
  const ct = res.headers.get('content-type') || '';
  if (ct.includes('application/json')) return res.json() as Promise<T>;
  return undefined as T;
}

export const api = {
  get: <T>(p: string) => request<T>(p),
  post: <T>(p: string, body?: unknown) =>
    request<T>(p, { method: 'POST', body: body ? JSON.stringify(body) : undefined }),
  put: <T>(p: string, body?: unknown) =>
    request<T>(p, { method: 'PUT', body: body ? JSON.stringify(body) : undefined }),
  patch: <T>(p: string, body?: unknown) =>
    request<T>(p, { method: 'PATCH', body: body ? JSON.stringify(body) : undefined }),
};

// ---------- SSE helper ----------

export type SseEvent = { event: string; data: string };

export type SseConnState = 'connecting' | 'open' | 'reconnecting' | 'closed';

export type SseHandle = {
  cancel: () => void;
};

export type StreamLinesOpts = {
  // Called whenever the connection state transitions. Use this to render
  // a "reconnecting (n)…" badge so the operator knows the panel will
  // recover automatically — without it, the silent reconnect loop is
  // indistinguishable from a dead session.
  onState?: (state: SseConnState, attempt: number) => void;
  // Auto-reconnect knobs. Defaults: indefinite retries with exponential
  // backoff capped at 15s, with ±20% jitter so a thundering herd of
  // tabs reconnects spread out. Pass ``maxRetries`` to bound the loop.
  maxRetries?: number;
  baseDelayMs?: number;
  maxDelayMs?: number;
};

// Subscribe to an SSE endpoint that requires Bearer auth. EventSource
// can't set custom headers, so we use fetch + ReadableStream and parse
// the wire format ourselves (event: / data: / blank-line frame).
//
// Reconnect behavior: on transport error (network drop, server-side
// proxy timeout, gateway crash) we wait baseDelayMs * 2^attempt with
// jitter, then reconnect transparently. ``onEvent`` is invoked on the
// reopened stream as if the connection had never dropped — the caller
// is responsible for de-duping or ignoring frames it already processed
// before the drop. For the transcript panel, the snapshot frame on
// every reconnect already handles state recovery. Authentication
// failures (401) abort the retry loop.
export function streamLines(
  path: string,
  onEvent: (ev: SseEvent) => void,
  onError?: (e: unknown) => void,
  opts: StreamLinesOpts = {},
): SseHandle {
  const ctrl = new AbortController();
  const baseDelay = opts.baseDelayMs ?? 1000;
  const maxDelay = opts.maxDelayMs ?? 15000;
  const maxRetries = opts.maxRetries; // undefined = retry forever
  const setState = (s: SseConnState, attempt: number) => {
    try {
      opts.onState?.(s, attempt);
    } catch {
      // Don't let an onState callback exception kill the loop.
    }
  };

  const sleep = (ms: number) =>
    new Promise<void>((resolve, reject) => {
      const t = setTimeout(() => resolve(), ms);
      ctrl.signal.addEventListener(
        'abort',
        () => {
          clearTimeout(t);
          reject(new DOMException('aborted', 'AbortError'));
        },
        { once: true },
      );
    });

  (async () => {
    let attempt = 0;
    setState('connecting', attempt);
    while (!ctrl.signal.aborted) {
      const token = getToken();
      const headers: Record<string, string> = { Accept: 'text/event-stream' };
      if (token) headers.Authorization = `Bearer ${token}`;
      let unrecoverable = false;
      try {
        const res = await fetch(`${API_BASE}${path}`, {
          headers,
          signal: ctrl.signal,
        });
        if (res.status === 401) {
          // Token expired — match the unary request() behavior so the
          // user lands on /login. Caller's onError still fires so the
          // panel renders the error state until the redirect lands.
          setToken(null);
          if (
            typeof window !== 'undefined'
            && window.location.pathname !== '/login'
          ) {
            window.location.href = '/login';
          }
          onError?.(new ApiError(401, await res.text().catch(() => '')));
          unrecoverable = true;
          break;
        }
        if (!res.ok || !res.body) {
          // Other HTTP errors are surfaced but still retried — the
          // server may be temporarily 502'ing during a redeploy.
          onError?.(new ApiError(res.status, await res.text().catch(() => '')));
        } else {
          attempt = 0;
          setState('open', attempt);
          const reader = res.body.getReader();
          const decoder = new TextDecoder('utf-8');
          let buf = '';
          while (true) {
            const { value, done } = await reader.read();
            if (done) break;
            buf += decoder.decode(value, { stream: true });
            let idx = buf.indexOf('\n\n');
            while (idx >= 0) {
              const raw = buf.slice(0, idx);
              buf = buf.slice(idx + 2);
              idx = buf.indexOf('\n\n');
              let evt = 'message';
              const dataLines: string[] = [];
              for (const line of raw.split('\n')) {
                if (line.startsWith(':')) continue;
                if (line.startsWith('event:')) evt = line.slice(6).trim();
                else if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart());
              }
              if (dataLines.length) onEvent({ event: evt, data: dataLines.join('\n') });
            }
          }
        }
      } catch (e) {
        if ((e as { name?: string })?.name === 'AbortError') break;
        // Network-level failure (DNS, TCP reset, fetch abort by browser
        // when laptop sleeps) — surface to caller AND reconnect.
        onError?.(e);
      }
      if (ctrl.signal.aborted || unrecoverable) break;
      if (maxRetries !== undefined && attempt >= maxRetries) {
        setState('closed', attempt);
        break;
      }
      attempt += 1;
      const expBackoff = Math.min(maxDelay, baseDelay * 2 ** (attempt - 1));
      const jitter = 0.8 + Math.random() * 0.4; // 0.8x – 1.2x
      const delay = Math.round(expBackoff * jitter);
      setState('reconnecting', attempt);
      try {
        await sleep(delay);
      } catch {
        break; // aborted
      }
      setState('connecting', attempt);
    }
    setState('closed', attempt);
  })();
  return { cancel: () => ctrl.abort() };
}

// ---------- v2.4 KOL agent types ----------

export type Lane = 'commerce' | 'fulfillment' | 'publish' | 'meta';

export type GoalState = {
  goal: string | null;
  state: string;
  missing_facts: string[];
  blocked_reason?: string | null;
};

export type LaneSnapshot = {
  identity_id: number;
  handle: string;
  goals: Record<Lane, GoalState | null>;
  repeat_count?: number;
  last_outcome?: string | null;
  // Card-level shortcuts pulled from the latest facts so the FE can
  // render the "sent 12h ago" line and the interest-signal badge
  // without a per-card /facts fan-out.
  outreach_sent_at?: string | null;
  interest_signal?: string | null;
  // Tri-state for "where is the initial outreach email":
  //   false / undefined          → no Gmail draft yet (operator may
  //                                need to re-trigger the skill)
  //   outreach_draft_created     → draft sitting in Gmail, waiting on
  //                                operator to click Send
  //   + outreach_sent_at         → SENT-reconcile confirmed delivery
  outreach_draft_created?: boolean;
  gmail_draft_id?: string | null;
  gmail_thread_id?: string | null;
  // Per-card unread + Draft sub-state inputs. *_latest_at is the
  // newest captured_at / created_at among pending items for this KOL;
  // the FE compares it against a localStorage last-seen timestamp to
  // decide whether to render a red dot.
  pending_approval_count?: number;
  pending_approval_latest_at?: string | null;
  open_escalation_count?: number;
  open_escalation_latest_at?: string | null;
  // Drives the "Draft 待审批" vs "Draft 待发送" badge on the kanban
  // card. Computed server-side from approval.reply_draft.decision and
  // offer.outreach_sent so the FE doesn't have to reach into facts.
  reply_draft_state?: 'pending' | 'approved_unsent' | 'sent' | null;
};

export type CampaignListItem = {
  campaign_id: string;
  env: 'TEST' | 'LIVE';
  candidate_count: number;
  last_touched_at?: string | null;
  label?: string | null;
  status?: string | null;
};

export type EscalationRow = {
  id: number;
  identity_id: number;
  // Bridge sends ``null`` for identity-scoped escalations (e.g.,
  // ``contact_email_not_found`` — no campaign context yet). Treat this
  // as ``string | null`` everywhere or risk shipping ``"null"`` into
  // URLs / fact writes via ``encodeURIComponent``.
  campaign_id: string | null;
  rule_id: string | null;
  reason: string;
  suggested_question: string | null;
  state:
    | 'awaiting_answer'
    | 'answered'
    | 'resuming'
    | 'resolved'
    | 're_escalated'
    | 'aborted';
  parent_id: number | null;
  created_at: string;
  resolved_at: string | null;
  operator_answer: string | null;
  // resume_context_json deserialised by the bridge — includes
  // `required_facts_to_resume` (used to drive the structured facts
  // form), `force_human_takeover_hint` (depth-aware badge),
  // `max_escalation_depth`, `attempts_count`, etc.
  resume_context?: Record<string, unknown> | null;
  attempts_count?: number | null;
};

export type Policy = {
  id: number;
  scope: 'company_style' | 'user_style' | 'escalation_rules';
  owner_user_id: number | null;
  title: string | null;
  content_md: string;
  version: number;
  is_active: number;
  updated_by: string;
  updated_at: string;
};
