import { useEffect, useRef } from 'react';

// setInterval wrapper that skips ticks while the operator is typing
// inside an editor surface. Editors mark themselves with the
// `data-editing` attribute; the hook checks `document.activeElement`
// on each tick and silently skips if it's inside one. This kills the
// page-flicker that happened when the kanban refresh re-rendered
// while an operator was halfway through filling a missing fact.

export function usePollingFallback(
  refresh: () => void | Promise<void>,
  intervalMs: number,
  // Set to false to pause the loop entirely (e.g. while a modal is
  // open). Default true.
  enabled: boolean = true,
) {
  const refreshRef = useRef(refresh);
  refreshRef.current = refresh;

  useEffect(() => {
    if (!enabled) return;
    const tick = () => {
      try {
        const ae = typeof document !== 'undefined' ? document.activeElement : null;
        if (ae && (ae as Element).closest?.('[data-editing]')) return;
        refreshRef.current();
      } catch {
        // Ignore — polling fallback shouldn't crash the page.
      }
    };
    const t = setInterval(tick, intervalMs);
    return () => clearInterval(t);
  }, [enabled, intervalMs]);
}
