import type { PriorOutreachTouch } from '../components/PriorOutreachTouchBadge';

const COOLDOWN_MS = 14 * 24 * 60 * 60 * 1000;

function parseMs(iso: string): number | null {
  const t = Date.parse(iso);
  return Number.isFinite(t) ? t : null;
}

function withinCooldown(lastTouchAt: string): boolean {
  const ms = parseMs(lastTouchAt);
  if (ms === null) return false;
  return Date.now() - ms < COOLDOWN_MS;
}

function touchFromFacts(facts: Record<string, unknown>): PriorOutreachTouch | null {
  const at = facts['offer.outreach_sent_at'];
  if (typeof at === 'string' && at.trim()) {
    const last_touch_at = at.trim();
    return {
      last_touch_at,
      within_cooldown: withinCooldown(last_touch_at),
      has_prior_touch: true,
      cooldown_days: 14,
    };
  }
  const sent = facts['offer.outreach_sent'];
  if (sent === true || sent === 'true') {
    return null;
  }
  return null;
}

/** Merge bridge global touch with current-campaign facts (whichever is later). */
export function resolvePriorOutreachTouch(
  apiTouch: PriorOutreachTouch | null | undefined,
  facts?: Record<string, unknown> | null,
): PriorOutreachTouch | null {
  const fromFacts = facts ? touchFromFacts(facts) : null;
  if (!apiTouch?.last_touch_at) return fromFacts;
  if (!fromFacts?.last_touch_at) return apiTouch;
  const apiMs = parseMs(apiTouch.last_touch_at) ?? 0;
  const factMs = parseMs(fromFacts.last_touch_at) ?? 0;
  return apiMs >= factMs ? apiTouch : fromFacts;
}
