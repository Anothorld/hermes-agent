import { useEffect, useState } from 'react';
import { api, type CampaignListItem } from '../../api';
import { errorSummary } from '../../lib/errors';
import { toast } from '../../lib/store';
import {
  DecisionTagChecklist,
  useDecisionTags,
  useFeedbackRequirements,
} from './ShortlistDecisionFeedbackDialog';

type Props = {
  open: boolean;
  identityId: number;
  handle: string;
  fromCampaignId: string;
  sku?: string | null;
  env: 'TEST' | 'LIVE';
  onClose: () => void;
  onTransferred?: (toCampaignId: string) => void;
};

export function KolTransferCampaignDialog({
  open,
  identityId,
  handle,
  fromCampaignId,
  sku,
  env,
  onClose,
  onTransferred,
}: Props) {
  const [campaigns, setCampaigns] = useState<CampaignListItem[]>([]);
  const [loadingCampaigns, setLoadingCampaigns] = useState(false);
  const [toCampaignId, setToCampaignId] = useState('');
  const [reason, setReason] = useState('');
  const [reasonTags, setReasonTags] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const { tags: decisionTags } = useDecisionTags('transfer', open);
  const req = useFeedbackRequirements(sku, env, open);
  // Same early-learning policy as approve/remove: the comment (reason) is
  // mandatory only while the SPU is below the sample threshold; tags follow
  // the global kill switch.
  const feedbackRequired = !req?.degraded && req?.feedback_required !== false;
  const commentRequired = feedbackRequired && req?.comment_required !== false;
  const sampleCount = req?.sku_sample_count ?? 0;
  const sampleThreshold = req?.comment_required_threshold ?? 0;

  useEffect(() => {
    if (!open) return;
    setToCampaignId('');
    setReason('');
    setReasonTags([]);
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
    if (feedbackRequired && reasonTags.length === 0) {
      toast.error('请至少勾选一个原因标签', '标签会成为 AI 学习样本，帮助下次发现更准');
      return;
    }
    if (commentRequired && !reason.trim()) {
      toast.error('请填写转移原因', '学习初期需要您用一句话说明真实理由');
      return;
    }
    setSubmitting(true);
    try {
      const r = await api.post<{ learning?: { recorded?: number; error?: string } }>(
        `/identities/${identityId}/transfer-campaign`,
        {
          from_campaign_id: fromCampaignId,
          to_campaign_id: toCampaignId,
          env,
          source_stage: 'shortlist',
          reason: reason.trim(),
          reason_tags: reasonTags,
        },
      );
      toast.success('已转到其他活动', `@${handle} → ${toCampaignId}`);
      if (r.learning?.error) {
        toast.error(
          '转移成功，但学习样本未能记录',
          '系统已留底备查，无需重试转移；学习服务恢复后可补录。',
        );
      }
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

          {commentRequired && sampleThreshold > 0 && (
            <div className="rounded border border-sky-200 bg-sky-50 px-2 py-1 text-[11px] text-sky-900">
              学习初期需要您说明真实理由（该产品已积累 {sampleCount}/{sampleThreshold} 条样本，达到后原因改为选填）
            </div>
          )}

          <div>
            <div className="mb-1 text-slate-600">
              原因标签{feedbackRequired ? '（必选）' : '（选填）'}{' '}
              <span className="text-slate-400">— 会成为 AI 学习样本</span>
            </div>
            <DecisionTagChecklist
              tags={decisionTags}
              selected={reasonTags}
              idPrefix="transfer"
              onToggle={(tag, checked) =>
                setReasonTags((prev) =>
                  checked ? [...prev, tag] : prev.filter((t) => t !== tag),
                )
              }
            />
          </div>

          <div>
            <label className="mb-1 block text-slate-600" htmlFor="transfer-reason">
              原因{commentRequired ? '（必填）' : '（选填，但越多越好）'}
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
