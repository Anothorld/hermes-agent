export const API_BASE: string =
  (import.meta.env.VITE_API_BASE as string) || 'http://localhost:8765';

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

export type SseHandle = {
  cancel: () => void;
};

// Subscribe to an SSE endpoint that requires Bearer auth. EventSource
// can't set custom headers, so we use fetch + ReadableStream and parse
// the wire format ourselves (event: / data: / blank-line frame).
export function streamLines(path: string, onEvent: (ev: SseEvent) => void, onError?: (e: unknown) => void): SseHandle {
  const ctrl = new AbortController();
  const token = getToken();
  const headers: Record<string, string> = { Accept: 'text/event-stream' };
  if (token) headers.Authorization = `Bearer ${token}`;
  (async () => {
    try {
      const res = await fetch(`${API_BASE}${path}`, { headers, signal: ctrl.signal });
      if (!res.ok || !res.body) {
        onError?.(new ApiError(res.status, await res.text().catch(() => '')));
        return;
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder('utf-8');
      let buf = '';
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        // Split on blank lines = end of one SSE frame.
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
    } catch (e) {
      if ((e as { name?: string })?.name !== 'AbortError') onError?.(e);
    }
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
};

export type EscalationRow = {
  id: number;
  identity_id: number;
  campaign_id: string;
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
