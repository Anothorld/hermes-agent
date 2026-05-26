import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';

export type Env = 'TEST' | 'LIVE';

// ------------------- env store -------------------
// Single source of truth for TEST/LIVE. Previously every page kept its
// own copy in `localStorage.kolEnv / approvalsEnv / escalationEnv`,
// which let the kanban load LIVE while approvals stayed on TEST —
// operators thought it was a data outage. Now one store, one banner,
// one switch.

type EnvState = {
  env: Env;
  setEnv: (env: Env) => void;
};

// Migrate the legacy raw-string keys into the new persisted store so
// returning users keep whatever env they last picked.
function readLegacyEnv(): Env {
  if (typeof localStorage === 'undefined') return 'LIVE';
  const candidates = ['kolEnv', 'approvalsEnv', 'escalationEnv'];
  for (const k of candidates) {
    const v = localStorage.getItem(k);
    if (v === 'TEST' || v === 'LIVE') return v;
  }
  return 'LIVE';
}

function clearLegacyEnv() {
  if (typeof localStorage === 'undefined') return;
  for (const k of ['kolEnv', 'approvalsEnv', 'escalationEnv']) {
    localStorage.removeItem(k);
  }
}

export const useEnvStore = create<EnvState>()(
  persist(
    (set) => ({
      env: readLegacyEnv(),
      setEnv: (env) => set({ env }),
    }),
    {
      name: 'koc.env',
      storage: createJSONStorage(() => localStorage),
      onRehydrateStorage: () => () => {
        clearLegacyEnv();
      },
    },
  ),
);

// ------------------- campaign store -------------------
// The current "context" campaign. Most pages are campaign-scoped, but
// before this the picker only existed on the Kanban — switching to
// Approvals / Escalations lost the binding. Now the picker is a global
// nav item bound to this store.

type CampaignState = {
  currentCampaignId: string;
  setCampaignId: (id: string) => void;
};

function readLegacyCampaign(): string {
  if (typeof localStorage === 'undefined') return '';
  return localStorage.getItem('lastCampaignId') ?? '';
}

export const useCampaignStore = create<CampaignState>()(
  persist(
    (set) => ({
      currentCampaignId: readLegacyCampaign(),
      setCampaignId: (id) => set({ currentCampaignId: id }),
    }),
    {
      name: 'koc.campaign',
      storage: createJSONStorage(() => localStorage),
      onRehydrateStorage: () => () => {
        if (typeof localStorage !== 'undefined') localStorage.removeItem('lastCampaignId');
      },
    },
  ),
);

// ------------------- toast store -------------------
// Lightweight toast queue. No external dep — ToastHost renders the
// list, push/dismiss are exposed as a `toast` helper so non-React code
// (api wrappers, error normalisers) can fire toasts too.

export type ToastKind = 'success' | 'error' | 'info' | 'progress';

export type ToastItem = {
  id: string;
  kind: ToastKind;
  title: string;
  detail?: string;
  // Auto-dismiss timeout. 0 = sticky (operator must close manually).
  durationMs?: number;
  // For progress toasts that should be updated in place. Caller passes
  // a stable key and a subsequent push() with the same key replaces the
  // entry instead of stacking another row.
  groupKey?: string;
};

type ToastState = {
  toasts: ToastItem[];
  push: (t: Omit<ToastItem, 'id'>) => string;
  dismiss: (id: string) => void;
  clear: () => void;
};

export const useToastStore = create<ToastState>((set) => ({
  toasts: [],
  push: (t) => {
    const id = `t-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    set((s) => {
      // Replace-in-place when the caller provides a groupKey (used for
      // long-running async actions like draft refine).
      if (t.groupKey) {
        const filtered = s.toasts.filter((x) => x.groupKey !== t.groupKey);
        return { toasts: [...filtered, { ...t, id }] };
      }
      return { toasts: [...s.toasts, { ...t, id }] };
    });
    return id;
  },
  dismiss: (id) => set((s) => ({ toasts: s.toasts.filter((x) => x.id !== id) })),
  clear: () => set({ toasts: [] }),
}));

// Convenience handles for non-component code.
export const toast = {
  success: (title: string, detail?: string, opts?: Partial<ToastItem>) =>
    useToastStore.getState().push({ kind: 'success', title, detail, durationMs: 4000, ...opts }),
  error: (title: string, detail?: string, opts?: Partial<ToastItem>) =>
    useToastStore.getState().push({ kind: 'error', title, detail, durationMs: 7000, ...opts }),
  info: (title: string, detail?: string, opts?: Partial<ToastItem>) =>
    useToastStore.getState().push({ kind: 'info', title, detail, durationMs: 5000, ...opts }),
  progress: (title: string, detail?: string, opts?: Partial<ToastItem>) =>
    useToastStore.getState().push({ kind: 'progress', title, detail, durationMs: 0, ...opts }),
  dismiss: (id: string) => useToastStore.getState().dismiss(id),
};

// ------------------- dev / preferences store -------------------
// Operator preferences that change rendering but not data. Today only
// `showRawFactKeys` (renders fact_path next to the friendly label —
// off by default, settable from Settings).

type PrefsState = {
  showRawFactKeys: boolean;
  setShowRawFactKeys: (v: boolean) => void;
};

export const usePrefsStore = create<PrefsState>()(
  persist(
    (set) => ({
      showRawFactKeys: false,
      setShowRawFactKeys: (v) => set({ showRawFactKeys: v }),
    }),
    { name: 'koc.prefs', storage: createJSONStorage(() => localStorage) },
  ),
);
