import { useGatewayApprovals } from '../../hooks/useGatewayApprovals';

/**
 * Persistent nav affordance for gateway dangerous-command approvals.
 * The floating dock auto-opens on new requests; this badge ensures
 * operators who collapsed the dock can find pending items again.
 */
export function GatewayApprovalNavBadge() {
  const { count, setOpen } = useGatewayApprovals();
  if (count <= 0) return null;

  return (
    <button
      type="button"
      onClick={() => setOpen(true)}
      className="relative rounded border border-amber-400 bg-amber-50 px-2 py-1 text-xs font-medium text-amber-900 hover:bg-amber-100"
      title="Agent 需要您批准一条终端命令才能继续"
    >
      <span className="mr-1 inline-block h-1.5 w-1.5 rounded-full bg-amber-500 animate-pulse" />
      命令待批
      <span className="ml-1 rounded bg-amber-200/80 px-1 font-semibold">{count}</span>
    </button>
  );
}
