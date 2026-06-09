import { useEffect, useState } from 'react';
import { api, type CampaignListItem } from '../../api';
import { errorSummary } from '../../lib/errors';
import { toast } from '../../lib/store';

type Props = {
  open: boolean;
  identityId: number;
  handle: string;
  fromCampaignId: string;
  env: 'TEST' | 'LIVE';
  onClose: () => void;
  onTransferred?: (toCampaignId: string) => void;
};

export function KolTransferCampaignDialog({
  open,
  identityId,
  handle,
  fromCampaignId,
  env,
  onClose,
  onTransferred,
}: Props) {
  const [campaigns, setCampaigns] = useState<CampaignListItem[]>([]);
  const [loadingCampaigns, setLoadingCampaigns] = useState(false);
  const [toCampaignId, setToCampaignId] = useState('');
  const [reason, setReason] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [loadErr, setLoadErr] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setToCampaignId('');
    setReason('');
    setSubmitting(false);
    setLoadErr(null);
    setLoadingCampaigns(true);
    api
      .get<{ items: CampaignListItem[] }>('/campaigns')
      .then((r) => setCampaigns(r.items ?? []))
      .catch((ex) => setLoadErr(errorSummary(ex)))
      .finally(() => setLoadingCampaigns(false));
  }, [open, fromCampaignId, env]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !submitting) onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, submitting, onClose]);

  if (!open) return null;

  const options = campaigns.filter(
    (c) => c.env === env && c.campaign_id !== fromCampaignId,
  );

  const submit = async () => {
    if (!toCampaignId || submitting) return;
    if (!reason.trim()) {
      toast.error('请填写转移原因', '便于团队了解为何换到其他产品活动');
      return;
    }
    setSubmitting(true);
    try {
      await api.post(`/identities/${identityId}/transfer-campaign`, {
        from_campaign_id: fromCampaignId,
        to_campaign_id: toCampaignId,
        env,
        source_stage: 'shortlist',
        reason: reason.trim(),
      });
      toast.success('已转到其他活动', `@${handle} → ${toCampaignId}`);
      onTransferred?.(toCampaignId);
      onClose();
    } catch (ex) {
      toast.error('转移失败', errorSummary(ex));
      setSubmitting(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="transfer-campaign-title"
    >
      <div className="w-full max-w-md rounded-lg border border-slate-200 bg-white p-4 shadow-lg">
        <h2 id="transfer-campaign-title" className="text-sm font-semibold text-slate-900">
          转到其他活动
        </h2>
        <p className="mt-1 text-xs text-slate-600">
          将 <span className="font-medium">@{handle}</span> 从当前活动移到另一产品的活动 shortlist。
          尚未批准外联的候选适用此操作。
        </p>

        <div className="mt-3 space-y-3 text-xs">
          <div>
            <div className="mb-1 text-slate-500">当前活动</div>
            <div className="rounded border border-slate-200 bg-slate-50 px-2 py-1.5 font-mono text-slate-800">
              {fromCampaignId}
            </div>
          </div>

          <div>
            <label className="mb-1 block text-slate-600" htmlFor="transfer-target-campaign">
              转到哪个活动
            </label>
            {loadingCampaigns ? (
              <div className="text-slate-400">加载活动列表…</div>
            ) : loadErr ? (
              <div className="text-rose-700">{loadErr}</div>
            ) : options.length === 0 ? (
              <div className="rounded border border-amber-200 bg-amber-50 px-2 py-1.5 text-amber-900">
                {env} 下没有其他可选活动。请先在目标产品页启动活动。
              </div>
            ) : (
              <select
                id="transfer-target-campaign"
                value={toCampaignId}
                onChange={(e) => setToCampaignId(e.target.value)}
                className="w-full rounded border border-slate-300 bg-white px-2 py-1.5"
              >
                <option value="">— 选择目标活动 —</option>
                {options.map((c) => (
                  <option key={`${c.campaign_id}|${c.env}`} value={c.campaign_id}>
                    {c.campaign_id}
                    {c.label ? ` · ${c.label}` : ''}
                    {typeof c.candidate_count === 'number' ? ` · ${c.candidate_count} kol` : ''}
                  </option>
                ))}
              </select>
            )}
          </div>

          <div>
            <label className="mb-1 block text-slate-600" htmlFor="transfer-reason">
              原因（必填）
            </label>
            <textarea
              id="transfer-reason"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={3}
              maxLength={500}
              placeholder="例如：该 KOL 内容风格更适合另一款产品"
              className="w-full rounded border border-slate-300 px-2 py-1.5"
            />
          </div>
        </div>

        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            disabled={submitting}
            onClick={onClose}
            className="rounded border border-slate-300 bg-white px-3 py-1.5 text-xs text-slate-700 hover:bg-slate-50 disabled:opacity-50"
          >
            取消
          </button>
          <button
            type="button"
            disabled={submitting || !toCampaignId || options.length === 0}
            onClick={() => void submit()}
            className="rounded bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
          >
            {submitting ? '转移中…' : '确认转移'}
          </button>
        </div>
      </div>
    </div>
  );
}
