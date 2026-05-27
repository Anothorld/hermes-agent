// Helpers for handling ``campaign_id`` at the React / URL boundary.
//
// JavaScript's ``encodeURIComponent`` coerces ``null`` → ``"null"`` and
// ``undefined`` → ``"undefined"`` (literal 4/9-char strings). When a
// JSON-null campaign_id from an identity-scoped escalation, a missing
// URL search param, or a route without a campaign context flows into
// one of our link builders, that sentinel string travels through the
// console-backend to the bridge, where it has historically been
// accepted as a valid string and persisted into ``kol_facts`` /
// ``kol_goal_state`` rows — see the 2026-05-27 pollution incident on
// identity 658 (@mysweetsavannah).
//
// The bridge and the console-backend now reject these sentinels, but
// catching them at the React layer is the cheapest, clearest place: we
// can either omit the param entirely or steer the operator to a route
// where the campaign context is actually known.
//
// Centralised here so any future page only has to import one helper.

const NULL_SENTINELS = new Set(['null', 'undefined', 'nan', 'none']);

/** True if the value is a real, usable campaign id (not null/empty/
 *  sentinel string). Use this to gate UI: e.g., show "redraft" only
 *  when ``isRealCampaignId(campaignId)``.
 */
export function isRealCampaignId(value: unknown): value is string {
  if (typeof value !== 'string') return false;
  const trimmed = value.trim();
  if (!trimmed) return false;
  return !NULL_SENTINELS.has(trimmed.toLowerCase());
}

/** Append ``&campaign_id=<encoded>`` only when the value is real.
 *  Returns the empty string otherwise so callers can do
 *  ``\`/foo?env=${env}${campaignIdQuery(cid)}\``` without conditionals.
 */
export function campaignIdQuery(value: unknown): string {
  if (!isRealCampaignId(value)) return '';
  return `&campaign_id=${encodeURIComponent(value)}`;
}

/** Build the first-param variant: returns ``?campaign_id=<encoded>``
 *  if real, otherwise ``""``. Use when campaign_id is the only
 *  search param being assembled.
 */
export function campaignIdQueryFirst(value: unknown): string {
  if (!isRealCampaignId(value)) return '';
  return `?campaign_id=${encodeURIComponent(value)}`;
}

/** Same idea but for path-segment use: returns the encoded id, or
 *  throws synchronously. Caller is responsible for catching and
 *  rendering a placeholder route — kept loud on purpose so a bad call
 *  fails in dev rather than producing ``/campaigns/null/...``.
 */
export function requireCampaignIdPathSegment(value: unknown): string {
  if (!isRealCampaignId(value)) {
    throw new Error(
      `requireCampaignIdPathSegment: refusing to build a path with ` +
      `${value === undefined ? 'undefined' : JSON.stringify(value)} ` +
      `as the campaign id. The caller should not have reached a ` +
      `campaign-scoped action without a real campaign context.`,
    );
  }
  return encodeURIComponent(value);
}
