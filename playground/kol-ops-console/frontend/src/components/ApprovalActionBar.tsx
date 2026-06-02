import { useState } from 'react';
import { api } from '../api';
import { toast } from '../lib/store';
import { errorSummary } from '../lib/errors';
import { dialog } from './dialogs/useDialog';
import DraftEditDiffPanel from './DraftEditDiffPanel';
import RejectCorrectionModal, { type RejectCorrectionModalProps } from './RejectCorrectionModal';
import type { RejectCorrection } from '../constants/rejectTags';

export type ApprovalDecisionRequest = {
  identity_id: number;
  campaign_id: string;
  env: string;
  decided_by: string;
  note?: string;
  correction?: RejectCorrection;
};

type Props = {
  factPath: string;
  identityId: number;
  campaignId: string;
  env: string;
  decidedBy: string;
  agentBody?: string;
  onRejected?: () => void;
  onApproved?: () => void;
  rejectButtonLabel?: string;
  approveButtonLabel?: string;
};

/**
 * Approve / reject action bar for pending approvals.
 * For ``approval.reply_draft``, reject opens structured correction modal.
 */
export default function ApprovalActionBar({
  factPath,
  identityId,
  campaignId,
  env,
  decidedBy,
  agentBody,
  onRejected,
  onApproved,
  rejectButtonLabel = '驳回',
  approveButtonLabel = '批准',
}: Props) {
  const [rejectOpen, setRejectOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [lastEdit, setLastEdit] = useState<Record<string, unknown> | null>(null);
  const isReplyDraft = factPath === 'approval.reply_draft';

  const postDecision = async (
    decision: 'approve' | 'reject',
    correction?: RejectCorrection,
  ) => {
    if (decision === 'approve' && isReplyDraft) {
      const ok = await dialog.confirm({
        title: '批准并创建 Gmail 草稿？',
        description:
          '影响：1) 立即创建 Gmail draft；2) 不会自动发送；3) campaign 将继续推进到下一步。',
        confirmLabel: '批准',
        cancelLabel: '取消',
        variant: 'info',
        liveWarning: env === 'LIVE',
      });
      if (!ok) return;
    }
    setLoading(true);
    try {
      const body: ApprovalDecisionRequest = {
        identity_id: identityId,
        campaign_id: campaignId,
        env,
        decided_by: decidedBy,
      };
      if (decision === 'reject' && isReplyDraft && correction) {
        body.correction = correction;
        if (correction.note) body.note = correction.note;
      }
      const path = `/approvals/${encodeURIComponent(factPath)}/${decision === 'approve' ? 'approve' : 'reject'}`;
      await api.post(path, body);
      toast.success(decision === 'approve' ? '已批准' : '已驳回');
      if (decision === 'reject') {
        onRejected?.();
      } else {
        onApproved?.();
      }
    } catch (ex) {
      toast.error('提交失败', errorSummary(ex));
      throw ex;
    } finally {
      setLoading(false);
      setRejectOpen(false);
    }
  };

  const loadLatestEdit = async () => {
    if (!isReplyDraft) return;
    try {
      const params = new URLSearchParams({
        env,
        identity_id: String(identityId),
        campaign_id: campaignId,
        limit: '1',
      });
      const res = await api.get<{ events: Array<{ payload?: Record<string, unknown> }> }>(
        `/learning/edit-events?${params}`,
      );
      setLastEdit(res.events?.[0]?.payload ?? null);
    } catch {
      setLastEdit(null);
    }
  };

  return (
    <div className="space-y-2">
      {isReplyDraft && lastEdit && (
        <DraftEditDiffPanel agentBody={agentBody} editLearning={lastEdit} />
      )}

      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          disabled={loading}
          onClick={() => {
            void postDecision('approve').catch(() => undefined);
          }}
          className="rounded bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
        >
          {approveButtonLabel}
        </button>
        <button
          type="button"
          disabled={loading}
          onClick={() => {
            if (isReplyDraft) {
              void loadLatestEdit();
              setRejectOpen(true);
            } else {
              void postDecision('reject');
            }
          }}
          className="rounded border border-rose-300 bg-white px-3 py-1.5 text-xs font-medium text-rose-700 hover:bg-rose-50 disabled:opacity-50"
        >
          {rejectButtonLabel}
        </button>
      </div>

      <RejectCorrectionModal
        open={rejectOpen}
        loading={loading}
        onClose={() => setRejectOpen(false)}
        onSubmit={(correction) => postDecision('reject', correction).catch(() => undefined)}
      />
    </div>
  );
}

export type { RejectCorrectionModalProps };
