import { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { RepeatKolBadge } from './RepeatKolBadge';
import { TimeAgo } from './inputs/TimeAgo';
import { outcomeChipClass, outcomeLabel } from '../lib/kolOutcomes';
import { KolNoxInsightsBoard } from './KolNoxInsightsBoard';
import { pickKolProfileMetrics } from '../lib/kolProfileMetrics';
import { truncateNoxId } from '../lib/noxLabels';
import { factKeyLabel } from './factKeyLabel';

type IdentitySummary = {
  id: number;
  primary_handle: string;
  display_name?: string | null;
  primary_email: string | null;
  creator_type?: string | null;
  region?: string | null;
  language?: string | null;
  env: string;
  repeat_count?: number;
  last_outcome?: string | null;
};

function StatCard({
  label,
  value,
  sub,
  source,
}: {
  label: string;
  value: ReactNode;
  sub?: string;
  source?: string;
}) {
  return (
    <div className="rounded-lg border border-slate-200/80 bg-white px-3 py-2.5 shadow-sm">
      <div className="flex items-center justify-between gap-1">
        <div className="text-[10px] font-medium uppercase tracking-wide text-slate-500">
          {label}
        </div>
        {source && (
          <span className="rounded bg-violet-100 px-1 py-0.5 text-[9px] font-medium text-violet-700">
            {source}
          </span>
        )}
      </div>
      <div className="mt-0.5 text-sm font-semibold text-slate-900">{value}</div>
      {sub && <div className="mt-0.5 text-[10px] text-slate-500">{sub}</div>}
    </div>
  );
}

function EmptyDash() {
  return <span className="font-normal text-slate-400">—</span>;
}

function outreachPathLabel(path: string): string {
  const opt = factKeyLabel('identity.outreach_path').enumOptions?.find(
    (o) => o.value === path,
  );
  return opt?.label ?? path;
}

export function KolProfileDashboard({
  identity,
  campaignId,
  facts,
  lastRefreshedAt,
  onArchive,
}: {
  identity: IdentitySummary;
  campaignId: string;
  facts: Record<string, unknown>;
  lastRefreshedAt: number;
  onArchive: () => void;
}) {
  const handle = identity.primary_handle || `kol#${identity.id}`;
  const displayName =
    identity.display_name && identity.display_name !== identity.primary_handle
      ? identity.display_name
      : null;
  const initials = (displayName || handle).replace(/^@/, '').slice(0, 2).toUpperCase();

  const m = pickKolProfileMetrics(facts, identity);
  const noxCreatorId =
    typeof facts['identity.nox_creator_id'] === 'string'
      ? facts['identity.nox_creator_id']
      : null;
  const outreachPath =
    typeof facts['identity.outreach_path'] === 'string'
      ? facts['identity.outreach_path']
      : null;

  const socialCount = [
    'identity.instagram_profile_url',
    'identity.tiktok_profile_url',
    'identity.youtube_profile_url',
    'identity.facebook_profile_url',
    'identity.twitter_profile_url',
    'identity.threads_profile_url',
    'identity.linktree_url',
    'identity.personal_site_url',
  ].filter((k) => {
    const v = facts[k];
    return typeof v === 'string' && v.trim().startsWith('http');
  }).length;

  const noxSource = m.hasNoxDiligence ? 'Nox' : undefined;

  return (
    <section className="overflow-hidden rounded-xl border border-slate-200 bg-gradient-to-br from-slate-50 via-white to-sky-50/40 shadow-sm">
      <div className="flex flex-wrap items-start gap-4 border-b border-slate-200/80 bg-white/70 px-4 py-4">
        <div
          className="flex h-14 w-14 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-sky-500 to-indigo-600 text-lg font-bold text-white shadow-md"
          aria-hidden
        >
          {initials}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-xl font-semibold tracking-tight text-slate-900">
              @{handle}
            </h1>
            <RepeatKolBadge
              count={identity.repeat_count || 0}
              lastOutcome={identity.last_outcome ?? null}
            />
            <span className="rounded border border-slate-200 bg-slate-100 px-1.5 py-0.5 text-[10px] font-medium uppercase text-slate-600">
              {identity.env}
            </span>
            {m.hasNoxDiligence && (
              <span className="rounded bg-violet-100 px-1.5 py-0.5 text-[10px] font-medium text-violet-800">
                已 Nox 尽调
              </span>
            )}
            {identity.last_outcome && (
              <span
                className={`rounded border px-1.5 py-0.5 text-[11px] ${outcomeChipClass(identity.last_outcome)}`}
                title={`上次归档：${identity.last_outcome}`}
              >
                {outcomeLabel(identity.last_outcome)}
              </span>
            )}
          </div>
          {displayName && (
            <p className="mt-0.5 text-sm text-slate-600">{displayName}</p>
          )}
          <p className="mt-1 text-sm text-slate-700">
            {identity.primary_email ? (
              <a
                href={`mailto:${identity.primary_email}`}
                className="text-sky-800 hover:underline"
              >
                {identity.primary_email}
              </a>
            ) : (
              <span className="italic text-amber-700">尚未登记邮箱</span>
            )}
          </p>
          <p className="mt-1 text-[11px] text-slate-500">
            活动 ID{' '}
            <span className="font-mono text-slate-700">{campaignId}</span>
            {lastRefreshedAt > 0 && (
              <>
                {' '}
                ·{' '}
                <TimeAgo
                  iso={lastRefreshedAt}
                  prefix="刷新于"
                  className="text-slate-400"
                />
              </>
            )}
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={onArchive}
            className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 shadow-sm hover:border-rose-300 hover:bg-rose-50 hover:text-rose-700"
          >
            归档此 KOL
          </button>
          <Link
            to={`/kols/${identity.id}/relationship`}
            className="rounded-lg border border-sky-200 bg-sky-50 px-3 py-1.5 text-xs font-medium text-sky-800 hover:bg-sky-100"
          >
            历史与复用 →
          </Link>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2 px-4 py-3 sm:grid-cols-3 lg:grid-cols-6">
        <StatCard
          label="粉丝数"
          value={
            m.followers ? (
              <>
                {m.followers}
                {m.followersIsEstimate && (
                  <span className="ml-1 text-[10px] font-normal text-amber-700">
                    （估算）
                  </span>
                )}
              </>
            ) : (
              <EmptyDash />
            )
          }
          source={
            m.followers
              ? m.followersIsEstimate
                ? 'Nox 估算'
                : noxSource
              : undefined
          }
          sub={
            m.followersIsEstimate
              ? '由平均播放 ÷ 播放/粉丝比推算（IG 尽调包不返回粉丝数）'
              : undefined
          }
        />
        <StatCard
          label="主要地区"
          value={m.region ?? <EmptyDash />}
          source={m.region && facts['identity.nox_top_region'] ? noxSource : undefined}
        />
        <StatCard
          label="互动率"
          value={m.engagementRate ?? <EmptyDash />}
          source={m.engagementRate ? noxSource : undefined}
        />
        <StatCard
          label="平均播放"
          value={m.avgViews ?? <EmptyDash />}
          source={m.avgViews ? noxSource : undefined}
        />
        <StatCard
          label="社交主页"
          value={socialCount > 0 ? `已填 ${socialCount} 个` : '未填写'}
          sub={socialCount > 0 ? '见下方快速跳转' : '可在下方补充链接'}
        />
        <StatCard
          label="Nox 达人 ID"
          value={
            noxCreatorId ? (
              <span className="font-mono text-xs" title={noxCreatorId}>
                {truncateNoxId(noxCreatorId)}
              </span>
            ) : (
              <EmptyDash />
            )
          }
          sub={noxCreatorId ? '尽调后写入' : undefined}
        />
      </div>

      <KolNoxInsightsBoard facts={facts} />

      {outreachPath && (
        <div className="border-t border-slate-200/80 px-4 py-2 text-xs text-slate-600">
          触达分路：
          <span className="ml-1 font-medium text-slate-800">
            {outreachPathLabel(outreachPath)}
          </span>
        </div>
      )}
    </section>
  );
}
