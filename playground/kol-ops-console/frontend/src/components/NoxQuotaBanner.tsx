import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import { errorSummary } from '../lib/errors';
import { toast } from '../lib/store';

export type NoxStatsUsage = {
  remaining_estimate?: number;
};

export type NoxStatsPayload = {
  cache?: { saved_api_calls_estimate?: number };
  usage?: NoxStatsUsage;
  supplement_usage?: { committed?: number; max_calls?: number; remaining?: number };
  quota_exhausted?: boolean;
};

export function isNoxQuotaExhausted(stats: NoxStatsPayload | null | undefined): boolean {
  if (!stats) return false;
  if (stats.quota_exhausted === true) return true;
  const rem = stats.usage?.remaining_estimate;
  return typeof rem === 'number' && rem <= 0;
}

type Props = {
  campaignId: string;
  env: string;
  stats: NoxStatsPayload | null;
  identityId?: number;
  onEscalationOpened?: () => void;
  className?: string;
};

export default function NoxQuotaBanner({
  campaignId,
  env,
  stats,
  identityId,
  onEscalationOpened,
  className = '',
}: Props) {
  const [busy, setBusy] = useState(false);
  if (!isNoxQuotaExhausted(stats)) return null;

  const openEscalation = async () => {
    setBusy(true);
    try {
      const r = await api.post<{ escalation_id?: number }>(
        `/campaigns/${encodeURIComponent(campaignId)}/nox-quota-escalation`,
        { env, identity_id: identityId ?? null },
      );
      toast.success('已打开 Nox 配额升级', String(r.escalation_id ?? ''));
      onEscalationOpened?.();
    } catch (ex) {
      toast.error('打开升级失败', errorSummary(ex));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className={
        `rounded border border-amber-400 bg-amber-50 px-3 py-2 text-xs text-amber-950 ${className}`
      }
      role="alert"
    >
      <div className="font-medium">Nox 本月本地预算已用尽</div>
      <p className="mt-1">
        LIVE 下的尽调 / 查邮箱 / 补搜已禁用，直至运营处理配额或进入下月缓存周期。
      </p>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <button
          type="button"
          disabled={busy}
          onClick={() => void openEscalation()}
          className="rounded bg-amber-700 px-2 py-1 text-white disabled:opacity-50"
        >
          {busy ? '提交中…' : '打开 nox_quota_exhausted 升级'}
        </button>
        <Link
          to={`/escalations?campaign_id=${encodeURIComponent(campaignId)}&env=${env}`}
          className="text-amber-900 underline-offset-2 hover:underline"
        >
          查看升级队列 →
        </Link>
      </div>
    </div>
  );
}
