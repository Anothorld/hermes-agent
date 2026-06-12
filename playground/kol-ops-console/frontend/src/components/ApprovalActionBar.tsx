import { useState } from 'react';
import { api } from '../api';
import { toast } from '../lib/store';
import { errorSummary } from '../lib/errors';
import { dialog } from './dialogs/useDialog';
import RejectCorrectionModal, { type RejectCorrectionModalProps } from './RejectCorrectionModal';
import type { RejectCorrection } from '../constants/rejectTags';

export type ApprovalDecisionResult = {
  ok?: boolean;
  decision?: string;
  gmail_draft?: {
    draft_id?: string;
    thread_id?: string;
    message_id?: string;
  } | null;
  handled_escalation_id?: number | null;
  linked_escalation_id?: number | null;
};

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
  onApproved?: (result?: ApprovalDecisionResult) => void;
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
  onRejected,
  onApproved,
  rejectButtonLabel = '驳回',
  approveButtonLabel = '批准',
}: Props) {
  const [rejectOpen, setRejectOpen] = useState(false);
  const [loading, setLoading] = useState(false);
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
      const result = await api.post<ApprovalDecisionResult>(path, body);
      if (decision === 'approve') {
        toast.success('已批准');
      } else {
        toast.success('已驳回');
      }
      if (decision === 'reject') {
        onRejected?.();
      } else {
        onApproved?.(result);
      }
    } catch (ex) {
      toast.error('提交失败', errorSummary(ex));
      throw ex;
    } finally {
      setLoading(false);
      setRejectOpen(false);
    }
  };

  return (
    <div className="space-y-2">
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
