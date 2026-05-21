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
    if (res.status === 401) setToken(null);
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
  state: 'open' | 'resolved' | 'terminated';
  parent_id: number | null;
  created_at: string;
  resolved_at: string | null;
  operator_answer: string | null;
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
