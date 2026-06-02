import ApprovalActionBar from './ApprovalActionBar';
import ApprovalContextCard from './ApprovalContextCard';
import DraftEditDiffPanel from './DraftEditDiffPanel';

type EditLearning = {
  was_edited?: boolean;
  edit_distance?: number;
  normalized_agent_body?: string;
  normalized_sent_body?: string;
};

/**
 * Combined approval detail: context preview + optional sent-body diff +
 * approve/reject actions (structured reject for reply drafts).
 */
export default function ApprovalDetailPanel({
  factPath,
  context,
  identityId,
  campaignId,
  env,
  decidedBy,
  agentBody,
  editLearning,
  onRejected,
  onApproved,
  showActions = true,
  approveButtonLabel,
  rejectButtonLabel,
}: {
  factPath: string;
  context: Record<string, unknown> | null;
  identityId: number;
  campaignId: string;
  env: string;
  decidedBy: string;
  agentBody?: string;
  editLearning?: EditLearning | null;
  onRejected?: () => void;
  onApproved?: () => void;
  showActions?: boolean;
  approveButtonLabel?: string;
  rejectButtonLabel?: string;
}) {
  const draftBody =
    agentBody
    ?? (typeof context?.draft === 'object' && context?.draft !== null
      ? String((context.draft as Record<string, unknown>).body ?? '')
      : '');

  return (
    <div className="space-y-3">
      <ApprovalContextCard
        factPath={factPath}
        context={context}
        identityId={identityId}
        campaignId={campaignId}
        env={env}
      />
      {factPath === 'approval.reply_draft' && editLearning && (
        <DraftEditDiffPanel agentBody={draftBody} editLearning={editLearning} />
      )}
      {showActions && (
        <ApprovalActionBar
          factPath={factPath}
          identityId={identityId}
          campaignId={campaignId}
          env={env}
          decidedBy={decidedBy}
          agentBody={draftBody}
          onRejected={onRejected}
          onApproved={onApproved}
          approveButtonLabel={approveButtonLabel}
          rejectButtonLabel={rejectButtonLabel}
        />
      )}
    </div>
  );
}
