import ApprovalActionBar from './ApprovalActionBar';
import ApprovalContextCard from './ApprovalContextCard';

/**
 * Combined approval detail: context preview + approve/reject actions
 * (structured reject for reply drafts). Sent-body diff belongs on history rows only.
 */
export default function ApprovalDetailPanel({
  factPath,
  context,
  identityId,
  campaignId,
  env,
  decidedBy,
  agentBody,
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
