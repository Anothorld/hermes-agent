import { useEffect, useState } from 'react';
import { api, ApiError } from '../api';
import { toast } from '../lib/store';
import { pickKolProfileMetrics } from '../lib/kolProfileMetrics';
import {
  formatNoxDiligenceVerdict,
  parseNoxCacheKey,
  truncateNoxId,
} from '../lib/noxLabels';
import NoxQuotaBanner, {
  isNoxQuotaExhausted,
  type NoxStatsPayload,
} from './NoxQuotaBanner';
import { TimeAgo } from './inputs/TimeAgo';

function parseApiErrorDetail(err: unknown): { message?: string } | null {
  if (!(err instanceof ApiError)) return { message: String(err) };
  try {
    const parsed = JSON.parse(err.body);
    if (parsed && typeof parsed === 'object' && 'detail' in parsed) {
      const detail = (parsed as { detail: unknown }).detail;
      if (detail && typeof detail === 'object') return detail as { message?: string };
      if (typeof detail === 'string') return { message: detail };
    }
  } catch {
    // not JSON
  }
  return { message: err.body || err.message };
}

type Props = {
  identityId: number;
  campaignId: string;
  env: string;
  facts: Record<string, unknown>;
  onTriggered: () => void;
};

export function NoxDiligencePanel({
  identityId,
  campaignId,
  env,
  facts,
  onTriggered,
}: Props) {
  const verdict = facts['identity.nox_diligence_verdict'];
  const cacheMonth = facts['identity.nox_cache_month'];
  const cacheKey = facts['identity.nox_cache_key'];
  const diligenceAt = facts['identity.nox_diligence_at'];
  const noxCreatorId = facts['identity.nox_creator_id'];

  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [statsHint, setStatsHint] = useState<string | null>(null);
  const [noxStats, setNoxStats] = useState<NoxStatsPayload | null>(null);

  useEffect(() => {
    let cancelled = false;
    void api
      .get<{ stats?: NoxStatsPayload }>(
        `/campaigns/${encodeURIComponent(campaignId)}/nox-stats?env=${env}`,
      )
      .then((r) => {
        if (cancelled) return;
        setNoxStats(r.stats ?? null);
        const saved = r.stats?.cache?.saved_api_calls_estimate;
        const rem = r.stats?.usage?.remaining_estimate;
        if (saved != null || rem != null) {
          setStatsHint(
            `本月缓存节省 ${saved ?? 0} 次 · 本地预算余量 ${rem ?? '—'}`,
          );
        }
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [campaignId, env]);

  const quotaBlocked = isNoxQuotaExhausted(noxStats);

  const runDiligence = async () => {
    if (quotaBlocked) {
      setErr('Nox 本月本地预算已用尽，请先打开升级或等待下月。');
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      const r = await api.post<{
        sync?: boolean;
        verdict?: string;
        cache_hit?: boolean;
        facts_written?: number;
      }>(`/kols/${identityId}/nox-diligence`, {
        env,
        campaign_id: campaignId,
      });
      if (r.sync) {
        const hit = r.cache_hit ? '（命中缓存，未扣 API）' : '';
        toast.success(
          'Nox 尽调完成',
          `结论 ${r.verdict ?? '—'} · 写入 ${r.facts_written ?? 0} 项事实${hit}`,
        );
      }
      onTriggered();
    } catch (ex) {
      setErr(parseApiErrorDetail(ex)?.message ?? String(ex));
    } finally {
      setBusy(false);
    }
  };

  const metrics = pickKolProfileMetrics(facts);
  const verdictStr = typeof verdict === 'string' ? verdict.trim() : '';
  const verdictUi = verdictStr ? formatNoxDiligenceVerdict(verdictStr) : null;
  const parsedKey =
    typeof cacheKey === 'string' ? parseNoxCacheKey(cacheKey) : null;
  const creatorIdStr =
    typeof noxCreatorId === 'string'
      ? noxCreatorId
      : parsedKey?.noxCreatorId ?? null;

  return (
    <div className="rounded-xl border border-violet-200 bg-gradient-to-br from-violet-50/80 to-white p-4 text-sm shadow-sm">
      <div className="font-semibold text-violet-900">Nox 尽调 (Gate A)</div>
      <p className="mt-1 text-xs text-violet-800/90">
        短名单确认阶段：拉取 Nox 达人档案、受众与内容数据，并给出是否优先合作的建议。
      </p>

      <NoxQuotaBanner
        campaignId={campaignId}
        env={env}
        stats={noxStats}
        identityId={identityId}
        className="mt-3"
      />

      {verdictUi ? (
        <div className="mt-3 space-y-3">
          <div
            className={`rounded-lg border px-3 py-2.5 ${verdictUi.tone}`}
            title={verdictUi.hint}
          >
            <div className="text-[10px] font-medium uppercase tracking-wide opacity-80">
              尽调结论
            </div>
            <div className="mt-0.5 text-base font-semibold">{verdictUi.label}</div>
            <p className="mt-1 text-xs leading-snug opacity-90">{verdictUi.hint}</p>
          </div>

          {(metrics.engagementRate
            || metrics.avgViews
            || metrics.region
            || metrics.audienceAuthenticity) && (
            <dl className="grid gap-2 text-xs sm:grid-cols-2 lg:grid-cols-4">
              {metrics.engagementRate && (
                <div className="rounded-lg border border-violet-100 bg-white/80 px-3 py-2">
                  <dt className="font-medium text-violet-700">互动率</dt>
                  <dd className="mt-0.5 text-slate-900">{metrics.engagementRate}</dd>
                </div>
              )}
              {metrics.avgViews && (
                <div className="rounded-lg border border-violet-100 bg-white/80 px-3 py-2">
                  <dt className="font-medium text-violet-700">平均播放</dt>
                  <dd className="mt-0.5 text-slate-900">{metrics.avgViews}</dd>
                </div>
              )}
              {metrics.region && (
                <div className="rounded-lg border border-violet-100 bg-white/80 px-3 py-2">
                  <dt className="font-medium text-violet-700">主要受众地区</dt>
                  <dd className="mt-0.5 text-slate-900">{metrics.region}</dd>
                </div>
              )}
              {metrics.audienceAuthenticity && (
                <div className="rounded-lg border border-violet-100 bg-white/80 px-3 py-2">
                  <dt className="font-medium text-violet-700">受众真实度</dt>
                  <dd className="mt-0.5 text-slate-900">{metrics.audienceAuthenticity}</dd>
                </div>
              )}
            </dl>
          )}

          <dl className="grid gap-2 text-xs sm:grid-cols-2">
            {typeof cacheMonth === 'string' && cacheMonth && (
              <div className="rounded-lg border border-violet-100 bg-white/80 px-3 py-2">
                <dt className="font-medium text-violet-700">数据所属月份</dt>
                <dd className="mt-0.5 text-slate-900">{cacheMonth}</dd>
                <dd className="mt-0.5 text-[10px] text-slate-500">
                  同月内重复尽调可命中缓存，通常不再扣 API
                </dd>
              </div>
            )}
            {creatorIdStr && (
              <div className="rounded-lg border border-violet-100 bg-white/80 px-3 py-2">
                <dt className="font-medium text-violet-700">Nox 达人 ID</dt>
                <dd className="mt-0.5 font-mono text-[11px] text-slate-800" title={creatorIdStr}>
                  {truncateNoxId(creatorIdStr, 14, 8)}
                </dd>
              </div>
            )}
            {typeof diligenceAt === 'string' && diligenceAt && (
              <div className="rounded-lg border border-violet-100 bg-white/80 px-3 py-2">
                <dt className="font-medium text-violet-700">最近尽调时间</dt>
                <dd className="mt-0.5 text-slate-900">
                  <TimeAgo iso={diligenceAt} />
                </dd>
              </div>
            )}
          </dl>

          {parsedKey && (
            <details className="rounded-lg border border-violet-100 bg-white/60 px-3 py-2 text-xs">
              <summary className="cursor-pointer font-medium text-violet-800">
                本月缓存说明（技术细节）
              </summary>
              <ul className="mt-2 space-y-1.5 text-slate-700">
                <li>
                  <span className="text-slate-500">报告类型：</span>
                  {parsedKey.kindLabel}
                </li>
                {parsedKey.dimensionLabels.length > 0 && (
                  <li>
                    <span className="text-slate-500">分析维度：</span>
                    {parsedKey.dimensionLabels.join('、')}
                  </li>
                )}
                {parsedKey.langLabel && (
                  <li>
                    <span className="text-slate-500">报告语言：</span>
                    {parsedKey.langLabel}
                  </li>
                )}
                <li className="text-[10px] leading-snug text-slate-500">
                  系统用下方「缓存键」判断本月是否已拉过相同数据；键相同则直接读缓存。
                </li>
                {typeof cacheKey === 'string' && (
                  <li className="break-all font-mono text-[10px] text-slate-400">
                    {cacheKey}
                  </li>
                )}
              </ul>
            </details>
          )}
        </div>
      ) : (
        <p className="mt-3 rounded-lg border border-dashed border-violet-200 bg-white/50 px-3 py-2 text-xs text-violet-800">
          尚未运行 Nox 短名单尽调。点击下方按钮后，约 30–60 秒内会写回结论与缓存信息。
        </p>
      )}

      <button
        type="button"
        disabled={busy || quotaBlocked}
        onClick={() => void runDiligence()}
        className="mt-3 rounded-lg bg-violet-700 px-4 py-2 text-xs font-medium text-white shadow-sm hover:bg-violet-800 disabled:opacity-50"
      >
        {busy ? '尽调中…' : verdictUi ? '重新尽调 (Nox)' : '确认尽调 (Nox)'}
      </button>
      {statsHint && <p className="mt-2 text-[11px] text-violet-600">{statsHint}</p>}
      {err && <p className="mt-2 text-xs text-red-600">{err}</p>}
    </div>
  );
}
