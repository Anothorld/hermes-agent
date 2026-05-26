import { useCallback, useRef } from 'react';
import { useLiveEvents, WsEvent } from '../useLiveEvents';

// Lightweight event-driven refresh trigger. Pages used to poll on a
// 10–15s setInterval; with WS already in place, useDataChannel lets a
// page register a `refresh()` callback that fires whenever a relevant
// `event_type` flows through the bridge. The fallback polling is
// retained but at a slower cadence — so when WS is healthy the UI
// keeps up live, and when WS is dead the page degrades gracefully.

type Match = string | RegExp | ((evt: string) => boolean);

interface Options {
  // Match against `event_type` of individual items inside the WS
  // payload. Page passes a regex, a literal, or a predicate. Match
  // omitted = fire on every event (broad pages like Kanban use this).
  match?: Match;
  // Called when at least one event in the batch matches.
  onMatch: () => void;
  // Match against optional identity_id for per-KOL pages.
  identityId?: number;
}

function matches(m: Match, evt: string): boolean {
  if (typeof m === 'string') return m === evt;
  if (m instanceof RegExp) return m.test(evt);
  return m(evt);
}

export function useDataChannel(opts: Options): { connected: boolean } {
  const optsRef = useRef(opts);
  optsRef.current = opts;

  const onEvent = useCallback((e: WsEvent) => {
    const cur = optsRef.current;
    const items = e.items || [];
    if (items.length === 0) return;
    const hit = items.some((it) => {
      if (cur.identityId != null && it.kol_identity_id !== cur.identityId) return false;
      if (cur.match && !matches(cur.match, it.event_type)) return false;
      return true;
    });
    if (hit) cur.onMatch();
  }, []);

  return useLiveEvents(onEvent);
}
