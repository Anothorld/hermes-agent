import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';

// Operator-private last-seen timestamps per "scope". A scope names a
// surface in the UI (e.g. "approvals.global", "escalations.kol.123").
// We compare a scope's last-seen against the newest item's timestamp
// to decide whether to render an unread red dot — no count, no list,
// just "is anything newer than what you've already looked at".
//
// All state is local-only (localStorage, no server). Different browsers
// / devices intentionally don't sync; the dots are an attention helper,
// not a system of record.

export type UnreadScope = string;

type UnreadState = {
  seen: Record<UnreadScope, number>;
  markSeen: (scope: UnreadScope, atMs?: number) => void;
};

export const useUnreadStore = create<UnreadState>()(
  persist(
    (set) => ({
      seen: {},
      markSeen: (scope, atMs) =>
        set((s) => ({
          seen: { ...s.seen, [scope]: Math.max(s.seen[scope] ?? 0, atMs ?? Date.now()) },
        })),
    }),
    {
      name: 'koc.unread',
      storage: createJSONStorage(() => localStorage),
    },
  ),
);

export function isUnread(
  latestAt: string | null | undefined,
  lastSeenMs: number | undefined,
): boolean {
  if (!latestAt) return false;
  const t = new Date(latestAt).getTime();
  if (!Number.isFinite(t)) return false;
  return t > (lastSeenMs ?? 0);
}
