import { useCallback, useEffect, useState } from 'react';
import { api } from '../api';
import NoxQuotaBanner, {
  isNoxQuotaExhausted,
  type NoxStatsPayload,
} from './NoxQuotaBanner';
import { errorSummary } from '../lib/errors';
import { toast } from '../lib/store';

type Props = {
  campaignId: string;
  env: string;
};

type NoxStats = NoxStatsPayload;

export default function NoxCampaignOpsPanel({ campaignId, env }: Props) {
  const [stats, setStats] = useState<NoxStats | null>(null);
  const [busy, setBusy] = useState(false);
  const [supplementBusy, setSupplementBusy] = useState(false);
  const [keywords, setKeywords] = useState('');

  const loadStats = useCallback(async () => {
    try {
      const r = await api.get<{ stats: NoxStats }>(
        `/campaigns/${encodeURIComponent(campaignId)}/nox-stats?env=${env}`,
      );
      setStats(r.stats ?? null);
    } catch {
      setStats(null);
    }
  }, [campaignId, env]);

  useEffect(() => {
    void loadStats();
  }, [loadStats]);

  const quotaBlocked = isNoxQuotaExhausted(stats);

  const runSupplement = async (platform: 'youtube' | 'tiktok') => {
    if (quotaBlocked) {
      toast.error('Nox 配额已用尽', '请先处理升级或等待下月');
      return;
    }
    const remaining = stats?.usage?.remaining_estimate;
    const supplementRemaining = stats?.supplement_usage?.remaining;
    const estCalls = 1;
    const msg = [
      `平台: ${platform}`,
      `预计消耗约 ${estCalls} 次 Nox API（cache miss 时）`,
      typeof remaining === 'number' ? `本月本地预算余量约 ${remaining}` : null,
      typeof supplementRemaining === 'number'
        ? `本 campaign 补搜剩余 ${supplementRemaining} 次`
        : null,
      '确认派发 gateway run？',
    ]
      .filter(Boolean)
      .join('\n');
    if (!window.confirm(msg)) {
      return;
    }
    setSupplementBusy(true);
    const kwList = keywords
      .split(/[,，\n]/)
      .map((s) => s.trim())
      .filter(Boolean);
    try {
      const r = await api.post<{ run_id?: string }>(
        `/campaigns/${encodeURIComponent(campaignId)}/nox-supplement`,
        { env, platforms: [platform], keywords: kwList, page_size: 10 },
      );
      toast.success(`Nox 补搜已派发 (${platform})`, r.run_id ?? '');
      void loadStats();
    } catch (ex) {
      toast.error('补搜失败', errorSummary(ex));
    } finally {
      setSupplementBusy(false);
    }
  };

  const refreshQuota = async () => {
    setBusy(true);
    try {
      await loadStats();
      toast.success('已刷新 Nox 本地统计');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded border border-indigo-200 bg-indigo-50/40 p-3 text-xs">
      <div className="font-medium text-indigo-900">Nox API（补搜 / 配额）</div>
      <NoxQuotaBanner
        campaignId={campaignId}
        env={env}
        stats={stats}
        className="mt-2"
      />
      {stats && (
        <p className="mt-1 text-indigo-800">
          本月缓存节省约 {stats.cache?.saved_api_calls_estimate ?? 0} 次；
          本地剩余估计 {stats.usage?.remaining_estimate ?? '—'}；
          补搜 {stats.supplement_usage?.committed ?? 0}/
          {stats.supplement_usage?.max_calls ?? 30}
        </p>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <input
          value={keywords}
          onChange={(e) => setKeywords(e.target.value)}
          placeholder="补搜关键词（agent 在 run 中使用）"
          className="min-w-[12rem] flex-1 rounded border px-2 py-1"
        />
        <button
          type="button"
          disabled={supplementBusy || quotaBlocked}
          onClick={() => void runSupplement('youtube')}
          className="rounded bg-indigo-700 px-2 py-1 text-white disabled:opacity-50"
        >
          YouTube 补搜
        </button>
        <button
          type="button"
          disabled={supplementBusy || quotaBlocked}
          onClick={() => void runSupplement('tiktok')}
          className="rounded bg-indigo-600 px-2 py-1 text-white disabled:opacity-50"
        >
          TikTok 补搜
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => void refreshQuota()}
          className="rounded border border-indigo-300 px-2 py-1 text-indigo-800"
        >
          刷新统计
        </button>
      </div>
      <p className="mt-1 text-[11px] text-indigo-600">
        需在 campaign_config 开启 nox_supplement_enabled。Launch Step 3 不会自动补搜。
      </p>
    </div>
  );
}
