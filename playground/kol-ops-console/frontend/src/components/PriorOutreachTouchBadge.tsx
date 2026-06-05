import { TimeAgo } from './inputs/TimeAgo';

export type PriorOutreachTouch = {
  last_touch_at: string;
  last_touch_campaign_id?: string | null;
  within_cooldown?: boolean;
  has_prior_touch?: boolean;
  cooldown_days?: number;
};

/**
 * Marks KOLs we have emailed before (cross-campaign). Shows how long ago
 * the last confirmed outreach send happened.
 */
export function PriorOutreachTouchBadge({
  touch,
}: {
  touch: PriorOutreachTouch | null | undefined;
}) {
  if (!touch?.last_touch_at) return null;
  const inCooldown = touch.within_cooldown === true;
  const tone = inCooldown
    ? 'bg-rose-100 text-rose-900 border-rose-200'
    : 'bg-indigo-50 text-indigo-900 border-indigo-200';
  const campaignHint = touch.last_touch_campaign_id
    ? ` · 活动 ${touch.last_touch_campaign_id}`
    : '';
  const title = inCooldown
    ? `近 ${touch.cooldown_days ?? 14} 天内已触达，发现流程会自动跳过${campaignHint}`
    : `历史上已发过初邀邮件${campaignHint}`;

  return (
    <span
      className={`rounded border px-1.5 py-0.5 text-[10px] font-medium ${tone}`}
      title={title}
    >
      {inCooldown ? '近期已触达' : '曾触达'}
      {' · '}
      <TimeAgo iso={touch.last_touch_at} prefix="" className="inline" />
    </span>
  );
}
